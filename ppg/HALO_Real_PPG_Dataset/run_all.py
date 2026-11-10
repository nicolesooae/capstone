#!/usr/bin/env python3
"""Batch-process every prepared subject, retaining per-subject outputs."""
from pathlib import Path
import subprocess
import sys
import csv
root = Path(__file__).resolve().parent
files = sorted((root / "prepared").glob("PPG_subj_*_forehead.csv"))
if not files:
    sys.exit("No prepared data found. Run python download_and_prepare.py first.")
summary = []
for f in files:
    subject = f.stem.replace("PPG_subj_", "").replace("_forehead", "")
    print(f"\n===== Subject {subject} =====", flush=True)
    try:
        subprocess.run([sys.executable, str(root / "run_one.py"), subject], check=True)
        summary.append((subject, "OK"))
    except subprocess.CalledProcessError as e:
        summary.append((subject, f"FAILED (exit {e.returncode})"))
with (root / "batch_status.csv").open("w", newline="") as out:
    w = csv.writer(out)
    w.writerow(["subject", "pipeline_status"])
    w.writerows(summary)
print("\nSaved batch_status.csv. A successful run does not establish clinical accuracy.")
