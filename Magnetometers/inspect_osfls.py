import os
import sys
import struct
import numpy as np
import pandas as pd

def inspect_osfls(file_path):
    print(f"==================================================")
    print(f"Inspecting File: {file_path}")
    print(f"==================================================")
    
    if not os.path.exists(file_path):
        print("ERROR: File path does not exist!")
        return

    file_size = os.path.getsize(file_path)
    print(f"File Size on Disk: {file_size} bytes")

    with open(file_path, 'rb') as f:
        # Unpack 49 packed header bytes using explicit little-endian format '<idiBQQQQ'
        header_bytes = f.read(49)
        if len(header_bytes) < 49:
            print("ERROR: File header truncated (less than 49 bytes).")
            return

        version, trigger_time, model, is_morphable, n_lines, n_points, n_extras, byte_size_names = \
            struct.unpack('<idiBQQQQ', header_bytes)

        dt_utc = pd.Timestamp("2000-01-01T12:00:00Z") + pd.Timedelta(seconds=trigger_time)

        print(f"Header Version:      {version}")
        print(f"J2000 Trigger Time:  {trigger_time:.2f} s  -->  UTC: {dt_utc}")
        print(f"Number of Lines:     {n_lines}")
        print(f"Number of Vertices:  {n_points}")
        print(f"Number of Extras:    {n_extras}")
        print(f"Scalar Name Bytes:   {byte_size_names}")

        if n_lines == 0 or n_points == 0:
            print("CRITICAL WARNING: Binary contains 0 lines or 0 points! OpenSpace will not render this frame.")
            return

        # Read array data
        line_starts = np.fromfile(f, dtype=np.int32, count=n_lines)
        line_counts = np.fromfile(f, dtype=np.uint32, count=n_lines)
        positions = np.fromfile(f, dtype=np.float32, count=n_points * 3).reshape(n_points, 3)
        extras = np.fromfile(f, dtype=np.float32, count=n_points * n_extras)
        scalar_name = f.read(byte_size_names).decode('utf-8', errors='ignore').strip('\x00')

        # Compute spatial metrics
        x, y, z = positions[:, 0], positions[:, 1], positions[:, 2]
        r = np.sqrt(x**2 + y**2 + z**2)
        earth_r_m = 6371000.0

        print(f"\n--- Fieldline Metrics ---")
        print(f"Scalar Quantity Name: '{scalar_name}'")
        print(f"Point Count per Line: Min={line_counts.min()}, Max={line_counts.max()}, Mean={line_counts.mean():.1f}")
        print(f"Radial Distance (m):  Min={r.min():.1f} m, Max={r.max():.1f} m (Earth Surface = {earth_r_m:.1f} m)")
        print(f"Scalar Values (nT):   Min={extras.min():.2f}, Max={extras.max():.2f}, Mean={extras.mean():.2f}")
        print(f"NaN Check:            Positions NaN={np.isnan(positions).sum()}, Extras NaN={np.isnan(extras).sum()}")

        # Red Flag Diagnostics
        print(f"\n--- OpenSpace Compatibility Check ---")
        if r.max() < 1000000:
            print("[RED FLAG] Coordinates appear to be in kilometers rather than meters! OpenSpace expects meters ($R_E \\approx 6.371 \\times 10^6$ m). Lines are trapped inside Earth's core.")
        elif r.min() <= earth_r_m:
            print("[WARNING] Min radius is <= Earth surface radius. Fieldlines may be obscured under the Earth globe mesh.")
        else:
            print("[OK] Radial coordinates match planetary meter scales.")

        if extras.max() == 0.0:
            print("[RED FLAG] Scalar array is all zeros! If ColorTableRanges starts > 0, fieldlines will render transparent.")
        else:
            print(f"[OK] Scalar values exist (peak = {extras.max():.1f}). Match your asset's ColorTableRanges to this magnitude.")

if __name__ == "__main__":
    target_file = sys.argv[1] if len(sys.argv) > 1 else "osfls_geomag/2024-05-11T00-00-00-000.osfls"
    inspect_osfls(target_file)