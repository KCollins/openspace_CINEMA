import sys
import os
import xarray as xr

def inspect_netcdf(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' does not exist.")
        return

    print("=" * 60)
    print(f"INSPECTING NETCDF: {file_path}")
    print("=" * 60)

    try:
        ds = xr.open_dataset(file_path)
        
        print("\n--- DATA VARIABLES ---")
        if ds.data_vars:
            for name, var in ds.data_vars.items():
                print(f"  • Name: {name:<20} Dtype: {str(var.dtype):<10} Dims: {var.dims} Shape: {var.shape}")
                if var.attrs:
                    print(f"    Attributes: {dict(var.attrs)}")
        else:
            print("  (No data variables found)")

        print("\n--- COORDINATES ---")
        if ds.coords:
            for name, coord in ds.coords.items():
                print(f"  • Name: {name:<20} Dtype: {str(coord.dtype):<10} Dims: {coord.dims} Shape: {coord.shape}")
        else:
            print("  (No coordinates found)")

        print("\n--- DIMENSIONS ---")
        for dim, size in ds.dims.items():
            print(f"  • {dim}: {size}")

        print("\n--- GLOBAL ATTRIBUTES ---")
        if ds.attrs:
            for k, v in ds.attrs.items():
                print(f"  • {k}: {v}")
        else:
            print("  (No global attributes found)")

        print("\n--- XARRAY DATASET SUMMARY ---")
        print(ds)

        ds.close()

    except Exception as e:
        print(f"Error reading NetCDF file: {e}")

if __name__ == "__main__":
    target_file = sys.argv[1] if len(sys.argv) > 1 else "gannon_storm_20240510.nc"
    inspect_netcdf(target_file)
