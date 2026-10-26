import os
import sys
import time
import argparse
import struct
import numpy as np
import xarray as xr
import pandas as pd

DEFAULT_NETCDF = "gannon_storm_20240510.nc"
R_EARTH = 6371000.0          # Earth radius in meters
ALTITUDE_BASE_M = 20000.0    # Anchor elevation (20 km) above globe
LENGTH_SCALE_M_PER_NT = 300.0 # Vector length scale: 1 nT = 300 meters (e.g., 500 nT = 150 km line)
J2000_EPOCH = pd.Timestamp("2000-01-01T12:00:00Z")

def log(msg):
    print(msg, flush=True)

def to_j2000_seconds(dt_val):
    ts = pd.to_datetime(dt_val)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return (ts - J2000_EPOCH).total_seconds()

def get_station_cartesian_basis(lat_deg, lon_deg, alt_m=ALTITUDE_BASE_M):
    """
    Computes local cartesian position and orthonormal local basis vectors:
    - Up/Radial (R_hat)
    - East (E_hat)
    - North (N_hat)
    """
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    
    # Station position at base altitude
    r = R_EARTH + alt_m
    pos_x = r * np.cos(lat) * np.cos(lon)
    pos_y = r * np.cos(lat) * np.sin(lon)
    pos_z = r * np.sin(lat)
    pos = np.array([pos_x, pos_y, pos_z], dtype=np.float64)

    # Unit vectors in Cartesian space
    r_hat = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], dtype=np.float64)
    e_hat = np.array([-np.sin(lon), np.cos(lon), 0.0], dtype=np.float64)
    n_hat = np.array([-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)], dtype=np.float64)

    return pos, e_hat, n_hat, r_hat

def export_to_osfls(osfls_path, fieldlines, trigger_time_j2000, scalar_name="b_magnitude_nT"):
    n_lines = len(fieldlines)
    extra_names_bytes = scalar_name.encode('utf-8') + b'\0'
    byte_size_all_names = len(extra_names_bytes)

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
        ofs.write(struct.pack('<Q', byte_size_all_names))

        np.array(line_start, dtype=np.int32).tofile(ofs)
        np.array(line_count, dtype=np.uint32).tofile(ofs)
        np.array(vertex_positions, dtype=np.float32).tofile(ofs)
        np.array(extra_quantities, dtype=np.float32).tofile(ofs)
        ofs.write(extra_names_bytes)

def process_station_vectors(file_path):
    script_start_time = time.time()
    log(f"[1/4] Opening NetCDF file: {file_path}")
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input NetCDF file not found at: {os.path.abspath(file_path)}")
        
    ds = xr.open_dataset(file_path)
    b_north_var = next((v for v in ['B_N', 'dBn', 'bn', 'X', 'B_north'] if v in ds), None)
    b_east_var = next((v for v in ['B_E', 'dBe', 'be', 'Y', 'B_east'] if v in ds), None)
    time_var = next((t for t in ['time', 'Date', 'Timestamp'] if t in ds.coords or t in ds.data_vars), 'time')

    time_steps = ds[time_var].values
    total_frames = len(time_steps)
    
    station_lats = ds['lat'].values
    station_lons = ds['lon'].values
    num_stations = len(station_lats)
    
    log(f"[2/4] Found {num_stations} stations and {total_frames} time frames.")

    # Pre-compute local coordinate basis for each station
    station_bases = [get_station_cartesian_basis(station_lats[i], station_lons[i]) for i in range(num_stations)]

    log(f"[3/4] Generating station 3D vector needles across all frames...")

    for t_idx in range(total_frames):
        dt_val = time_steps[t_idx]
        trigger_time_j2000 = to_j2000_seconds(dt_val)
        time_str = pd.to_datetime(dt_val).strftime("%Y-%m-%dT%H-%M-%S-000")
        filename_osfls = f"osfls_geomag/{time_str}.osfls"

        bn_vals = ds[b_north_var].isel({time_var: t_idx}).values
        be_vals = ds[b_east_var].isel({time_var: t_idx}).values

        fieldlines = []

        for stn_idx in range(num_stations):
            bn = bn_vals[stn_idx]
            be = be_vals[stn_idx]
            b_mag = float(np.sqrt(bn**2 + be**2))

            pos_base, e_hat, n_hat, r_hat = station_bases[stn_idx]

            # 1. Vertical Anchor Line (ground to base altitude)
            pos_ground = pos_base - (r_hat * ALTITUDE_BASE_M)
            ground_pin = []
            for t in np.linspace(0.0, 1.0, 5):
                pt = pos_ground + t * (pos_base - pos_ground)
                ground_pin.append([pt[0], pt[1], pt[2], 0.0])  # Scalar 0 for anchor pin
            fieldlines.append(ground_pin)

            # 2. Main Perturbation Vector Needle
            if b_mag > 1e-3:
                u_dir = (be / b_mag) * e_hat + (bn / b_mag) * n_hat
                vec_length_m = b_mag * LENGTH_SCALE_M_PER_NT
                
                needle_pts = []
                for t in np.linspace(0.0, 1.0, 10):
                    pt = pos_base + (t * vec_length_m * u_dir)
                    needle_pts.append([pt[0], pt[1], pt[2], b_mag])
                fieldlines.append(needle_pts)

                # 3. Arrowhead Fin 1 & Fin 2 for clear directional pointing
                tip_pos = pos_base + (vec_length_m * u_dir)
                fin_length = vec_length_m * 0.25
                
                # Fin directions orthogonal to main vector needle
                fin1_dir = -u_dir + 0.3 * r_hat
                fin2_dir = -u_dir - 0.3 * r_hat
                fin1_dir /= np.linalg.norm(fin1_dir)
                fin2_dir /= np.linalg.norm(fin2_dir)

                fin1_pts = [[tip_pos[0], tip_pos[1], tip_pos[2], b_mag],
                            [(tip_pos + fin_length * fin1_dir)[0], (tip_pos + fin_length * fin1_dir)[1], (tip_pos + fin_length * fin1_dir)[2], b_mag]]
                fin2_pts = [[tip_pos[0], tip_pos[1], tip_pos[2], b_mag],
                            [(tip_pos + fin_length * fin2_dir)[0], (tip_pos + fin_length * fin2_dir)[1], (tip_pos + fin_length * fin2_dir)[2], b_mag]]
                
                fieldlines.append(fin1_pts)
                fieldlines.append(fin2_pts)

        if fieldlines:
            export_to_osfls(filename_osfls, fieldlines, trigger_time_j2000, scalar_name="b_magnitude_nT")

        if (t_idx + 1) % 100 == 0 or t_idx == 0 or t_idx == total_frames - 1:
            log(f"      Frame [{t_idx + 1:4d}/{total_frames:4d}] ({((t_idx+1)/total_frames)*100:5.1f}%) -> Saved {filename_osfls}")

    ds.close()
    elapsed = time.time() - script_start_time
    log(f"\n[4/4] Successfully generated station vector binary sequence in {elapsed:.1f} seconds.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert station magnetometer data to 3D station vector arrows for OpenSpace.")
    parser.add_argument("--input", type=str, default=DEFAULT_NETCDF, help="Input NetCDF file path.")
    args = parser.parse_args()

    process_station_vectors(args.input)
