import argparse
import csv
import io
import os
import random
import zipfile
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set, Tuple
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


def write_asset_file(asset_path: str, start_dt: datetime, end_dt: datetime) -> None:
    """Writes rbn_fieldlines.asset with matching TimeFrameInterval bounds."""
    start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
    lua_content = f"""local earthTransforms = asset.require("scene/solarsystem/planets/earth/transforms")

local node = {{
  Identifier = "Earth-RBN-Propagation",
  Parent = "Earth",
  TimeFrame = {{
    Type = "TimeFrameInterval",
    Start = "{start_str}",
    End = "{end_str}"
  }},
  Renderable = {{
    Type = "RenderableFieldlinesSequence",
    LoadingType = "StaticLoading",
    SourceFolder = asset.resource("osfls"),
    InputFileType = "Osfls",

    LineWidth = 2.0,
    ColorMethod = "By Quantity",
    ColorQuantity = 0,
    ColorTableRanges = {{
      {{ 0.0, 11.0 }},       -- 0: band_index
      {{ 1800.0, 30000.0 }}, -- 1: freq_khz
      {{ 0.0, 45.0 }},       -- 2: snr_db
      {{ 10.0, 45.0 }},      -- 3: speed_wpm
      {{ 0.0, 16000.0 }}     -- 4: distance_km
    }},
    ColorMinMaxRange = {{ 0.0, 11.0 }},
    ColorTablePaths = {{
      asset.resource("colortables/rbn-band.txt"),
      asset.resource("colortables/rbn-freq.txt"),
      asset.resource("colortables/rbn-snr.txt"),
      asset.resource("colortables/rbn-speed.txt"),
      asset.resource("colortables/rbn-distance.txt")
    }},
    Color = {{ 1.0, 0.84, 0.0, 1.0 }},

    ParticleSpacing = 25.0,
    ParticleSize = 18.0,
    FlowColor = {{ 1.0, 1.0, 1.0, 0.45 }},
    FlowSpeed = 20.0,
    FlowEnabled = true,
    ReversedFlow = false,
    Enabled = true
  }},
  GUI = {{
    Path = "/Solar System/Planets/Earth/Radio Propagation",
    Name = "RBN Propagation Arcs",
    Description = "Reverse Beacon Network TX-to-RX propagation paths"
  }}
}}

asset.onInitialize(function()
  openspace.addSceneGraphNode(node)
end)

asset.onDeinitialize(function()
  openspace.removeSceneGraphNode(node)
end)

asset.export(node)
"""
    with open(asset_path, "w", encoding="utf-8") as f:
        f.write(lua_content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch historical RBN archives and build OpenSpace .osfls files.")
    parser.add_argument("--start", required=True, help="Start UTC timestamp (ISO 8601)")
    parser.add_argument("--end", required=True, help="End UTC timestamp (ISO 8601)")
    parser.add_argument("--step-minutes", type=int, default=1, help="Frame interval in minutes (default: 1)")
    parser.add_argument("--window-minutes", type=int, default=3, help="How long each spot stays visible in minutes (default: 3)")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--out-dir", default="./output/osfls")
    parser.add_argument("--asset-out", default="./output/rbn_fieldlines.asset")

    parser.add_argument("--bands", default="")
    parser.add_argument("--freq-min", type=float, default=None)
    parser.add_argument("--freq-max", type=float, default=None)
    parser.add_argument("--tx-modes", default="")
    parser.add_argument("--spot-types", default="")
    parser.add_argument("--dx", default="")
    parser.add_argument("--de", default="")
    parser.add_argument("--dx-cont", default="")
    parser.add_argument("--de-cont", default="")
    parser.add_argument("--min-snr", type=float, default=None)
    parser.add_argument("--max-snr", type=float, default=None)
    parser.add_argument("--min-speed", type=float, default=None)
    parser.add_argument("--max-arcs-per-frame", type=int, default=1500)

    parser.add_argument("--qrz-user", default=os.getenv("QRZ_USER", ""))
    parser.add_argument("--qrz-pass", default=os.getenv("QRZ_PASS", ""))
    parser.add_argument("--strict-qrz", action="store_true")

    args = parser.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    start_dt = parse_iso_utc(args.start)
    end_dt = parse_iso_utc(args.end)
    step_delta = timedelta(minutes=args.step_minutes)
    window_delta = timedelta(minutes=args.window_minutes)

    band_filter = {b.lower() for b in args.bands.split(",") if b.strip()} if args.bands else None
    tx_mode_filter = parse_csv_set(args.tx_modes)
    spot_type_filter = parse_csv_set(args.spot_types)
    dx_filter = parse_csv_set(args.dx)
    de_filter = parse_csv_set(args.de)
    dx_cont_filter = parse_csv_set(args.dx_cont)
    de_cont_filter = parse_csv_set(args.de_cont)

    matched_spots: List[dict] = []
    day_cursor = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    last_day = (end_dt - timedelta(seconds=1)).replace(hour=0, minute=0, second=0, microsecond=0)

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

                            matched_spots.append(
                                {
                                    "dt": spot_dt,
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
        day_cursor += timedelta(days=1)

    matched_spots.sort(key=lambda s: s["dt"])
    print(f"Matched {len(matched_spots)} total spots.")

    resolver = StationResolver(
        data_dir=args.data_dir,
        qrz_user=args.qrz_user,
        qrz_pass=args.qrz_pass,
        strict_qrz=args.strict_qrz,
    )

    random.seed(42)
    left_idx = 0
    right_idx = 0
    n_spots = len(matched_spots)

    try:
        frame_dt = start_dt
        while frame_dt < end_dt:
            win_end = frame_dt + window_delta
            while left_idx < n_spots and matched_spots[left_idx]["dt"] < frame_dt:
                left_idx += 1
            if right_idx < left_idx:
                right_idx = left_idx
            while right_idx < n_spots and matched_spots[right_idx]["dt"] < win_end:
                right_idx += 1

            window_spots = matched_spots[left_idx:right_idx]
            if args.max_arcs_per_frame > 0 and len(window_spots) > args.max_arcs_per_frame:
                window_spots = random.sample(window_spots, args.max_arcs_per_frame)

            seen_paths: Set[Tuple[str, str, str]] = set()
            arcs = []
            for s in window_spots:
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
                    tx_coords[0], tx_coords[1], rx_coords[0], rx_coords[1],
                    scalars=(band_idx, s["freq_khz"], s["snr_db"], s["speed_wpm"], 0.0),
                )
                if arc_res is not None:
                    xyz, dist_km = arc_res
                    arcs.append((xyz, (band_idx, s["freq_khz"], s["snr_db"], s["speed_wpm"], dist_km)))

            filename = frame_dt.strftime("%Y-%m-%dT%H-%M-%S-000.osfls")
            export_arcs_to_osfls(os.path.join(args.out_dir, filename), arcs, dt_to_j2000_seconds(frame_dt))
            frame_dt += step_delta

        # Write a final empty frame at end_dt so arcs clear at sequence completion
        end_filename = end_dt.strftime("%Y-%m-%dT%H-%M-%S-000.osfls")
        export_arcs_to_osfls(os.path.join(args.out_dir, end_filename), [], dt_to_j2000_seconds(end_dt))
        write_asset_file(args.asset_out, start_dt, end_dt)
        print(f"Sequence complete through {end_filename}; wrote {args.asset_out}.")
    finally:
        resolver.close()


if __name__ == "__main__":
    main()