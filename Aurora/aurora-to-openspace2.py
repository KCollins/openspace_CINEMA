import os
import datetime
import warnings
import numpy as np
from PIL import Image
import pyaurorax

# --- 1. Comprehensive Warning Suppression ---
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*No data found to download.*")

# --- 2. Configuration ---
START_TIME = datetime.datetime(2023, 5, 1, 0, 0, 0)
END_TIME = datetime.datetime(2023, 5, 3, 10, 0, 0)
TIME_STEP = datetime.timedelta(minutes=15)

SITES = ["fsmi", "gill", "luck", "rabb"]
OUTPUT_DIR = "./oval_images/global/"
CANVAS_WIDTH = 3600
CANVAS_HEIGHT = 1800

os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- 3. Helper Functions ---
def parse_dt(dt):
    if dt is None: return None
    if isinstance(dt, str):
        try: return datetime.datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except Exception: return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def lonlat_to_pixel(lon, lat, width, height):
    lon_norm = np.where(lon > 180.0, lon - 360.0, lon)
    x = np.floor((lon_norm + 180.0) / 360.0 * (width - 1)).astype(int)
    y = np.floor((90.0 - lat) / 180.0 * (height - 1)).astype(int)
    return x, y

def sanitize_grid(arr):
    if arr is None: return None
    arr = np.squeeze(np.array(arr))
    if arr.ndim == 3: arr = arr[0]
    return arr

def normalize_image_shape(img):
    """Standardizes 2D, 3D, or 4D camera images into (H, W, C) format."""
    if img is None:
        return None
    img = np.squeeze(np.array(img))
    
    # Flatten extra slice/burst dimensions if present (e.g. 4D -> 3D)
    while img.ndim > 3:
        img = img[..., 0]
        
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

# --- 4. Build Timestamps & Setup PyAuroraX ---
target_times = []
curr = START_TIME
while curr <= END_TIME:
    target_times.append(curr)
    curr += TIME_STEP

aurorax = pyaurorax.PyAuroraX()
cam_dataset = aurorax.data.ucalgary.get_dataset("TREX_RGB_RAW_NOMINAL")
skymap_dataset = aurorax.data.ucalgary.get_dataset("TREX_RGB_SKYMAP_IDLSAV")

# --- 5. Pre-Load Skymaps ---
print("Pre-loading skymaps...")
skymaps = {}
for site in SITES:
    glat, glon, elev = None, None, None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
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
        print(f"  [!] Failed to load skymap for {site}: {e}")

    if glat is not None:
        skymaps[site] = (glat, glon, elev)
        print(f"  [✓] Loaded skymap for {site}")
    else:
        print(f"  [!] Missing coords for {site}")

