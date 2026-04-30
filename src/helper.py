"""
helper.py — Runs dcm2bids_helper to explore DICOM headers.
Helps you build dcm2bids_config.json for your scanner protocol.
"""

import subprocess
import sys
from pathlib import Path


def run_helper(args) -> None:
    dicom_dir = Path(args.dicom).resolve()
    output_dir = Path(args.output).resolve()

    if not dicom_dir.exists():
        print(f"ERROR: DICOM directory not found: {dicom_dir}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["dcm2bids_helper", "--dicom_dir", str(dicom_dir), "--output_dir", str(output_dir)]
    print(f"Running: {' '.join(cmd)}")
    print(f"Output will be in: {output_dir}")
    print("Use the JSON files there to build your dcm2bids_config.json\n")

    result = subprocess.run(cmd)
    sys.exit(result.returncode)
