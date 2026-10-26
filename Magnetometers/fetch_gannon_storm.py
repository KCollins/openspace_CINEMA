import os
import sys
import argparse
import numpy as np
import pandas as pd
import xarray as xr
from hapiclient import hapi

SERVER = "https://cdaweb.gsfc.nasa.gov/hapi"
START_TIME = "2024-05-10T00:00:00Z"
END_TIME = "2024-05-12T00:00:00Z"
DEFAULT_OUTPUT_NC = "gannon_storm_20240510.nc"

# Expanded station network (EZIE-Mag, THEMIS GMAG, CARISMA, CANMOS, USGS, Greenland)
STATIONS = {
    # --- Alaska & High-Latitude Electrojet (EZIE Core Region) ---
    "AET":  {"dataset": "THG_L2_MAG_AET",  "lat": 63.81, "lon": -149.00}, # Healy, AK, USA
    "BET":  {"dataset": "THG_L2_MAG_BET",  "lat": 60.79, "lon": -161.77}, # Bethel, AK, USA
    "FYKN": {"dataset": "THG_L2_MAG_FYKN", "lat": 66.56, "lon": -145.21}, # Fort Yukon, AK, USA
    "MCGR": {"dataset": "THG_L2_MAG_MCGR", "lat": 62.95, "lon": -155.60}, # McGrath, AK, USA
    "TALK": {"dataset": "THG_L2_MAG_TALK", "lat": 62.30, "lon": -150.10}, # Talkeetna, AK, USA
    "SIT":  {"dataset": "THG_L2_MAG_SIT",  "lat": 57.06, "lon": -135.33}, # Sitka, AK, USA

    # --- Canada Auroral Electrojet ---
    "FSIM": {"dataset": "THG_L2_MAG_FSIM", "lat": 61.76, "lon": -121.23}, # Fort Simpson, CA
    "YKC":  {"dataset": "THG_L2_MAG_YKC",  "lat": 62.48, "lon": -114.48}, # Yellowknife, CA
    "PINE": {"dataset": "THG_L2_MAG_PINE", "lat": 58.67, "lon": -111.15}, # Pine Ridge, CA
    "SNAP": {"dataset": "THG_L2_MAG_SNAP", "lat": 63.60, "lon": -110.87}, # Snap Lake, CA
    "GILL": {"dataset": "THG_L2_MAG_GILL", "lat": 56.38, "lon": -94.64},  # Gillam, CA
    "ISLL": {"dataset": "THG_L2_MAG_ISLL", "lat": 53.86, "lon": -94.66},  # Island Lake, CA
    "RANK": {"dataset": "THG_L2_MAG_RANK", "lat": 62.82, "lon": -92.11},  # Rankin Inlet, CA
    "KAPU": {"dataset": "THG_L2_MAG_KAPU", "lat": 49.40, "lon": -82.42},  # Kapuskasing, CA
    "OTTA": {"dataset": "THG_L2_MAG_OTTA", "lat": 45.40, "lon": -75.55},  # Ottawa, CA
    "STJ":  {"dataset": "THG_L2_MAG_STJ",  "lat": 47.60, "lon": -52.68},  # St. John's, CA

    # --- Mid-Latitude US Mainland ---
    "NEW":  {"dataset": "THG_L2_MAG_NEW",  "lat": 48.26, "lon": -117.12}, # Newport, WA, USA
    "FRN":  {"dataset": "THG_L2_MAG_FRN",  "lat": 37.09, "lon": -119.72}, # Fresno, CA, USA
    "BOU":  {"dataset": "THG_L2_MAG_BOU",  "lat": 40.14, "lon": -105.24}, # Boulder, CO, USA
    "TUC":  {"dataset": "THG_L2_MAG_TUC",  "lat": 32.17, "lon": -110.73}, # Tucson, AZ, USA

    # --- Atlantic & Greenland ---
    "GHB":  {"dataset": "THG_L2_MAG_GHB",  "lat": 64.17, "lon": -51.73},  # Nuuk, Greenland
    "NAQ":  {"dataset": "THG_L2_MAG_NAQ",  "lat": 61.16, "lon": -45.44},  # Narsarsuaq, Greenland
    "SAN":  {"dataset": "THG_L2_MAG_SAN",  "lat": 18.11, "lon": -66.15},  # San Juan, PR
}

