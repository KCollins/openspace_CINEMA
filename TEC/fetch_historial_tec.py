from datetime import datetime, timedelta
import gzip
import os
import traceback
import matplotlib
import numpy as np
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_DIR = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in globals()
    else os.getcwd()
)
# OUT_DIR = os.path.join(BASE_DIR, "glotec_frames_historical")
OUT_DIR = os.path.join(BASE_DIR, "glotec_frames") # one directory for recent and historic data
os.makedirs(OUT_DIR, exist_ok=True)

# CODE/IGS open HTTPS archive (direct file-by-file object access)
CODE_ARCHIVE_URL = "https://www.aiub.unibe.ch/download/CODE"


def download_daily_ionex(target_date):
    year = target_date.strftime("%Y")
    doy = target_date.strftime("%j")  # 3-digit day of year (131 = May 10, 2024)
    yy = target_date.strftime("%y")

    # Try Final (1h), Rapid (1h), and legacy IONEX filenames directly
    candidates = [
        f"COD0OPSFIN_{year}{doy}0000_01D_01H_GIM.INX.gz",
        f"COD0OPSRAP_{year}{doy}0000_01D_01H_GIM.INX.gz",
        f"CODG{doy}0.{yy}I.gz",
    ]

    for fname in candidates:
        url = f"{CODE_ARCHIVE_URL}/{year}/{fname}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            return resp.content, fname

    raise FileNotFoundError(
        f"Could not download IONEX GIM for {target_date:%Y-%m-%d} (DOY {doy}). "
        f"Tried: {', '.join(candidates)}"
    )


def parse_ionex_maps(ionex_text):
    lines = ionex_text.splitlines()
    exponent = -1  # IONEX default: grid values are in 0.1 TECU

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        label = line[60:].strip()
        if label == "EXPONENT":
            exponent = int(line[:60].split()[0])
        elif label == "END OF HEADER":
            idx += 1
            break
        idx += 1

    scale = 10.0 ** exponent
    maps = []
    in_tec_map = False
    current_dt = None
    lat_slices = {}

    while idx < len(lines):
        line = lines[idx]
        label = line[60:].strip()

        if label == "START OF TEC MAP":
            in_tec_map = True
            current_dt = None
            lat_slices = {}

        elif in_tec_map and label == "EPOCH OF CURRENT MAP":
            yr, mo, dy, hr, mn, sc = map(int, line[:60].split()[:6])
            # Use timedelta for hours so IONEX '24:00:00' rolls cleanly to 00:00 next day
            current_dt = datetime(yr, mo, dy) + timedelta(
                hours=hr, minutes=mn, seconds=sc
            )

        elif in_tec_map and label == "LAT/LON1/LON2/DLON/H":
            lat = float(line[2:8])
            lon1 = float(line[8:14])
            lon2 = float(line[14:20])
            dlon = float(line[20:26])
            n_lon = int(round((lon2 - lon1) / dlon)) + 1

            vals = []
            while len(vals) < n_lon and idx + 1 < len(lines):
                idx += 1
                row_line = lines[idx][:80]
                for col in range(0, len(row_line), 5):
                    chunk = row_line[col : col + 5].strip()
                    if chunk:
                        vals.append(int(chunk))

            lat_slices[lat] = vals[:n_lon]

        elif in_tec_map and label == "END OF TEC MAP":
            if current_dt is not None and lat_slices:
                # Sort latitudes North (+87.5) to South (-87.5) for top-down image orientation
                sorted_lats = sorted(lat_slices.keys(), reverse=True)
                grid = np.array(
                    [lat_slices[lat] for lat in sorted_lats], dtype=np.float32
                )
                grid[grid == 9999] = np.nan
                grid *= scale
                maps.append((current_dt, grid))
            in_tec_map = False

        idx += 1

    return maps


def fetch_storm_range(start_date="2024-05-10", end_date="2024-05-12", vmax=120):
    dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    print(f"Output folder: {OUT_DIR}")
    while dt <= end_dt:
        raw_gz, fname = download_daily_ionex(dt)
        print(f"Downloaded {fname} ...")

        ionex_text = gzip.decompress(raw_gz).decode("ascii", errors="replace")
        maps = parse_ionex_maps(ionex_text)

        created = 0
        for epoch_dt, tec_grid in maps:
            iso_stamp = epoch_dt.strftime("%Y-%m-%dT%H-%M-%S")
            out_path = os.path.join(OUT_DIR, f"{iso_stamp}.png")
            if os.path.exists(out_path):
                continue

            fig = plt.figure(figsize=(10, 5), dpi=150)
            ax = plt.Axes(fig, [0.0, 0.0, 1.0, 1.0])
            ax.set_axis_off()
            fig.add_axes(ax)

            ax.imshow(tec_grid, cmap="plasma", vmin=0, vmax=vmax, aspect="auto")
            plt.savefig(out_path, transparent=True)
            plt.close(fig)
            created += 1

        print(f"Exported {created} hourly PNG frames for {dt:%Y-%m-%d}.")
        dt += timedelta(days=1)

    print("Historical TEC sync complete.")


if __name__ == "__main__":
    try:
        # Default: May 10–12, 2024 G5 Gannon Geomagnetic Storm
        fetch_storm_range(start_date="2024-05-10", end_date="2024-05-12", vmax=120)
    except Exception:
        traceback.print_exc()
        if "__file__" in globals():
            input("\nPress Enter to exit...")