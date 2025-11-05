from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import subprocess
from zoneinfo import ZoneInfo

def run_one(args, site):
    with open(f"{args.log_path}logs/{site}.log", "w") as log:
        if args.start_date is not None and args.end_date is not None:
            return subprocess.run(
                    ["python",
                     "ingest-wxt.py",
                     "--outdir",
                     args.outdir,
                     "--start_date",
                     args.start_date,
                     "--end_date",
                     args.end_date,
                     "--site",
                     site],
                stdout=log, stderr=subprocess.STDOUT, check=False
            ).returncode, site
        else:
            return subprocess.run(
                    ["python",
                     "ingest-wxt.py",
                     "--outdir",
                     args.outdir,
                     "--site",
                     site],
                stdout=log, stderr=subprocess.STDOUT, check=False
            ).returncode, site


def main(args):
    MAX_WORKERS = 6

    print("\nStarting Mass-WXT Ingest: ", datetime.now(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:00Z'))
    print("\n")

    # Define a set of days to process
    SITES = ["NEIU",
             "NU", 
             "CSU", 
             "ATMOS",
             "UIC",
             "NEIU_CCICS",
             "BIG",
             "HUM",
             "DOWN",
             "SHEDD",
             "VLPK"]

    # Ensure a log directory exists
    Path(args.log_path + "logs").mkdir(exist_ok=True)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(run_one, args, site) for site in SITES]
        for fut in as_completed(futures):
            rc, d = fut.result()
            print(f"{d}: {'OK' if rc==0 else f'FAILED (rc={rc})'}")
    
    print("Finished Mass-WXT Ingest: ", datetime.now(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:00Z'))

if __name__ == '__main__':
    #Parsing the command line
    descript = ("Mass Ingest for CROCUS WXT Data (b-level dataset)")
    parser = argparse.ArgumentParser(description=descript,
                                             usage=(
            "python mass-ingest-wxt.py --outdir /user/dev/crocus/"
        )
    )

    parser.add_argument("--start_date",
                        dest='start_date',
                        default=None,
                        help="[Default|None] Date to pass to ingest-wxt.py in YYYY-MM-DD format"
    )

    parser.add_argument("--end_date",
                        dest='end_date',
                        default=None,
                        help="[Default|None] Date to pass to ingest-wxt.py in YYYY-MM-DD format"
    )

    parser.add_argument('--outdir',
                        type=str,
                        dest="outdir",
                        default="./",
                        help='[Default|Current Directory] Output directory to pass to ingest-wxt.py b-level files.')
    
    parser.add_argument("--log_path",
                        dest='log_path',
                        default="./",
                        help="[Default|Current Directory] Output directory for logs"
    )

    args = parser.parse_args()

    main(args)