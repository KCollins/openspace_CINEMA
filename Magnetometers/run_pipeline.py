import os
import argparse
import subprocess
import sys

def run_command(cmd_list):
    print(f"\n>>> Running: {' '.join(cmd_list)}", flush=True)
    res = subprocess.run(cmd_list)
    if res.returncode != 0:
        print(f"ERROR: Command failed with return code {res.returncode}")
        sys.exit(res.returncode)

def main():
    parser = argparse.ArgumentParser(description="Master OpenSpace Geomagnetic Vector Generation Pipeline.")
    parser.add_argument("--start", type=str, default="2024-05-10T00:00:00", help="Start UTC date/time")
    parser.add_argument("--end", type=str, default="2024-05-12T00:00:00", help="End UTC date/time")
    parser.add_argument("--year", type=int, default=2024, help="Year for EZIE-Mag dataset")
    parser.add_argument("--month", type=int, default=5, help="Month for EZIE-Mag dataset")
    parser.add_argument("--force", action="store_true", help="Force re-download and re-processing")
    args = parser.parse_args()

    force_flag = ["--force"] if args.force else []

    print("=================================================================")
    print(" PIPELINE PART 1: General Geomagnetic Network Processing")
    print("=================================================================")
    geomag_nc = "geomag_data.nc"
    run_command([sys.executable, "fetch_cdaweb_mag.py", "--start", args.start, "--end", args.end, "--output", geomag_nc] + force_flag)
    run_command([sys.executable, "process_station_vectors.py", "--input", geomag_nc, "--outdir", "osfls_geomag"] + force_flag)

    print("\n=================================================================")
    print(" PIPELINE PART 2: JHU/APL EZIE-Mag Network Processing")
    print("=================================================================")
    eziemag_nc = "eziemag_data.nc"
    run_command([sys.executable, "fetch_eziemag.py", "--year", str(args.year), "--month", str(args.month), "--start", args.start, "--end", args.end, "--output", eziemag_nc] + force_flag)
    run_command([sys.executable, "process_station_vectors.py", "--input", eziemag_nc, "--outdir", "osfls_eziemag", "--eic"] + force_flag)

    print("\n=================================================================")
    print(" PIPELINE COMPLETE!")
    print("=================================================================")

if __name__ == "__main__":
    main()