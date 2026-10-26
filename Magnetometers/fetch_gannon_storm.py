import os
import numpy as np
import pandas as pd
import xarray as xr
from hapiclient import hapi

SERVER = "https://cdaweb.gsfc.nasa.gov/hapi"
START_TIME = "2024-05-10T00:00:00Z"
END_TIME = "2024-05-12T00:00:00Z"
OUTPUT_NC = "gannon_storm_20240510.nc"

# Active THEMIS GMAG station endpoints on CDAWeb HAPI
STATIONS = {
    "FSIM": {"dataset": "THG_L2_MAG_FSIM", "lat": 61.76, "lon": -121.23},
    "YKC":  {"dataset": "THG_L2_MAG_YKC",  "lat": 62.48, "lon": -114.48},
    "PINE": {"dataset": "THG_L2_MAG_PINE", "lat": 58.67, "lon": -111.15},
    "KAPU": {"dataset": "THG_L2_MAG_KAPU", "lat": 49.40, "lon": -82.42},
    "FRN":  {"dataset": "THG_L2_MAG_FRN",  "lat": 37.09, "lon": -119.72},
    "BOU":  {"dataset": "THG_L2_MAG_BOU",  "lat": 40.14, "lon": -105.24},
    "AET":  {"dataset": "THG_L2_MAG_AET",  "lat": 63.81, "lon": -149.00},
}

def fetch_gannon_storm_netcdf():
    print(f"Fetching Ground Magnetometer Data ({START_TIME} to {END_TIME})...")
    
    time_grid_pd = pd.date_range(start=START_TIME, end=END_TIME, freq="1min", inclusive="left").tz_localize(None)
    time_grid_np = time_grid_pd.values.astype("datetime64[ns]")

    station_names, station_lats, station_lons, station_data_n, station_data_e = [], [], [], [], []

    for stn_id, info in STATIONS.items():
        dataset_id = info["dataset"]
        print(f"  -> Pulling station {stn_id} ({dataset_id})...")
        
        try:
            data, meta = hapi(SERVER, dataset_id, "", START_TIME, END_TIME)
            raw_times = [t.decode('utf-8') if isinstance(t, bytes) else t for t in data['Time']]
            times_pd = pd.to_datetime(raw_times).tz_localize(None)
            
            mag_var_name = f"thg_mag_{stn_id.lower()}"
            mag_vec = data[mag_var_name] if mag_var_name in data.dtype.names else data[list(data.dtype.names)[1]]
            
            df = pd.DataFrame({"B_N": mag_vec[:, 0], "B_E": mag_vec[:, 1]}, index=times_pd)
            df = df[~df.index.duplicated(keep='first')]
            df_reindexed = df.reindex(time_grid_pd).interpolate(method="linear").bfill().ffill()

            station_names.append(stn_id)
            station_lats.append(info["lat"])
            station_lons.append(info["lon"])
            station_data_n.append(df_reindexed["B_N"].values)
            station_data_e.append(df_reindexed["B_E"].values)
            print(f"     [SUCCESS] Retrieved {len(df_reindexed)} samples for {stn_id}.")

        except Exception as e:
            print(f"     [WARNING] Could not retrieve station {stn_id}: {e}")

    if not station_data_n:
        raise RuntimeError("Failed to download data from all requested magnetometer stations.")

    bn_matrix = np.column_stack(station_data_n)
    be_matrix = np.column_stack(station_data_e)

    ds = xr.Dataset(
        data_vars={
            "B_N": (["time", "station"], bn_matrix, {"units": "nT"}),
            "B_E": (["time", "station"], be_matrix, {"units": "nT"}),
        },
        coords={
            "time": ("time", time_grid_np),
            "lat": ("station", station_lats, {"units": "degrees_north"}),
            "lon": ("station", station_lons, {"units": "degrees_east"}),
            "station_name": ("station", station_names),
        },
        attrs={"title": "Gannon Storm Ground Magnetometer Perturbations"}
    )

    ds.to_netcdf(OUTPUT_NC)
    print(f"\nSuccessfully created: {os.path.abspath(OUTPUT_NC)}")

if __name__ == "__main__":
    fetch_gannon_storm_netcdf()