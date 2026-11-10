#!/usr/bin/env python3
"""Download the original Vasquez Salazar & Argüello-Prada MAX30102 PPG CSV recordings.

Outputs compatible files for ../halo_ppg.py. Data is downloaded from the original
public GitHub repository when you run this script, not embedded in this bundle.
"""
from pathlib import Path
from urllib.request import urlopen, Request
import csv
import io
import json
import sys
import time

OWNER_REPO = "sanvsquezsz/PPG-based-BGL-assessment"
API = f"https://api.github.com/repos/{OWNER_REPO}/contents/Dataset/PPG_csv"
RAW_BASE = f"https://raw.githubusercontent.com/{OWNER_REPO}/main/Dataset/PPG_csv"
ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT / "original"
PREPARED = ROOT / "prepared"
FS_HZ = 40  # Reported nominal rate in the source README


def get(url):
    req = Request(url, headers={"User-Agent": "HALO-PPG-Research/1.0", "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=45) as response:
        return response.read()


def main():
    ORIGINAL.mkdir(parents=True, exist_ok=True)
    PREPARED.mkdir(parents=True, exist_ok=True)
    print("Retrieving published subject manifest from GitHub...")
    try:
        manifest = json.loads(get(API))
    except Exception as e:
        sys.exit(f"Unable to reach GitHub: {e}\nCheck your internet connection or try again.")
    files = sorted(x["name"] for x in manifest if x.get("type") == "file" and x["name"].startswith("PPG_subj_") and x["name"].endswith(".csv"))
    if not files:
        sys.exit("No matching subject CSVs were found. GitHub repository structure may have changed.")

    results = []
    for idx, name in enumerate(files, 1):
        original = ORIGINAL / name
        prepared = PREPARED / name.replace(".csv", "_forehead.csv")
        print(f"[{idx}/{len(files)}] {name}", flush=True)
        try:
            if not original.exists() or original.stat().st_size == 0:
                original.write_bytes(get(f"{RAW_BASE}/{name}"))
            with original.open("r", newline="", encoding="utf-8-sig") as inp:
                reader = csv.DictReader(inp)
                needed = {"Timestamp", "PPG_Forehead", "PPG_Earlobe", "PPG_Finger"}
                if not needed.issubset(set(reader.fieldnames or [])):
                    raise ValueError(f"Unexpected columns: {reader.fieldnames}")
                n = 0
                with prepared.open("w", newline="", encoding="utf-8") as out:
                    writer = csv.writer(out)
                    # The supplied Timestamp increments do not reliably reflect 40 Hz.
                    # Do NOT pass it to halo_ppg.py as time_s without verification.
                    writer.writerow(["ir", "source_timestamp", "earlobe_source", "finger_source"])
                    for row in reader:
                        ir = float(row["PPG_Forehead"])
                        writer.writerow([ir, row["Timestamp"], row["PPG_Earlobe"], row["PPG_Finger"]])
                        n += 1
            results.append((name, n, n / FS_HZ))
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
        time.sleep(0.05)

    with (ROOT / "download_manifest.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_filename", "samples", "duration_s_at_nominal_40hz"])
        w.writerows(results)
    print(f"\nDownloaded and prepared {len(results)}/{len(files)} recordings.")
    print(f"Original CSVs: {ORIGINAL}")
    print(f"Prepared forehead CSVs: {PREPARED}")
    print("NOTE: 'ir' is a compatibility label ONLY; dataset does not confirm optical wavelength per column.")
    print("NOTE: source timestamps retained for inspection, not used for signal timing.")
    print("Next run: python run_one.py 01")


if __name__ == "__main__":
    main()
