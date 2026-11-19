from datetime import datetime, timedelta
import os
import traceback
import matplotlib
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt

GFZ_KP_URL = "https://kp.gfz.de/app/json/"
BASE_DIR = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in globals()
    else os.getcwd()
)
OUT_DIR = os.path.join(BASE_DIR, "aurora_frames")
os.makedirs(OUT_DIR, exist_ok=True)

# Centered dipole geomagnetic pole coordinates (IGRF)
POLE_LAT = np.radians(80.8)
POLE_LON = np.radians(-72.7)


def get_aurora_cmap():
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


def geo_to_geomag(lat_rad, lon_rad):
    sin_mlat = (
        np.sin(lat_rad) * np.sin(POLE_LAT)
        + np.cos(lat_rad) * np.cos(POLE_LAT) * np.cos(lon_rad - POLE_LON)
    )
    mlat_deg = np.degrees(np.arcsin(np.clip(sin_mlat, -1.0, 1.0)))

    y = np.cos(lat_rad) * np.sin(lon_rad - POLE_LON)
    x = (
        np.cos(lat_rad) * np.cos(lon_rad - POLE_LON) * np.sin(POLE_LAT)
        - np.sin(lat_rad) * np.cos(POLE_LAT)
    )
    mlon_deg = np.degrees(np.arctan2(y, x))
    return mlat_deg, mlon_deg


def synthesize_auroral_oval(dt, kp, mlat_deg, mlon_deg):
    # Subsolar geomagnetic longitude at UTC timestamp
    utc_hours = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    doy = dt.timetuple().tm_yday
    subsolar_lat = np.radians(23.44 * np.sin(2.0 * np.pi * (doy - 81) / 365.25))
    subsolar_lon = np.radians(-15.0 * (utc_hours - 12.0))
    _, subsolar_mlon = geo_to_geomag(subsolar_lat, subsolar_lon)

    # Magnetic Local Time (0 = midnight, 12 = noon)
    mlt = ((mlon_deg - subsolar_mlon) / 15.0 + 12.0) % 24.0

    # Feldstein-Starkov / Holzworth-Meng Kp-driven oval center & width
    oval_center = (71.5 - 1.95 * kp) - 2.6 * np.cos(2.0 * np.pi * mlt / 24.0)
    sigma = 2.2 + 0.35 * kp
    mlt_brightness = 0.65 + 0.35 * np.cos(2.0 * np.pi * (mlt - 23.5) / 24.0)
    peak = np.clip(18.0 + 9.2 * kp, 0.0, 100.0)

    intensity = (
        peak
        * mlt_brightness
        * np.exp(-0.5 * ((np.abs(mlat_deg) - oval_center) / sigma) ** 2)
    )
    intensity[intensity < 1.5] = 0.0
    return intensity


def fetch_historical_aurora(start_date="2024-05-10", end_date="2024-05-12"):
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    # Query GFZ Potsdam Kp index with 3-hour padding for smooth hourly interpolation
    params = {
        "start": (start_dt - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (end_dt + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index": "Kp",
    }
    print("Fetching historical Kp data from GFZ Potsdam ...")
    resp = requests.get(GFZ_KP_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    kp_times = np.array(
        [
            datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
            for ts in data["datetime"]
        ]
    )
    kp_vals = np.array(data["Kp"], dtype=np.float32)

    lats = np.linspace(90.0, -90.0, 181)
    lons = np.linspace(-180.0, 179.0, 360)
    lon_grid, lat_grid = np.meshgrid(np.radians(lons), np.radians(lats))
    mlat_deg, mlon_deg = geo_to_geomag(lat_grid, lon_grid)

    cmap = get_aurora_cmap()
    cur_dt = start_dt
    created = 0

    while cur_dt <= end_dt:
        iso_stamp = cur_dt.strftime("%Y-%m-%dT%H-%M-%S")
        out_path = os.path.join(OUT_DIR, f"{iso_stamp}.png")

        if not os.path.exists(out_path):
            kp_interp = float(np.interp(cur_dt.timestamp(), kp_times, kp_vals))
            oval_grid = synthesize_auroral_oval(cur_dt, kp_interp, mlat_deg, mlon_deg)

            fig = plt.figure(figsize=(10, 5), dpi=150)
            ax = plt.Axes(fig, [0.0, 0.0, 1.0, 1.0])
            ax.set_axis_off()
            fig.add_axes(ax)

            ax.imshow(oval_grid, cmap=cmap, vmin=0, vmax=100, aspect="auto")
            plt.savefig(out_path, transparent=True)
            plt.close(fig)
            created += 1

        cur_dt += timedelta(hours=1)

    print(f"Exported {created} historical aurora frames ({start_date} to {end_date}).")
    refresh_gap_bookends(OUT_DIR)


if __name__ == "__main__":
    try:
        fetch_historical_aurora(start_date="2024-05-05", end_date="2024-05-15")
    except Exception:
        traceback.print_exc()
        if "__file__" in globals():
            input("\nPress Enter to exit...")