import argparse
import os
from datetime import datetime, timezone
import requests

from station_resolver import StationResolver
from arc_builder import resolve_band_index, dt_to_j2000_seconds, build_propagation_arc, export_arcs_to_osfls

VAIL_SPOTS_URL = "https://vailrerbn.com/api/v1/spots"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch live/recent RBN spots via Vail ReRBN API.")
    parser.add_argument("--call", default="", help="Filter by spotted DX callsign")
    parser.add_argument("--spotter", default="", help="Filter by receiving skimmer callsign")
    parser.add_argument("--band", default="", help="Filter by band (e.g., 20m)")
    parser.add_argument("--mode", default="CW", help="Filter by mode (e.g., CW, FT8, RTTY)")
    parser.add_argument("--limit", type=int, default=500, help="Max recent spots to fetch")
    parser.add_argument("--data-dir", default="./data", help="Directory for SQLite cache and cty.dat")
    parser.add_argument("--out-dir", default="./output/osfls", help="Output directory for .osfls frame")
    parser.add_argument("--qrz-user", default=os.getenv("QRZ_USER", ""))
    parser.add_argument("--qrz-pass", default=os.getenv("QRZ_PASS", ""))
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    params = {"limit": args.limit}
    if args.call:
        params["call"] = args.call.upper()
    if args.spotter:
        params["spotter"] = args.spotter.upper()
    if args.band:
        params["band"] = args.band.lower()
    if args.mode:
        params["mode"] = args.mode.upper()

    resp = requests.get(VAIL_SPOTS_URL, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    spots = payload if isinstance(payload, list) else payload.get("spots", payload.get("data", []))

    resolver = StationResolver(args.data_dir, qrz_user=args.qrz_user, qrz_pass=args.qrz_pass)
    arcs = []
    seen = set()

    try:
        for s in spots:
            dx_call = (s.get("dx") or s.get("callsign") or s.get("call") or "").strip().upper()
            de_call = (s.get("spotter") or s.get("de") or "").strip().upper()
            band = (s.get("band") or "").strip().lower()
            if not dx_call or not de_call or (dx_call, de_call, band) in seen:
                continue
            seen.add((dx_call, de_call, band))

            tx_coords = resolver.resolve(dx_call)
            rx_coords = resolver.resolve(de_call)
            if not tx_coords or not rx_coords:
                continue

            freq_khz = float(s.get("frequency") or s.get("freq") or 14000.0)
            snr_db = float(s.get("snr") or s.get("db") or 0.0)
            speed_wpm = float(s.get("wpm") or s.get("speed") or 20.0)
            band_idx = resolve_band_index(band, freq_khz)

            arc_res = build_propagation_arc(
                tx_coords[0], tx_coords[1], rx_coords[0], rx_coords[1],
                scalars=(band_idx, freq_khz, snr_db, speed_wpm, 0.0),
            )
            if arc_res is not None:
                xyz, dist_km = arc_res
                arcs.append((xyz, (band_idx, freq_khz, snr_db, speed_wpm, dist_km)))
    finally:
        resolver.close()

    now_dt = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    out_path = os.path.join(args.out_dir, now_dt.strftime("%Y-%m-%dT%H-%M-%S-000.osfls"))
    export_arcs_to_osfls(out_path, arcs, dt_to_j2000_seconds(now_dt))
    print(f"Wrote live snapshot {out_path} ({len(arcs)} arcs)")


if __name__ == "__main__":
    main()