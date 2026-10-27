import os
import sys
import time
import gzip
import zipfile
import argparse
import calendar
import urllib.request
import numpy as np
import pandas as pd
import xarray as xr

BASE_URL = "https://eziemag.jhuapl.edu/ui/DataDownload/download/smr.60s"
DEFAULT_OUTPUT_NC = "eziemag_data.nc"

MIN_VALID_EPOCH = 1577836800.0  # 2020-01-01
MAX_VALID_EPOCH = 2051222400.0  # 2035-01-01

def log(msg):
    print(msg, flush=True)

def download_eziemag_zip(year, month, cache_dir="downloads"):
    last_day = calendar.monthrange(year, month)[1]
    filename = f"eziemag.{year}{month:02d}01-{year}{month:02d}{last_day:02d}.smr.60s.zip"
    url = f"{BASE_URL}/{filename}"
    os.makedirs(cache_dir, exist_ok=True)
    local_path = os.path.join(cache_dir, filename)

    if os.path.exists(local_path):
        log(f"[CACHE] Using existing download: {local_path}")
        return local_path

    log(f"Downloading EZIE-Mag 60s ASCII archive from: {url}")
    try:
        def _progress(count, block_size, total_size):
            percent = int(count * block_size * 100 / total_size)
            sys.stdout.write(f"\r  -> Downloading {filename}: {percent}%")
            sys.stdout.flush()

        urllib.request.urlretrieve(url, local_path, reporthook=_progress)
        log("\n  -> Download complete!")
        return local_path
    except Exception as e:
        if os.path.exists(local_path): os.remove(local_path)
        raise RuntimeError(f"Failed to download {url}: {e}")

def readsmr(line):
    rec = {}
    linesplit = line.split(' ', maxsplit=4)
    if len(linesplit) != 5:
        return None

    rec['timeString'] = linesplit[0]
    try:
        rec['tval'] = np.float64(linesplit[1])
        rec['intt'] = np.float64(linesplit[2])
        rec['nsamp'] = np.float64(linesplit[3])
    except ValueError:
        return None

    line = linesplit[4]
    linesplit = line.split('"', maxsplit=2)
    if len(linesplit) != 3:
        return None

    rec['stid'] = linesplit[1].strip()

    line = linesplit[2]
    linesplit = line.split(' ', maxsplit=19)
    if len(linesplit) != 20:
        return None

    try:
        rec['fingerprint'] = linesplit[1]
        rec['gpslat'] = np.float32(linesplit[2])
        rec['gpslon'] = np.float32(linesplit[3])
        rec['gpsalt'] = np.float32(linesplit[4])
        rec['tres'] = np.int32(linesplit[5])
        rec['ctemp'] = np.float32(linesplit[6])
        rec['ccr'] = np.int32(linesplit[7])
        rec['Bx'] = np.float32(linesplit[8])
        rec['By'] = np.float32(linesplit[9])
        rec['Bz'] = np.float32(linesplit[10])
    except ValueError:
        return None

    return rec

def parse_smr_stream(stream_lines, default_stn_id):
    records = []
    for raw_line in stream_lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        rec = readsmr(line)
        if rec is None:
            continue

        tval = rec['tval']
        if not np.isfinite(tval) or tval < MIN_VALID_EPOCH or tval > MAX_VALID_EPOCH:
            continue

        lat, lon = rec['gpslat'], rec['gpslon']
        if not np.isfinite(lat) or not np.isfinite(lon) or lat < -90.0 or lat > 90.0 or lon < -180.0 or lon > 180.0:
            continue

        bx, by, bz = rec['Bx'], rec['By'], rec['Bz']
        if not np.isfinite(bx) or not np.isfinite(by) or not np.isfinite(bz):
            continue

        try:
            dt_utc = pd.to_datetime(tval, unit='s', utc=True).tz_localize(None)
        except Exception:
            continue

        base_name = rec['stid'] if (rec['stid'] and len(rec['stid']) >= 2) else default_stn_id
        unique_stn_id = f"{base_name}_{float(lat):.1f}_{float(lon):.1f}"

        records.append({
            'time': dt_utc,
            'stid': unique_stn_id,
            'lat': float(lat),
            'lon': float(lon),
            'Bx': float(bx),
            'By': float(by),
            'Bz': float(bz)
        })
    return records

