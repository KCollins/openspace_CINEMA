import os
import datetime
import warnings
import numpy as np
from PIL import Image
import pyaurorax

warnings.filterwarnings("ignore")

# --- 1. Configuration ---
START_TIME = datetime.datetime(2024, 1, 15, 4, 0, 0)
END_TIME = datetime.datetime(2024, 1, 15, 10, 0, 0)
TIME_STEP = datetime.timedelta(minutes=15)

SITES = ["fsmi", "gill", "luck", "rabb"]
OUTPUT_DIR = "./oval_images/global/"
CANVAS_WIDTH = 3600
CANVAS_HEIGHT = 1800

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- 2. Helper Functions ---
def parse_dt(dt):
    """Ensures datetime object is timezone-naive."""
    if isinstance(dt, str):
        dt = datetime.datetime.fromisoformat(dt.replace('Z', '+00:00'))
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def lonlat_to_pixel(lon, lat, width, height):
    """Converts geographic (lon, lat) to equirectangular canvas (x, y)."""
    # Normalize longitudes from [0, 360] East to [-180, 180]
    lon_norm = np.where(lon > 180.0, lon - 360.0, lon)

    x = np.floor((lon_norm + 180.0) / 360.0 * (width - 1)).astype(int)
    y = np.floor((90.0 - lat) / 180.0 * (height - 1)).astype(int)
    return x, y

def sanitize_grid(arr):
    """Extracts a 2D spatial grid from skymap arrays (handles 3D altitude layers)."""
    if arr is None:
        return None
    arr = np.squeeze(np.array(arr))
    if arr.ndim == 3:
        arr = arr[0]
    return arr

def normalize_image_shape(img):
    """Standardizes 2D or 3D camera images into (H, W, C) format."""
    img = np.squeeze(np.array(img))
    if img.ndim == 2:
        return img[:, :, np.newaxis]
    elif img.ndim == 3:
        s0, s1, s2 = img.shape
        if s0 in [1, 3, 4] and s1 > 10 and s2 > 10:
            img = np.transpose(img, (1, 2, 0))
        elif s1 in [1, 3, 4] and s0 > 10 and s2 > 10:
            img = np.transpose(img, (0, 2, 1))
    return img

def extract_skymap_coords(sm_read):
    """Extracts latitude, longitude, and elevation arrays from skymap object."""
    containers = [sm_read]
    if hasattr(sm_read, 'data') and isinstance(sm_read.data, (list, tuple, np.ndarray)):
        if len(sm_read.data) > 0:
            containers.append(sm_read.data[0])

    for container in containers:
        glat = getattr(container, 'full_map_latitude', getattr(container, 'full_glat', None))
        glon = getattr(container, 'full_map_longitude', getattr(container, 'full_glon', None))
        elev = getattr(container, 'full_elevation', getattr(container, 'elevation', None))
        if glat is not None and glon is not None and elev is not None:
            return sanitize_grid(glat), sanitize_grid(glon), sanitize_grid(elev)
    return None, None, None

# --- 3. Build Timestamps ---
target_times = []
t_curr = START_TIME
while t_curr <= END_TIME:
    target_times.append(t_curr)
    t_curr += TIME_STEP

print("=" * 70)
print(f" AURORAX PIPELINE: {START_TIME} to {END_TIME}")
print("=" * 70)

aurorax = pyaurorax.PyAuroraX()
cam_dataset = aurorax.data.ucalgary.get_dataset("TREX_RGB_RAW_NOMINAL")
skymap_dataset = aurorax.data.ucalgary.get_dataset("TREX_RGB_SKYMAP_IDLSAV")

site_data = {}

# --- 4. Data Acquisition ---
for site in SITES:
    print(f"\n[+] Processing Site: {site.upper()}")

    # Download 1-minute camera data slices around target timestamps
    downloaded_files = []
    for t_target in target_times:
        try:
            dl_res = aurorax.data.ucalgary.download(
                "TREX_RGB_RAW_NOMINAL",
                start=t_target,
                end=t_target + datetime.timedelta(seconds=59),
                site_uid=site
            )
            files = getattr(dl_res, 'filenames', dl_res)
            if files:
                downloaded_files.extend(files)
        except Exception:
            pass

    if not downloaded_files:
        print("    └─ No camera files returned for this timeframe.")
        continue

    print(f"    ├─ Files Downloaded : {len(downloaded_files)}")

    # Read camera frame data
    cam_read = aurorax.data.ucalgary.read(dataset=cam_dataset, file_list=downloaded_files)
    raw_images = getattr(cam_read, 'data', [])
    raw_ts = getattr(cam_read, 'timestamps', getattr(cam_read, 'timestamp', []))

    frame_list = list(raw_images) if hasattr(raw_images, '__len__') else []
    ts_list = [parse_dt(t) for t in raw_ts] if hasattr(raw_ts, '__len__') else []

    print(f"    ├─ Frames Extracted : {len(frame_list)}")

    # Retrieve skymap using download_best_skymap
    glat, glon, elev = None, None, None
    try:
        sm_dl = aurorax.data.ucalgary.download_best_skymap(
            dataset_name="TREX_RGB_SKYMAP_IDLSAV",
            site_uid=site,
            timestamp=START_TIME
        )
        sm_files = getattr(sm_dl, 'filenames', sm_dl)
        if sm_files:
            sm_read = aurorax.data.ucalgary.read(dataset=skymap_dataset, file_list=sm_files)
            glat, glon, elev = extract_skymap_coords(sm_read)
    except Exception as e:
        print(f"    ├─ Skymap Warning   : {e}")

    skymap_ok = glat is not None
    print(f"    └─ Skymap Loaded    : {'OK' if skymap_ok else 'FAILED'}")

    site_data[site] = {
        "images": frame_list,
        "timestamps": ts_list,
        "glat": glat,
        "glon": glon,
        "elevation": elev
    }

