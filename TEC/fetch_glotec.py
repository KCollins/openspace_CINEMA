from datetime import datetime, timedelta
import os
import re
import sys
import tempfile
import traceback
import matplotlib
import numpy as np
import requests
import xarray as xr

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_URL = "https://services.swpc.noaa.gov/products/glotec/netcdf_2d_urt/"
BASE_DIR = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in globals()
    else os.getcwd()
)
OUT_DIR = os.path.join(BASE_DIR, "glotec_frames")
TMP_DIR = tempfile.gettempdir()
os.makedirs(OUT_DIR, exist_ok=True)


def parse_file_date(fname):
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", fname)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def get_frame_timestamp(da, time_dim, t_idx, base_date):
    if time_dim in da.coords:
        t_val = da[time_dim].values[t_idx]
        if np.issubdtype(np.asarray(t_val).dtype, np.datetime64):
            return np.datetime_as_string(t_val, unit="s").replace(":", "-")

    if base_date is not None:
        # 72 daily steps = 20-minute cadence starting at 00:00:00 UTC
        dt = base_date + timedelta(minutes=20 * t_idx)
        return dt.strftime("%Y-%m-%dT%H-%M-%S")

    return None


def sync_glotec(max_files=3):  # 3 daily files = 216 frames at 20-min cadence
    print(f"Output folder: {OUT_DIR}")
    print(f"Fetching directory listing from {BASE_URL} ...")
    resp = requests.get(BASE_URL, timeout=15)
    resp.raise_for_status()

    raw_links = re.findall(
        r'href=["\']([^"\']+\.nc)["\']', resp.text, re.IGNORECASE
    )
    files = sorted(set(link.split("/")[-1] for link in raw_links))[-max_files:]
    print(f"Found {len(files)} daily NetCDF files to process.")

    for fname in files:
        base_date = parse_file_date(fname)
        nc_path = os.path.join(TMP_DIR, fname)

        print(f"Downloading {fname} ...")
        with requests.get(BASE_URL + fname, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(nc_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        try:
            ds = xr.open_dataset(nc_path)
        except ValueError as e:
            if "netcdf4" in str(e).lower() or "h5netcdf" in str(e).lower():
                raise RuntimeError(
                    f"Missing NetCDF backend for {sys.executable}.\nRun:"
                    f' "{sys.executable}" -m pip install h5netcdf netCDF4'
                ) from e
            raise

        try:
            var_name = next(
                (v for v in ds.data_vars if v.lower() == "tec"),
                next(
                    (v for v in ds.data_vars if "tec" in v.lower()),
                    list(ds.data_vars)[0],
                ),
            )
            da = ds[var_name]

            # Identify lon, lat, and time dimensions (handles named or phony_dim_* axes)
            lon_dim = next((d for d in da.dims if "lon" in d.lower()), None)
            lat_dim = next((d for d in da.dims if "lat" in d.lower()), None)
            time_dim = next((d for d in da.dims if "time" in d.lower()), None)

            if da.ndim == 3 and (
                lon_dim is None or lat_dim is None or time_dim is None
            ):
                # Standard GloTEC shape is (144, 72, 72) -> (lon, lat, time)
                lon_dim = lon_dim or next(
                    (d for d in da.dims if da.sizes[d] == 144), da.dims[0]
                )
                rem = [d for d in da.dims if d != lon_dim]
                lat_dim = lat_dim or rem[0]
                time_dim = time_dim or rem[1]

            # Wrap longitude from [0, 360] to [-180, 180] if needed
            if lon_dim in da.coords and float(da[lon_dim].max()) > 180:
                da = da.assign_coords(
                    {lon_dim: (((da[lon_dim] + 180) % 360) - 180)}
                ).sortby(lon_dim)

            n_steps = da.sizes[time_dim] if time_dim else 1
            created = 0

            for t_idx in range(n_steps):
                iso_stamp = get_frame_timestamp(da, time_dim, t_idx, base_date)
                if not iso_stamp:
                    continue

                out_path = os.path.join(OUT_DIR, f"{iso_stamp}.png")
                if os.path.exists(out_path):
                    continue

                slice_da = da.isel({time_dim: t_idx}) if time_dim else da
                slice_da = slice_da.transpose(lat_dim, lon_dim)
                tec = slice_da.values

                # Flip vertically if latitude runs South-to-North so North is at top
                if lat_dim in slice_da.coords:
                    if float(slice_da[lat_dim][0]) < float(
                        slice_da[lat_dim][-1]
                    ):
                        tec = np.flipud(tec)
                else:
                    tec = np.flipud(tec)

                fig = plt.figure(figsize=(10, 5), dpi=150)
                ax = plt.Axes(fig, [0.0, 0.0, 1.0, 1.0])
                ax.set_axis_off()
                fig.add_axes(ax)

                ax.imshow(tec, cmap="plasma", vmin=0, vmax=100, aspect="auto")
                plt.savefig(out_path, transparent=True)
                plt.close(fig)
                created += 1

            print(f"Exported {created} PNG frames from {fname}.")
        finally:
            ds.close()
            if os.path.exists(nc_path):
                os.remove(nc_path)

    print("Sync complete.")


if __name__ == "__main__":
    try:
        sync_glotec()
    except Exception:
        traceback.print_exc()
        if "__file__" in globals():
            input("\nPress Enter to exit...")