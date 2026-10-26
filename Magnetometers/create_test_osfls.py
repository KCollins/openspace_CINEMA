import os
import struct
import numpy as np
import pandas as pd

OUTPUT_DIR = "osfls_geomag"
TEST_TIMESTAMP = "2024-05-11T03:00:00Z"
J2000_EPOCH = pd.Timestamp("2000-01-01T12:00:00Z")

R_EARTH = 6371000.0
ALTITUDE = 50000.0  # 50 km above Earth surface for high visibility

def to_j2000_seconds(dt_str):
    ts = pd.to_datetime(dt_str)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return (ts - J2000_EPOCH).total_seconds()

def latlon_to_cartesian(lat_deg, lon_deg, alt_m=ALTITUDE):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    r = R_EARTH + alt_m
    x = r * np.cos(lat) * np.cos(lon)
    y = r * np.cos(lat) * np.sin(lon)
    z = r * np.sin(lat)
    return x, y, z

def generate_test_binary():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    dt = pd.to_datetime(TEST_TIMESTAMP)
    trigger_time_j2000 = to_j2000_seconds(TEST_TIMESTAMP)
    filename = dt.strftime(f"{OUTPUT_DIR}/%Y-%m-%dT%H-%M-%S-000.osfls")

    print(f"Generating synthetic test OSFLS file: {filename}")
    print(f"Trigger Time J2000: {trigger_time_j2000} seconds")

    # Build a grid of vertical vector spikes over North America (Lat 30..60, Lon -120..-70)
    lats = np.linspace(30.0, 60.0, 30)
    lons = np.linspace(-120.0, -70.0, 30)

    fieldlines = []
    for lat in lats:
        for lon in lons:
            line_pts = []
            # Create a 10-node vertical spike extending upward from 50 km to 500 km altitude
            for alt_step in np.linspace(50000.0, 500000.0, 10):
                x, y, z = latlon_to_cartesian(lat, lon, alt_m=alt_step)
                scalar_val = 500.0  # High magnitude scalar (500 nT)
                line_pts.append([x, y, z, scalar_val])
            fieldlines.append(line_pts)

    n_lines = len(fieldlines)
    scalar_name = "b_magnitude_nT"
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

    with open(filename, 'wb') as ofs:
        ofs.write(struct.pack('i', 0))                   # Version
        ofs.write(struct.pack('d', trigger_time_j2000))  # J2000 time
        ofs.write(struct.pack('i', 0))                   # Model
        ofs.write(struct.pack('B', 0))                   # IsMorphable
        ofs.write(struct.pack('Q', n_lines))             # nLines
        ofs.write(struct.pack('Q', n_points))            # nPoints
        ofs.write(struct.pack('Q', 1))                   # nExtras
        ofs.write(struct.pack('Q', byte_size_all_names)) # Header byte length

        np.array(line_start, dtype=np.int32).tofile(ofs)
        np.array(line_count, dtype=np.uint32).tofile(ofs)
        np.array(vertex_positions, dtype=np.float32).tofile(ofs)
        np.array(extra_quantities, dtype=np.float32).tofile(ofs)
        ofs.write(extra_names_bytes)

    print(f"[SUCCESS] Wrote {n_lines} lines and {n_points} vertices to {filename}")
    print("Test in OpenSpace by setting clock to: 2024-05-11T03:00:00")

if __name__ == "__main__":
    generate_test_binary()
