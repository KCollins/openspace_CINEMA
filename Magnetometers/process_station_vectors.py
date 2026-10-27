import os
import sys
import glob
import shutil
import argparse
import struct
import numpy as np
import xarray as xr
import pandas as pd

DEFAULT_NETCDF = "eziemag_data.nc"
DEFAULT_OUTDIR = "osfls_eziemag"
R_EARTH = 6371000.0
J2000_EPOCH = pd.Timestamp("2000-01-01T12:00:00Z")

def to_j2000_seconds(dt_val):
    ts = pd.to_datetime(dt_val)
    if ts.tzinfo is None: ts = ts.tz_localize("UTC")
    return (ts - J2000_EPOCH).total_seconds()

def get_station_cartesian_basis(lat_deg, lon_deg, alt_m):
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

def process_station_vectors(file_path, out_dir=DEFAULT_OUTDIR, eic_mode=False, scale=400.0, force=False):
    if os.path.exists(out_dir) and len(glob.glob(os.path.join(out_dir, "*.osfls"))) > 0 and not force:
        print(f"[SKIP] OSFLS directory '{out_dir}/' already exists. Use --force to overwrite.")
        return

    if force and os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    altitude_m = 100000.0 if eic_mode else 20000.0
    mode_str = "Equivalent Ionospheric Currents (EIC, 100 km)" if eic_mode else "Ground Magnetic Perturbations (20 km)"
    
    print(f"Loading NetCDF: {file_path}")
    print(f"Render Mode: {mode_str}")
    print(f"Output Directory: {out_dir}/")

    ds = xr.open_dataset(file_path)
    dn_var = next(v for v in ['dB_N', 'B_N', 'dBn'] if v in ds)
    de_var = next(v for v in ['dB_E', 'B_E', 'dBe'] if v in ds)
    dz_var = next((v for v in ['dB_Z', 'B_Z', 'dBz'] if v in ds), None)
    time_var = next(t for t in ['time', 'Date'] if t in ds.coords or t in ds.data_vars)

    time_steps = ds[time_var].values
    station_lats, station_lons = ds['lat'].values, ds['lon'].values
    num_stations = len(station_lats)
    station_bases = [get_station_cartesian_basis(station_lats[i], station_lons[i], altitude_m) for i in range(num_stations)]

    for t_idx in range(len(time_steps)):
        dt_val = time_steps[t_idx]
        trigger_time_j2000 = to_j2000_seconds(dt_val)
        filename_osfls = pd.to_datetime(dt_val).strftime(f"{out_dir}/%Y-%m-%dT%H-%M-%S-000.osfls")

        dn_vals = ds[dn_var].isel({time_var: t_idx}).values
        de_vals = ds[de_var].isel({time_var: t_idx}).values
        dz_vals = ds[dz_var].isel({time_var: t_idx}).values if dz_var else np.zeros(num_stations)

        fieldlines = []

        for stn_idx in range(num_stations):
            dn, de, dz = dn_vals[stn_idx], de_vals[stn_idx], dz_vals[stn_idx]

            if np.isnan(dn) or np.isnan(de):
                continue

            dup = -dz if not np.isnan(dz) else 0.0
            pos_base, e_hat, n_hat, r_hat = station_bases[stn_idx]

            if eic_mode:
                j_east = dn
                j_north = -de
                b_mag = float(np.sqrt(j_east**2 + j_north**2))
                u_vec = (j_east * e_hat) + (j_north * n_hat)
            else:
                b_mag = float(np.sqrt(dn**2 + de**2 + dup**2))
                u_vec = (de * e_hat) + (dn * n_hat) + (dup * r_hat)

            # Ground Pin Stem (lifted +1,000 m above surface to prevent mesh clipping)
            pos_ground = pos_base - (r_hat * (altitude_m - 1000.0))
            ground_pin = []
            pin_scalar = max(b_mag, 5.0)
            for t in np.linspace(0.0, 1.0, 4):
                pt = pos_ground + t * (pos_base - pos_ground)
                ground_pin.append([pt[0], pt[1], pt[2], pin_scalar])
            fieldlines.append(ground_pin)

            # Vector Needle & Arrowheads
            if b_mag >= 1.0:
                u_dir = u_vec / b_mag
                vec_len = b_mag * scale

                needle_pts = []
                for t in np.linspace(0.0, 1.0, 8):
                    pt = pos_base + (t * vec_len * u_dir)
                    needle_pts.append([pt[0], pt[1], pt[2], b_mag])
                fieldlines.append(needle_pts)

                tip_pos = pos_base + (vec_len * u_dir)
                fin_len = vec_len * 0.2
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

        export_to_osfls(filename_osfls, fieldlines, trigger_time_j2000, scalar_name="b_magnitude_nT")

    ds.close()
    print(f"[DONE] Exported binary frame sequence to '{out_dir}/'")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default=DEFAULT_NETCDF)
    parser.add_argument("--outdir", type=str, default=DEFAULT_OUTDIR)
    parser.add_argument("--eic", action="store_true")
    parser.add_argument("--scale", type=float, default=400.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    process_station_vectors(args.input, out_dir=args.outdir, eic_mode=args.eic, scale=args.scale, force=args.force)