# --- 5. Render Equirectangular PNG Sequence ---
print("\n" + "=" * 70)
print(" RENDERING EQUIRECTANGULAR MOSAICS")
print("=" * 70)

for t_target in target_times:
    canvas = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH, 4), dtype=np.uint8)
    rendered_pixels = 0
    active_sites = []

    for site, sdata in site_data.items():
        if sdata["glat"] is None or not sdata["images"]:
            continue

        # Find closest camera frame within +/- 7.5 minutes of step mark
        match_idx = next(
            (i for i, t in enumerate(sdata["timestamps"])
             if abs((t - t_target).total_seconds()) <= 450),
            None
        )

        if match_idx is None or match_idx >= len(sdata["images"]):
            continue

        try:
            raw_frame = sdata["images"][match_idx]
            if raw_frame is None:
                continue
            img = np.array(raw_frame)
            if img.size == 0 or img.ndim < 2:
                continue
        except Exception:
            continue

        glat, glon, elev = sdata["glat"], sdata["glon"], sdata["elevation"]

        # Standardize image array layout to (H, W, C)
        img_norm = normalize_image_shape(img)

        # Dynamically match spatial shapes across glat, glon, elev, and img
        min_h = min(glat.shape[0], glon.shape[0], elev.shape[0], img_norm.shape[0])
        min_w = min(glat.shape[1], glon.shape[1], elev.shape[1], img_norm.shape[1])

        if min_h <= 0 or min_w <= 0:
            continue

        glat_c = glat[:min_h, :min_w]
        glon_c = glon[:min_h, :min_w]
        elev_c = elev[:min_h, :min_w]
        img_c  = img_norm[:min_h, :min_w, :]

        # Filter out horizon noise (<15 deg elevation) and invalid coordinates
        mask = (elev_c > 15) & (glat_c > 0) & (~np.isnan(glat_c)) & (~np.isnan(glon_c))

        if not np.any(mask):
            continue

        lons, lats = glon_c[mask], glat_c[mask]
        x, y = lonlat_to_pixel(lons, lats, CANVAS_WIDTH, CANVAS_HEIGHT)

        valid_bounds = (x >= 0) & (x < CANVAS_WIDTH) & (y >= 0) & (y < CANVAS_HEIGHT)
        if not np.any(valid_bounds):
            continue

        x_val, y_val = x[valid_bounds], y[valid_bounds]
        colors = img_c[mask][valid_bounds]

        if len(colors) == 0:
            continue

        # Format color array shape to (N, 3)
        if colors.ndim == 1:
            colors = np.column_stack([colors] * 3)
        elif colors.shape[1] == 1:
            colors = np.hstack([colors, colors, colors])
        elif colors.shape[1] > 3:
            colors = colors[:, :3]

        # Normalize pixel intensities to 8-bit [0, 255] range
        if colors.dtype != np.uint8:
            c_min, c_max = colors.min(), colors.max()
            if c_max > c_min:
                colors = ((colors.astype(np.float32) - c_min) / (c_max - c_min) * 255.0).astype(np.uint8)
            else:
                colors = colors.astype(np.uint8)

        alphas = np.full((len(colors), 1), 255, dtype=np.uint8)
        rgba = np.hstack((colors, alphas))

        canvas[y_val, x_val] = rgba
        rendered_pixels += len(x_val)
        active_sites.append(site.upper())

    fname = t_target.strftime("%Y-%m-%d-%H-%M.png")
    filepath = os.path.join(OUTPUT_DIR, fname)
    Image.fromarray(canvas, 'RGBA').save(filepath)

    if rendered_pixels > 0:
        print(f" [✓] {fname} | {rendered_pixels:7d} pixels projected | Sites: {', '.join(active_sites)}")
    else:
        print(f" [-] {fname} | Empty frame")

print("\nProcessing complete! Output saved to ./oval_images/global/")