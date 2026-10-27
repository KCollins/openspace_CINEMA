import os
import sys
import gzip
import zipfile
import argparse
import pandas as pd
import numpy as np

MIN_VALID_EPOCH = 1577836800.0  # 2020-01-01
MAX_VALID_EPOCH = 2051222400.0  # 2035-01-01

def readsmr(line):
    """Parses an SMR ASCII line using R.J. Barnes' specification."""
    rec = {}
    linesplit = line.split(' ', maxsplit=4)
    if len(linesplit) != 5:
        return None

    try:
        tval = np.float64(linesplit[1])
    except ValueError:
        return None

    line = linesplit[4]
    linesplit = line.split('"', maxsplit=2)
    if len(linesplit) != 3:
        return None

    stid = linesplit[1].strip()
    line = linesplit[2]
    linesplit = line.split(' ', maxsplit=19)
    if len(linesplit) != 20:
        return None

    try:
        lat = np.float32(linesplit[2])
        lon = np.float32(linesplit[3])
        bx = np.float32(linesplit[8])
        by = np.float32(linesplit[9])
        bz = np.float32(linesplit[10])
    except ValueError:
        return None

    return {
        'tval': tval,
        'stid': stid,
        'lat': lat,
        'lon': lon,
        'Bx': bx,
        'By': by,
        'Bz': bz
    }

def audit_zip_archive(zip_path, start_time=None, end_time=None):
    if not os.path.exists(zip_path):
        print(f"ERROR: File '{zip_path}' not found.")
        sys.exit(1)

    print("=" * 70)
    print(f" AUDITING EZIE-MAG ZIP ARCHIVE: {os.path.basename(zip_path)}")
    print("=" * 70)

    start_dt = pd.to_datetime(start_time) if start_time else None
    end_dt = pd.to_datetime(end_time) if end_time else None

    if start_dt and end_dt:
        print(f"Target Storm Window: {start_dt} to {end_dt} UTC")
    else:
        print("Target Storm Window: Entire Archive Duration")

    station_stats = []
    total_raw_lines = 0
    total_valid_records = 0

    with zipfile.ZipFile(zip_path, 'r') as zf:
        file_list = [f for f in zf.namelist() if not f.endswith('/')]
        print(f"Total Enclosed Station Archives: {len(file_list)}\n")

        for idx, f_name in enumerate(file_list):
            stn_id_fallback = os.path.basename(f_name).split('.')[0].upper()
            file_valid_recs = 0
            file_window_recs = 0
            file_lines = 0
            times, lats, lons = [], [], []

            with zf.open(f_name) as f_bin:
                if f_name.endswith('.gz'):
                    stream = gzip.open(f_bin, mode='rt', encoding='ascii', errors='ignore')
                else:
                    stream = [line.decode('ascii', errors='ignore') for line in f_bin.readlines()]

                for raw_line in stream:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    file_lines += 1

                    rec = readsmr(line)
                    if rec is None:
                        continue

                    tval = rec['tval']
                    if not np.isfinite(tval) or tval < MIN_VALID_EPOCH or tval > MAX_VALID_EPOCH:
                        continue

                    try:
                        dt_utc = pd.to_datetime(tval, unit='s', utc=True).tz_localize(None)
                    except Exception:
                        continue

                    file_valid_recs += 1
                    times.append(dt_utc)
                    lats.append(rec['lat'])
                    lons.append(rec['lon'])

                    if start_dt and end_dt and (start_dt <= dt_utc <= end_dt):
                        file_window_recs += 1

                if hasattr(stream, 'close'):
                    stream.close()

            total_raw_lines += file_lines
            total_valid_records += file_valid_recs

            if times:
                stn_name = rec['stid'] if (rec.get('stid') and len(rec['stid']) >= 2) else stn_id_fallback
                min_t = min(times)
                max_t = max(times)
                mean_lat = np.mean(lats)
                mean_lon = np.mean(lons)

                station_stats.append({
                    'station': stn_name,
                    'file': os.path.basename(f_name),
                    'total_records': file_valid_recs,
                    'window_records': file_window_recs if (start_dt and end_dt) else file_valid_recs,
                    'lat': mean_lat,
                    'lon': mean_lon,
                    'first_time': min_t,
                    'last_time': max_t
                })

    df_stats = pd.DataFrame(station_stats)

    print("-" * 70)
    print(" AUDIT SUMMARY REPORT")
    print("-" * 70)
    print(f"  • Total Enclosed Files:        {len(file_list)}")
    print(f"  • Stations with Valid Data:    {len(df_stats)}")

    if df_stats.empty:
        print("\n[CRITICAL WARNING] No valid SMR records found in this ZIP archive!")
        return

    active_in_window = df_stats[df_stats['window_records'] > 0]
    print(f"  • Stations Active in Window:   {len(active_in_window)}")
    print(f"  • Total Parsed Records:        {total_valid_records:,}")
    print(f"  • Overall Time Coverage:       {df_stats['first_time'].min()} to {df_stats['last_time'].max()}")

    print("\n" + "=" * 70)
    print(" STATION DATA BREAKDOWN")
    print("=" * 70)
    header_str = f"{'Station':<12} | {'Lat':<7} | {'Lon':<8} | {'Window Recs':<11} | {'Total Recs':<10} | {'Status'}"
    print(header_str)
    print("-" * len(header_str))

    for _, row in df_stats.sort_values('window_records', ascending=False).iterrows():
        status = "ACTIVE" if row['window_records'] > 0 else "OFFLINE IN WINDOW"
        print(f"{row['station']:<12} | {row['lat']:<7.2f} | {row['lon']:<8.2f} | {row['window_records']:<11,d} | {row['total_records']:<10,d} | {status}")

    print("-" * len(header_str))

    if len(active_in_window) == 0:
        print("\n[FAIL] Archive has station data, but NO records exist within your requested start/end time window.")
    else:
        print(f"\n[PASS] Found {len(active_in_window)} station(s) with active data ready for vector processing.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit EZIE-Mag monthly ZIP archives for data presence.")
    parser.add_argument("zip_path", type=str, nargs="?", default="downloads/eziemag.20240501-20240531.smr.60s.zip", help="Path to ZIP file")
    parser.add_argument("--start", type=str, default="2024-05-10T00:00:00", help="Start UTC date/time")
    parser.add_argument("--end", type=str, default="2024-05-12T00:00:00", help="End UTC date/time")
    args = parser.parse_args()

    audit_zip_archive(args.zip_path, start_time=args.start, end_time=args.end)