def process_eziemag(year=None, month=None, start_time=None, end_time=None, input_file=None, output_nc=DEFAULT_OUTPUT_NC, cadence="1min", force=False):
    if os.path.exists(output_nc) and not force:
        log(f"[SKIP] Target file '{output_nc}' already exists. Use --force to overwrite.")
        return

    parse_start = time.time()
    if year and month:
        archive_path = download_eziemag_zip(year, month)
    elif input_file:
        archive_path = input_file
    else:
        raise ValueError("Must specify either --year and --month OR --input file.")

    log(f"Parsing EZIE-Mag ASCII data from: {archive_path}")
    all_records = []

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            smr_files = [f for f in zf.namelist() if not f.endswith('/')]
            total_files = len(smr_files)
            log(f"Found {total_files} SMR station files in ZIP archive. Parsing records...")

            for idx, f_name in enumerate(smr_files):
                stn_fallback = os.path.basename(f_name).split('.')[0].upper()
                if (idx + 1) % 100 == 0 or idx == 0 or idx == total_files - 1:
                    pct = ((idx + 1) / total_files) * 100
                    log(f"  -> Stream [{idx + 1:4d}/{total_files:4d}] ({pct:5.1f}%) -- Total records parsed: {len(all_records)}")

                with zf.open(f_name) as f_bin:
                    if f_name.endswith('.gz'):
                        with gzip.open(f_bin, mode='rt', encoding='ascii', errors='ignore') as gzf:
                            all_records.extend(parse_smr_stream(gzf, stn_fallback))
                    else:
                        text_lines = [l.decode('ascii', errors='ignore') for l in f_bin.readlines()]
                        all_records.extend(parse_smr_stream(text_lines, stn_fallback))

    if not all_records:
        raise RuntimeError("No valid magnetic records parsed from input file.")

    log(f"Extracted {len(all_records)} valid records in {time.time() - parse_start:.1f} seconds. Filtering window...")

    df_all = pd.DataFrame(all_records)

    # Round timestamps to nearest 1 minute to strip sub-second offsets
    df_all['time'] = df_all['time'].dt.round(cadence)

    if start_time and end_time:
        start_dt = pd.to_datetime(start_time)
        end_dt = pd.to_datetime(end_time)
        df_all = df_all[(df_all['time'] >= start_dt) & (df_all['time'] <= end_dt)]
        target_time_grid = pd.date_range(start=start_dt, end=end_dt, freq=cadence)
    else:
        min_time = df_all['time'].min().floor(cadence)
        max_time = df_all['time'].max().ceil(cadence)
        target_time_grid = pd.date_range(start=min_time, end=max_time, freq=cadence)

    stations = df_all['stid'].unique()
    log(f"Identified {len(stations)} unique active station locations in target window.")

    station_names, station_lats, station_lons = [], [], []
    db_n_list, db_e_list, db_z_list = [], [], []

    for stn_id in stations:
        stn_df = df_all[df_all['stid'] == stn_id].sort_values('time')
        if len(stn_df) < 5:
            continue

        # Aggregate duplicate sub-minute timestamps by mean
        stn_resampled = stn_df.groupby('time')[['Bx', 'By', 'Bz']].mean()

        base_x = stn_resampled['Bx'].median(skipna=True)
        base_y = stn_resampled['By'].median(skipna=True)
        base_z = stn_resampled['Bz'].median(skipna=True)

        stn_resampled = stn_resampled.reindex(target_time_grid).interpolate(method='linear', limit=15)

        station_names.append(stn_id)
        station_lats.append(float(stn_df['lat'].mean()))
        station_lons.append(float(stn_df['lon'].mean()))
        db_n_list.append(stn_resampled['Bx'].values - base_x)
        db_e_list.append(stn_resampled['By'].values - base_y)
        db_z_list.append(stn_resampled['Bz'].values - base_z)

    ds = xr.Dataset(
        data_vars={
            "dB_N": (["time", "station"], np.column_stack(db_n_list), {"units": "nT"}),
            "dB_E": (["time", "station"], np.column_stack(db_e_list), {"units": "nT"}),
            "dB_Z": (["time", "station"], np.column_stack(db_z_list), {"units": "nT"}),
        },
        coords={
            "time": ("time", target_time_grid.values.astype("datetime64[ns]")),
            "lat": ("station", station_lats),
            "lon": ("station", station_lons),
            "station_name": ("station", station_names),
        },
        attrs={"title": "EZIE-Mag 3D Perturbation Dataset"}
    )

    ds.to_netcdf(output_nc)
    log(f"\n[SUCCESS] Saved EZIE-Mag NetCDF to: {os.path.abspath(output_nc)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--start", type=str)
    parser.add_argument("--end", type=str)
    parser.add_argument("--input", type=str)
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_NC)
    parser.add_argument("--cadence", type=str, default="1min")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    process_eziemag(year=args.year, month=args.month, start_time=args.start, end_time=args.end, input_file=args.input, output_nc=args.output, cadence=args.cadence, force=args.force)