import argparse
from datetime import datetime, timedelta
import os
import time
import traceback
import matplotlib
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
BASE_DIR = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in globals()
    else os.getcwd()
)
OUT_DIR = os.path.join(BASE_DIR, "aurora_frames")
os.makedirs(OUT_DIR, exist_ok=True)


def get_aurora_cmap():
    """RGBA colormap: 0 = 100% transparent, mid = 557.7 nm green, peak = 630.0 nm crimson."""
    stops = [
        (0.00, (0.00, 0.20, 0.10, 0.00)),
        (0.04, (0.02, 0.55, 0.25, 0.15)),
        (0.20, (0.05, 0.90, 0.35, 0.55)),
        (0.55, (0.20, 1.00, 0.45, 0.85)),
        (0.82, (0.75, 0.20, 0.85, 0.95)),
        (1.00, (1.00, 0.15, 0.45, 1.00)),
    ]
    return LinearSegmentedColormap.from_list("aurora_rgba", stops)


def refresh_gap_bookends(frames_dir, max_gap_minutes=90, fade_minutes=20):
    """Inserts 100% transparent PNGs around any gap > max_gap_minutes."""
    blank_rgba = np.zeros((2, 4, 4), dtype=np.uint8)
    real_frames = []

    for fname in sorted(os.listdir(frames_dir)):
        if not fname.lower().endswith(".png"):
            continue
        fpath = os.path.join(frames_dir, fname)
        try:
            dt = datetime.strptime(os.path.splitext(fname)[0], "%Y-%m-%dT%H-%M-%S")
        except ValueError:
            continue

        img = plt.imread(fpath)
        if img.ndim == 3 and img.shape[2] == 4 and float(img[..., 3].max()) == 0.0:
            os.remove(fpath)
        else:
            real_frames.append(dt)

    max_gap = timedelta(minutes=max_gap_minutes)
    fade = timedelta(minutes=fade_minutes)
    bookends = 0

    for prev_dt, next_dt in zip(real_frames, real_frames[1:]):
        if (next_dt - prev_dt) > max_gap:
            for stamp_dt in (prev_dt + fade, next_dt - fade):
                out_path = os.path.join(
                    frames_dir, stamp_dt.strftime("%Y-%m-%dT%H-%M-%S.png")
                )
                if not os.path.exists(out_path):
                    plt.imsave(out_path, blank_rgba)
                    bookends += 1

    print(f"Updated gap bookends ({bookends} transparent boundary frames).")


def fetch_latest_aurora():
    resp = requests.get(OVATION_URL, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    # Use Forecast Time so the oval matches solar-wind arrival time at Earth
    ts_raw = payload.get("Forecast Time") or payload.get("Observation Time")
    dt = datetime.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S")
    iso_stamp = dt.strftime("%Y-%m-%dT%H-%M-%S")
    out_path = os.path.join(OUT_DIR, f"{iso_stamp}.png")

    if os.path.exists(out_path):
        print(f"Frame {iso_stamp}.png already exists.")
        return

    # Build 181 (lat +90..-90) x 360 (lon -180..+179) equirectangular grid
    grid = np.zeros((181, 360), dtype=np.float32)
    for lon, lat, val in payload["coordinates"]:
        lon_wrapped = ((int(round(lon)) + 180) % 360) - 180
        col = lon_wrapped + 180
        row = 90 - int(round(lat))
        if 0 <= row < 181 and 0 <= col < 360:
            grid[row, col] = float(val)

    fig = plt.figure(figsize=(10, 5), dpi=150)
    ax = plt.Axes(fig, [0.0, 0.0, 1.0, 1.0])
    ax.set_axis_off()
    fig.add_axes(ax)

    ax.imshow(grid, cmap=get_aurora_cmap(), vmin=0, vmax=100, aspect="auto")
    plt.savefig(out_path, transparent=True)
    plt.close(fig)
    print(f"Generated {iso_stamp}.png")

    refresh_gap_bookends(OUT_DIR)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously poll SWPC every 5 minutes to build a live timeline",
    )
    args = parser.parse_args()

    try:
        while True:
            fetch_latest_aurora()
            if not args.loop:
                break
            time.sleep(300)
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception:
        traceback.print_exc()
        if "__file__" in globals():
            input("\nPress Enter to exit...")