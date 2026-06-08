"""validate.py — Wraps bids-validator to check BIDS compliance."""

import shutil
import subprocess
import sys
from pathlib import Path


_VALIDATOR_CANDIDATES = [
    "bids-validator",
    "bids-validator-deno",
    str(Path.home() / ".local/bin/bids-validator-deno"),
]


def _find_validator() -> str | None:
    for candidate in _VALIDATOR_CANDIDATES:
        if shutil.which(candidate):
            return candidate
        if Path(candidate).exists():
            return candidate
    return None


def run_validate(args) -> None:
    validator = _find_validator()
    if validator is None:
        print("ERROR: bids-validator not found.")
        print("Expected at one of:")
        for c in _VALIDATOR_CANDIDATES:
            print(f"  {c}")
        sys.exit(1)

    cmd = [validator, args.bids_dir] + args.validator_args
    if args.ignore_warnings:
        cmd.append("--ignoreWarnings")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)
