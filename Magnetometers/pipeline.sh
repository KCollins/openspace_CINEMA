# 1. Fetch 3D baseline-subtracted perturbation data
python fetch_gannon_storm.py

# 2. Process into 3D station vector needles
python process_station_vectors.py --input gannon_storm_20240510.nc

# 3. Inspect sample frame
python inspect_osfls.py osfls_geomag/2024-05-11T00-35-00-000.osfls