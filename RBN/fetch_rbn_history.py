import argparse
import csv
import io
import os
import random
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple
import requests

from station_resolver import StationResolver
from arc_builder import resolve_band_index, dt_to_j2000_seconds, build_propagation_arc, export_arcs_to_osfls

RBN_HISTORY_BASE = "https://data.reversebeacon.net/rbn_history"


def parse_iso_utc(ts: str) -> datetime:
    cleaned = ts.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def download_daily_zip(date_str: str, data_dir: str) -> Optional[str]:
    """Downloads YYYYMMDD.zip from RBN history archive if not already cached locally."""
    zip_path = os.path.join(data_dir, f"{date_str}.zip")
    if os.path.exists(zip_path):
        return zip_path

    url = f"{RBN_HISTORY_BASE}/{date_str}.zip"
    print(f"Downloading {url} ...")
    resp = requests.get(url, stream=True, timeout=60)
    if resp.status_code == 404:
        print(f"Warning: Archive not found for {date_str} ({url})")
        return None
    resp.raise_for_status()

    tmp_path = zip_path + ".part"
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    os.replace(tmp_path, zip_path)
    return zip_path


def parse_csv_set(val: Optional[str]) -> Optional[Set[str]]:
    if not val:
        return None
    items = {x.strip().upper() for x in val.split(",") if x.strip()}
    return items if items else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch historical RBN archives and build OpenSpace .osfls files.")
    parser.add_argument("--start", required=True, help="Start UTC timestamp (ISO 8601, e.g. 2025-10-25T00:00:00Z)")
    parser.add_argument("--end", required=True, help="End UTC timestamp (ISO 8601, e.g. 2025-10-25T06:00:00Z)")
    parser.add_argument("--bin-minutes", type=int, default=15, help="Discrete time bucket width in minutes (default: 15)")
    parser.add_argument("--data-dir", default="./data", help="Directory for cached RBN zips, cty.dat, and SQLite DB")
    parser.add_argument("--out-dir", default="./output/osfls", help="Output directory for .osfls sequence")

    # RBN-aligned query filters
    parser.add_argument("--bands", default="", help="Comma-separated bands (e.g., 40m,20m,15m,10m)")
    parser.add_argument("--freq-min", type=float, default=None, help="Minimum frequency in kHz")
    parser.add_argument("--freq-max", type=float, default=None, help="Maximum frequency in kHz")
    parser.add_argument("--tx-modes", default="", help="Comma-separated tx_mode values (e.g., CW,FT8,RTTY)")
    parser.add_argument("--spot-types", default="", help="Comma-separated RBN mode values (e.g., CQ,DX,BEACON)")
    parser.add_argument("--dx", default="", help="Comma-separated transmitting station callsigns")
    parser.add_argument("--de", default="", help="Comma-separated receiving skimmer callsigns")
    parser.add_argument("--dx-cont", default="", help="Comma-separated TX continents (NA,EU,AS,SA,OC,AF)")
    parser.add_argument("--de-cont", default="", help="Comma-separated RX continents (NA,EU,AS,SA,OC,AF)")
    parser.add_argument("--min-snr", type=float, default=None, help="Minimum SNR in dB")
    parser.add_argument("--max-snr", type=float, default=None, help="Maximum SNR in dB")
    parser.add_argument("--min-speed", type=float, default=None, help="Minimum CW/RTTY speed in WPM")
    parser.add_argument("--max-arcs-per-frame", type=int, default=3000, help="Max arcs per frame (0 = unlimited)")

    # Callsign lookup options
    parser.add_argument("--qrz-user", default=os.getenv("QRZ_USER", ""), help="QRZ XML username")
    parser.add_argument("--qrz-pass", default=os.getenv("QRZ_PASS", ""), help="QRZ XML password")
    parser.add_argument("--strict-qrz", action="store_true", help="Drop stations without exact QRZ/skimmer coords")

    args = parser.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    start_dt = parse_iso_utc(args.start)
    end_dt = parse_iso_utc(args.end)
    bin_delta = timedelta(minutes=args.bin_minutes)

    band_filter = {b.lower() for b in args.bands.split(",") if b.strip()} if args.bands else None
    tx_mode_filter = parse_csv_set(args.tx_modes)
    spot_type_filter = parse_csv_set(args.spot_types)
    dx_filter = parse_csv_set(args.dx)
    de_filter = parse_csv_set(args.de)
    dx_cont_filter = parse_csv_set(args.dx_cont)
    de_cont_filter = parse_csv_set(args.de_cont)

    # Pre-initialize all discrete time buckets between start_dt and end_dt
    binned_spots: Dict[datetime, List[dict]] = {}
    cursor = start_dt
    while cursor < end_dt:
        binned_spots[cursor] = []
        cursor += bin_delta

    # Iterate over all UTC calendar days overlapping [start_dt, end_dt)
    day_cursor = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    last_day = (end_dt - timedelta(seconds=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    total_matched = 0
    while day_cursor <= last_day:
        date_str = day_cursor.strftime("%Y%m%d")
        zip_path = download_daily_zip(date_str, args.data_dir)
        if zip_path:
            with zipfile.ZipFile(zip_path, "r") as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if csv_names:
                    print(f"Filtering {csv_names[0]} ...")
                    with zf.open(csv_names[0], "r") as raw_f:
                        text_stream = io.TextIOWrapper(raw_f, encoding="utf-8", errors="ignore")
                        reader = csv.DictReader(text_stream)
                        for row in reader:
                            raw_date = (row.get("date") or "").strip()
                            if not raw_date:
                                continue
                            try:
                                spot_dt = datetime.strptime(raw_date[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            except ValueError:
                                continue

                            if spot_dt < start_dt or spot_dt >= end_dt:
                                continue

                            band = (row.get("band") or "").strip().lower()
                            if band_filter and band not in band_filter:
                                continue

                            tx_mode = (row.get("tx_mode") or "").strip().upper()
                            if tx_mode_filter and tx_mode not in tx_mode_filter:
                                continue

                            spot_type = (row.get("mode") or "").strip().upper()
                            if spot_type_filter and spot_type not in spot_type_filter:
                                continue

                            dx_call = (row.get("dx") or "").strip().upper()
                            de_call = (row.get("callsign") or "").strip().upper()
                            if dx_filter and dx_call not in dx_filter:
                                continue
                            if de_filter and de_call not in de_filter:
                                continue

                            if dx_cont_filter and (row.get("dx_cont") or "").strip().upper() not in dx_cont_filter:
                                continue
                            if de_cont_filter and (row.get("de_cont") or "").strip().upper() not in de_cont_filter:
                                continue

                            try:
                                freq_khz = float(row.get("freq") or 0.0)
                                snr_db = float(row.get("db") or 0.0)
                                speed_wpm = float(row.get("speed") or 0.0)
                            except ValueError:
                                continue

                            if args.freq_min is not None and freq_khz < args.freq_min:
                                continue
                            if args.freq_max is not None and freq_khz > args.freq_max:
                                continue
                            if args.min_snr is not None and snr_db < args.min_snr:
                                continue
                            if args.max_snr is not None and snr_db > args.max_snr:
                                continue
                            if args.min_speed is not None and speed_wpm < args.min_speed:
                                continue

                            # Assign to discrete time bucket
                            offset_sec = (spot_dt - start_dt).total_seconds()
                            bin_idx = int(offset_sec // (args.bin_minutes * 60))
                            bucket_dt = start_dt + timedelta(minutes=bin_idx * args.bin_minutes)
                            if bucket_dt in binned_spots:
                                binned_spots[bucket_dt].append(
                                    {
                                        "dx": dx_call,
                                        "dx_pfx": (row.get("dx_pfx") or "").strip().upper(),
                                        "de": de_call,
                                        "de_pfx": (row.get("de_pfx") or "").strip().upper(),
                                        "band": band,
                                        "freq_khz": freq_khz,
                                        "snr_db": snr_db,
                                        "speed_wpm": speed_wpm,
                                    }
                                )
                                total_matched += 1

        day_cursor += timedelta(days=1)

    print(f"Matched {total_matched} spots across {len(binned_spots)} time buckets.")

    resolver = StationResolver(
        data_dir=args.data_dir,
        qrz_user=args.qrz_user,
        qrz_pass=args.qrz_pass,
        strict_qrz=args.strict_qrz,
    )

    random.seed(42)
    try:
        for bucket_dt in sorted(binned_spots.keys()):
            spots = binned_spots[bucket_dt]
            if args.max_arcs_per_frame > 0 and len(spots) > args.max_arcs_per_frame:
                spots = random.sample(spots, args.max_arcs_per_frame)

            # Deduplicate identical (tx, rx, band) pairs within a single time bucket
            seen_paths: Set[Tuple[str, str, str]] = set()
            arcs = []

            for s in spots:
                path_key = (s["dx"], s["de"], s["band"])
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)

                tx_coords = resolver.resolve(s["dx"], pfx_hint=s["dx_pfx"])
                rx_coords = resolver.resolve(s["de"], pfx_hint=s["de_pfx"])
                if not tx_coords or not rx_coords:
                    continue

                band_idx = resolve_band_index(s["band"], s["freq_khz"])
                arc_res = build_propagation_arc(
                    tx_coords[0],
                    tx_coords[1],
                    rx_coords[0],
                    rx_coords[1],
                    scalars=(band_idx, s["freq_khz"], s["snr_db"], s["speed_wpm"], 0.0),
                )
                if arc_res is not None:
                    xyz, dist_km = arc_res
                    scalars = (band_idx, s["freq_khz"], s["snr_db"], s["speed_wpm"], dist_km)
                    arcs.append((xyz, scalars))

            filename = bucket_dt.strftime("%Y-%m-%dT%H-%M-%S-000.osfls")
            out_path = os.path.join(args.out_dir, filename)
            trigger_j2000 = dt_to_j2000_seconds(bucket_dt)
            export_arcs_to_osfls(out_path, arcs, trigger_j2000)
            print(f"Wrote {filename} ({len(arcs)} arcs)")
    finally:
        resolver.close()


if __name__ == "__main__":
    main()