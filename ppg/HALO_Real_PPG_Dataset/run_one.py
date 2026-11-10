#!/usr/bin/env python3
"""Run user's existing ../halo_ppg.py on a real forehead CSV."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parent
script = project / "halo_ppg.py"
if not script.exists():
    sys.exit(f"Can't find {script}. Put this folder directly inside your existing halo_ppg folder.")
subject = sys.argv[1] if len(sys.argv) > 1 else "01"
if not subject.isdigit():
    sys.exit("Specify a numeric subject ID, for example: python run_one.py 01")
subject = subject.zfill(2)
source = root / "prepared" / f"PPG_subj_{subject}_forehead.csv"
if not source.exists():
    sys.exit(f"Can't find {source}. Run python download_and_prepare.py first.")
out = root / "results" / f"subject_{subject}"
cmd = [sys.executable, str(script), "--csv", str(source), "--fs", "40", "--window", "60", "--out", str(out)]
print("Running:", " ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