# --- 6. Processing Loop (Matching OpenSpace YYYY-MM-DD-HR-MN format) ---
for t_target in target_times:
    # OpenSpace format: YYYY-MM-DD-HR-MN (e.g. 2023-05-01-00-15)
    time_str = t_target.strftime("%Y-%m-%d-%H-%M")
    out_path = os.path.join(OUTPUT_DIR, f"mosaic_{time_str}.png")
    
    # Skip if file already exists and is non-empty
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        print(f"Skipping {time_str} - Valid image already exists.")
        continue
        
    print(f"\nProcessing {t_target}...")
    canvas = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH, 4), dtype=np.uint8)
    rendered_pixels = 0

    for site in SITES:
        if site not in skymaps:
            continue
            
        glat, glon, elev = skymaps[site]
        
        # Download 1-minute slice with warning suppression
        downloaded_files = []
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                dl_res = aurorax.data.ucalgary.download(
                    "TREX_RGB_RAW_NOMINAL",
                    start=t_target,
                    end=t_target + datetime.timedelta(seconds=59),
                    site_uid=site
                )
            files = getattr(dl_res, 'filenames', dl_res)
            if files:
                if isinstance(files, (list, tuple)): downloaded_files.extend(files)
                else: downloaded_files.append(files)
        except Exception:
            continue
            
        if not downloaded_files:
            continue
            
        # Read the file(s)
        try:
            cam_read = aurorax.data.ucalgary.read(dataset=cam_dataset, file_list=downloaded_files)
            raw_images = getattr(cam_read, 'data', getattr(cam_read, 'images', []))
            raw_ts = getattr(cam_read, 'timestamps', getattr(cam_read, 'timestamp', []))
            
            # Parse timestamps
            if not isinstance(raw_ts, (list, tuple, np.ndarray)):
                raw_ts = [raw_ts] if raw_ts else []
            ts_list = [parse_dt(t) for t in raw_ts]
            
            # Handle single-frame arrays without splitting along row axes
            if isinstance(raw_images, np.ndarray):
                if len(ts_list) == 1 and raw_images.shape[0] != 1:
                    frame_list = [raw_images]
                elif len(ts_list) == 1 and raw_images.ndim > 2 and raw_images.shape[0] == 1:
                    frame_list = list(raw_images)
                elif len(ts_list) > 1 and raw_images.shape[0] == len(ts_list):
                    frame_list = list(raw_images)
                else:
                    frame_list = [raw_images]
            else:
                frame_list = list(raw_images) if hasattr(raw_images, '__len__') else [raw_images]
                
        except Exception:
            continue
            
        if not frame_list:
            continue
            
        # Match nearest frame to target timestamp
        match_idx = None
        if ts_list and len(ts_list) == len(frame_list):
            best_diff = float('inf')
            for i, t in enumerate(ts_list):
                if t is not None:
                    diff = abs((t - t_target).total_seconds())
                    if diff <= 450 and diff < best_diff:
                        best_diff = diff
                        match_idx = i
                        
        if match_idx is None and len(frame_list) > 0:
            match_idx = 0
            
        if match_idx is None or match_idx >= len(frame_list):
            continue
            
        # Extract and normalize the matched frame
        try:
            raw_frame = frame_list[match_idx]
            if raw_frame is None:
                continue
            
            img = np.array(raw_frame)
            if img.size == 0 or img.ndim < 2:
                continue
                
            img_norm = normalize_image_shape(img)
        except Exception:
            continue

        # Spatial dimension matching
        min_h = min(glat.shape[0], glon.shape[0], elev.shape[0], img_norm.shape[0])
        min_w = min(glat.shape[1], glon.shape[1], elev.shape[1], img_norm.shape[1])
        if min_h == 0 or min_w == 0:
            continue

        glat_c = glat[:min_h, :min_w]
        glon_c = glon[:min_h, :min_w]
        elev_c = elev[:min_h, :min_w]
        img_c  = img_norm[:min_h, :min_w, :]

        # Only process valid pixels above 15 deg elevation
        mask = (elev_c > 15) & (glat_c > 0) & (~np.isnan(glat_c)) & (~np.isnan(glon_c))
        if not np.any(mask):
            continue

        # Map coordinates to canvas
        x_pix, y_pix = lonlat_to_pixel(glon_c[mask], glat_c[mask], CANVAS_WIDTH, CANVAS_HEIGHT)
        valid_idx = (x_pix >= 0) & (x_pix < CANVAS_WIDTH) & (y_pix >= 0) & (y_pix < CANVAS_HEIGHT)
        x_val, y_val = x_pix[valid_idx], y_pix[valid_idx]
        
        if len(x_val) == 0:
            continue

        colors = img_c[mask][valid_idx]
        
        # Ensure 2D color matrix of shape (N, 3)
        if colors.ndim == 1:
            colors = np.column_stack([colors] * 3)
        elif colors.ndim > 2:
            while colors.ndim > 2:
                colors = colors[..., 0]
        
        if colors.shape[1] == 1:
            colors = np.hstack([colors, colors, colors])
        elif colors.shape[1] > 3:
            colors = colors[:, :3]

        # Normalize pixel intensities to 8-bit range
        if colors.dtype != np.uint8:
            c_min, c_max = colors.min(), colors.max()
            if c_max > c_min:
                colors = ((colors.astype(np.float32) - c_min) / (c_max - c_min) * 255.0).astype(np.uint8)
            else:
                colors = colors.astype(np.uint8)

        alphas = np.full((len(colors), 1), 255, dtype=np.uint8)
        rgba = np.hstack((colors, alphas))

        # Project pixels onto canvas
        canvas[y_val, x_val] = rgba
        rendered_pixels += len(x_val)

    # Always save the PNG (blank if no pixels were rendered)
    Image.fromarray(canvas, 'RGBA').save(out_path)
    if rendered_pixels > 0:
        print(f"  [✓] Saved {out_path} ({rendered_pixels} pixels)")
    else:
        print(f"  [✓] Saved blank image {out_path} (0 pixels)")

# --- 7. Generate OpenSpace Asset File ---
start_date_str = START_TIME.strftime("%Y%m%d")
end_date_str = END_TIME.strftime("%Y%m%d")
asset_filename = f"aurora_mosaic_{start_date_str}_{end_date_str}.asset"

start_iso = START_TIME.strftime("%Y-%m-%dT%H:%M:%S")
end_iso = END_TIME.strftime("%Y-%m-%dT%H:%M:%S")

step_minutes = int(TIME_STEP.total_seconds() // 60)
temporal_res = f"{step_minutes}m" if step_minutes < 60 else f"{int(step_minutes // 60)}h"

lua_image_path = OUTPUT_DIR.rstrip("/") + "/"

asset_content = f"""local globe = asset.require("scene/solarsystem/planets/earth/globe")

-- Point this to the directory where your Python script saved the PNGs
local imagePath = asset.resource("{lua_image_path}")

local AuroraLayer = {{
    Identifier = "Aurora",
    Name = "AuroraX Mosaic",
    Enabled = asset.enabled,
    ZIndex = 9005, -- High ZIndex to render on top of clouds and surface textures
    Type = "TemporalTileProvider",
    Mode = "Prototyped",
    Prototyped = {{
        Time = {{
            Start = "{start_iso}",
            End = "{end_iso}"
        }},
        TemporalResolution = "{temporal_res}",
        TimeFormat = "YYYY-MM-DD-HR-MN",
        Prototype = imagePath .. "mosaic_${{OpenSpaceTimeId}}.png"
    }},
    Description = "AuroraX All-Sky Camera Mosaic",
    Opacity = 1.0
}}

asset.onInitialize(function()
    openspace.globebrowsing.addLayer(globe.Earth.Identifier, "ColorLayers", AuroraLayer)
end)

asset.onDeinitialize(function()
    openspace.globebrowsing.deleteLayer(globe.Earth.Identifier, "ColorLayers", AuroraLayer)
end)

asset.export("Prototype", AuroraLayer)
"""

with open(asset_filename, "w") as f:
    f.write(asset_content)

print(f"\n[✓] OpenSpace asset file written to: {asset_filename}")