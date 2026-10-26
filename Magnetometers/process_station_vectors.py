import os
import sys
import time
import argparse
import struct
import numpy as np
import xarray as xr
import pandas as pd

DEFAULT_NETCDF = "gannon_storm_20240510.nc"
R_EARTH = 6371000.0
ALTITUDE_BASE_M = 20000.0       # Base altitude (20 km) above Earth
LENGTH_SCALE_M_PER_NT = 400.0   # 1 nT = 400m (1000 nT storm perturbation = 400 km vector length)
J2000_EPOCH = pd.Timestamp("2000-01-01T12:00:00Z")

def to_j2000_seconds(dt_val):
    ts = pd.to_datetime(dt_val)
    if ts.tzinfo is None: ts = ts.tz_localize("UTC")
    return (ts - J2000_EPOCH).total_seconds()

def get_station_cartesian_basis(lat_deg, lon_deg, alt_m=ALTITUDE_BASE_M):
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    r = R_EARTH + alt_m
    pos = np.array([r * np.cos(lat) * np.cos(lon), r * np.cos(lat) * np.sin(lon), r * np.sin(lat)])
    r_hat = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    e_hat = np.array([-np.sin(lon), np.cos(lon), 0.0])
    n_hat = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)])
    return pos, e_hat, n_hat, r_hat

def export_to_osfls(osfls_path, fieldlines, trigger_time_j2000, scalar_name="db_magnitude_nT"):
    n_lines = len(fieldlines)
    extra_names_bytes = scalar_name.encode('utf-8') + b'\0'
    line_start, line_count, vertex_positions, extra_quantities = [], [], [], []
    curr_index = 0

    for line in fieldlines:
        pts_count = len(line)
        line_start.append(curr_index)
        line_count.append(pts_count)
        curr_index += pts_count
        for pt in line:
            vertex_positions.extend(pt[:3])
            extra_quantities.append(pt[3])

    n_points = curr_index
    os.makedirs(os.path.dirname(osfls_path), exist_ok=True)
    with open(osfls_path, 'wb') as ofs:
        ofs.write(struct.pack('<i', 0))
        ofs.write(struct.pack('<d', trigger_time_j2000))
        ofs.write(struct.pack('<i', 0))
        ofs.write(struct.pack('<B', 0))
        ofs.write(struct.pack('<Q', n_lines))
        ofs.write(struct.pack('<Q', n_points))
        ofs.write(struct.pack('<Q', 1))
        ofs.write(struct.pack('<Q', len(extra_names_bytes)))

        np.array(line_start, dtype=np.int32).tofile(ofs)
        np.array(line_count, dtype=np.uint32).tofile(ofs)
        np.array(vertex_positions, dtype=np.float32).tofile(ofs)
        np.array(extra_quantities, dtype=np.float32).tofile(ofs)
        ofs.write(extra_names_bytes)

def process_station_vectors(file_path):
    print(f"Loading NetCDF: {file_path}")
    ds = xr.open_dataset(file_path)

    dn_var = next(v for v in ['dB_N', 'B_N', 'dBn'] if v in ds)
    de_var = next(v for v in ['dB_E', 'B_E', 'dBe'] if v in ds)
    dz_var = next((v for v in ['dB_Z', 'B_Z', 'dBz'] if v in ds), None)
    time_var = next(t for t in ['time', 'Date'] if t in ds.coords or t in ds.data_vars)

    time_steps = ds[time_var].values
    station_lats, station_lons = ds['lat'].values, ds['lon'].values
    num_stations = len(station_lats)
    station_bases = [get_station_cartesian_basis(station_lats[i], station_lons[i]) for i in range(num_stations)]

    print(f"Processing 3D perturbation vectors for {num_stations} stations...")

    for t_idx in range(len(time_steps)):
        dt_val = time_steps[t_idx]
        trigger_time_j2000 = to_j2000_seconds(dt_val)
        filename_osfls = pd.to_datetime(dt_val).strftime("osfls_geomag/%Y-%m-%dT%H-%M-%S-000.osfls")

        dn_vals = ds[dn_var].isel({time_var: t_idx}).values
        de_vals = ds[de_var].isel({time_var: t_idx}).values
        dz_vals = ds[dz_var].isel({time_var: t_idx}).values if dz_var else np.zeros(num_stations)

        fieldlines = []

        for stn_idx in range(num_stations):
            dn = dn_vals[stn_idx]
            de = de_vals[stn_idx]
            dup = -dz_vals[stn_idx]  # Upward component = -dB_Z

            # Total 3D perturbation vector magnitude
            b_mag = float(np.sqrt(dn**2 + de**2 + dup**2))
            pos_base, e_hat, n_hat, r_hat = station_bases[stn_idx]

            # 1. Ground Anchor Pin (Gray vertical stem)
            pos_ground = pos_base - (r_hat * ALTITUDE_BASE_M)
            ground_pin = []
            for t in np.linspace(0.0, 1.0, 4):
                pt = pos_ground + t * (pos_base - pos_ground)
                ground_pin.append([pt[0], pt[1], pt[2], 0.0])
            fieldlines.append(ground_pin)

            # 2. 3D Perturbation Vector Needle
            if b_mag > 10.0:  # Render vectors above 10 nT noise floor
                u_vec = (de * e_hat) + (dn * n_hat) + (dup * r_hat)
                u_dir = u_vec / b_mag
                vec_len = b_mag * LENGTH_SCALE_M_PER_NT

                needle_pts = []
                for t in np.linspace(0.0, 1.0, 8):
                    pt = pos_base + (t * vec_len * u_dir)
                    needle_pts.append([pt[0], pt[1], pt[2], b_mag])
                fieldlines.append(needle_pts)

                # 3. Arrowhead Fins
                tip_pos = pos_base + (vec_len * u_dir)
                fin_len = vec_len * 0.2
                
                # Orthogonal fin directions
                perp1 = np.cross(u_dir, r_hat)
                if np.linalg.norm(perp1) < 1e-3: perp1 = np.cross(u_dir, e_hat)
                perp1 /= np.linalg.norm(perp1)
                
                fin1 = -u_dir + 0.4 * perp1
                fin2 = -u_dir - 0.4 * perp1
                fin1 /= np.linalg.norm(fin1)
                fin2 /= np.linalg.norm(fin2)

                fieldlines.append([[tip_pos[0], tip_pos[1], tip_pos[2], b_mag],
                                   [(tip_pos + fin_len * fin1)[0], (tip_pos + fin_len * fin1)[1], (tip_pos + fin_len * fin1)[2], b_mag]])
                fieldlines.append([[tip_pos[0], tip_pos[1], tip_pos[2], b_mag],
                                   [(tip_pos + fin_len * fin2)[0], (tip_pos + fin_len * fin2)[1], (tip_pos + fin_len * fin2)[2], b_mag]])

        if fieldlines:
            export_to_osfls(filename_osfls, fieldlines, trigger_time_j2000, scalar_name="db_magnitude_nT")

    ds.close()
    print("3D Vector generation complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default=DEFAULT_NETCDF)
    args = parser.parse_args()
    process_station_vectors(args.input)