def fetch_gannon_storm_netcdf(output_nc=DEFAULT_OUTPUT_NC, force=False):
    # 1. Check if output file already exists
    if os.path.exists(output_nc) and not force:
        print(f"[SKIP] NetCDF file already exists at: {os.path.abspath(output_nc)}")
        print("       Use --force flag if you want to re-download and overwrite it.")
        return

    print(f"Fetching 3-Component Magnetometer Data ({START_TIME} to {END_TIME})...")
    
    time_grid_pd = pd.date_range(start=START_TIME, end=END_TIME, freq="1min", inclusive="left").tz_localize(None)
    time_grid_np = time_grid_pd.values.astype("datetime64[ns]")

    station_names, station_lats, station_lons = [], [], []
    station_db_n, station_db_e, station_db_z = [], [], []

    for stn_id, info in STATIONS.items():
        dataset_id = info["dataset"]
        print(f"  -> Pulling station {stn_id} ({dataset_id})...")
        
        try:
            data, meta = hapi(SERVER, dataset_id, "", START_TIME, END_TIME)
            raw_times = [t.decode('utf-8') if isinstance(t, bytes) else t for t in data['Time']]
            times_pd = pd.to_datetime(raw_times).tz_localize(None)
            
            mag_var_name = f"thg_mag_{stn_id.lower()}"
            mag_vec = data[mag_var_name] if mag_var_name in data.dtype.names else data[list(data.dtype.names)[1]]
            
            # Extract 3 vector components: North (0), East (1), Vertical Down (2)
            bn_raw = mag_vec[:, 0]
            be_raw = mag_vec[:, 1]
            bz_raw = mag_vec[:, 2]

            # Align on uniform 1-minute time grid
            df = pd.DataFrame({"B_N": bn_raw, "B_E": be_raw, "B_Z": bz_raw}, index=times_pd)
            df = df[~df.index.duplicated(keep='first')].reindex(time_grid_pd).interpolate(method="linear").bfill().ffill()

            # Baseline subtraction (first 3 hours quiet-time mean) to isolate storm perturbation dB
            baseline_n = df["B_N"].iloc[:180].mean()
            baseline_e = df["B_E"].iloc[:180].mean()
            baseline_z = df["B_Z"].iloc[:180].mean()

            db_n = df["B_N"].values - baseline_n
            db_e = df["B_E"].values - baseline_e
            db_z = df["B_Z"].values - baseline_z

            station_names.append(stn_id)
            station_lats.append(info["lat"])
            station_lons.append(info["lon"])
            station_db_n.append(db_n)
            station_db_e.append(db_e)
            station_db_z.append(db_z)
            print(f"     [SUCCESS] {stn_id}: Max perturbation = {np.nanmax(np.sqrt(db_n**2 + db_e**2 + db_z**2)):.1f} nT")

        except Exception as e:
            print(f"     [WARNING] Could not retrieve {stn_id}: {e}")

    if not station_db_n:
        raise RuntimeError("Failed to retrieve data from all requested stations.")

    print(f"\nConstructing NetCDF with {len(station_names)} active stations...")
    ds = xr.Dataset(
        data_vars={
            "dB_N": (["time", "station"], np.column_stack(station_db_n), {"units": "nT", "long_name": "Northward Perturbation"}),
            "dB_E": (["time", "station"], np.column_stack(station_db_e), {"units": "nT", "long_name": "Eastward Perturbation"}),
            "dB_Z": (["time", "station"], np.column_stack(station_db_z), {"units": "nT", "long_name": "Vertical Downward Perturbation"}),
        },
        coords={
            "time": ("time", time_grid_np),
            "lat": ("station", station_lats, {"units": "degrees_north"}),
            "lon": ("station", station_lons, {"units": "degrees_east"}),
            "station_name": ("station", station_names),
        },
        attrs={
            "title": "3D Ground Magnetometer Perturbations during the May 2024 Gannon Storm",
            "source": "NASA CDAWeb / EZIE-Mag & THEMIS GMAG Network",
        }
    )

    ds.to_netcdf(output_nc)
    print(f"Successfully generated: {os.path.abspath(output_nc)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch ground magnetometer dataset for OpenSpace processing.")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_NC, help="Output NetCDF path.")
    parser.add_argument("--force", action="store_true", help="Force re-download even if .nc file exists.")
    args = parser.parse_args()

    fetch_gannon_storm_netcdf(output_nc=args.output, force=args.force)