"""
cli.py — Main entry point for the DELTA pipeline.
Parses user commands and dispatches to the correct operation.
"""

import argparse
import sys
from pathlib import Path

from state import load_state, print_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delta",
        description="DELTA neuroimaging pipeline — DICOM → BIDS → fMRIPrep → Analysis",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- convert ---
    conv = subparsers.add_parser("convert", help="Convert DICOM → BIDS (incremental)")
    conv.add_argument("--raw", required=True, help="Raw DICOM root directory")
    conv.add_argument("--bids", required=True, help="BIDS output directory")
    conv.add_argument("--config", required=True, help="dcm2bids config JSON file")
    conv.add_argument("--subjects", nargs="+", default=None, help="Subject IDs to convert (default: all new)")
    conv.add_argument("--sessions", nargs="+", default=None, help="Session IDs (default: all)")
    conv.add_argument("--state", default="delta_state.json", help="State file path")
    conv.add_argument("--force", action="store_true", help="Re-convert even if already done")
    conv.add_argument("--dry-run", action="store_true", help="Show what would run, no changes")

    # --- preprocess ---
    prep = subparsers.add_parser("preprocess", help="Run fMRIPrep on converted subjects")
    prep.add_argument("--bids", required=True, help="BIDS directory")
    prep.add_argument("--output", required=True, help="Derivatives output directory")
    prep.add_argument("--fmriprep-sif", required=True, help="Path to fmriprep.sif")
    prep.add_argument("--fs-license", required=True, help="FreeSurfer license file")
    prep.add_argument("--subjects", nargs="+", default=None, help="Subject IDs (default: all converted)")
    prep.add_argument("--state", default="delta_state.json", help="State file path")
    prep.add_argument("--force", action="store_true", help="Re-preprocess even if already done")
    prep.add_argument("--dry-run", action="store_true", help="Show what would run, no changes")

    # --- analyze ---
    anal = subparsers.add_parser("analyze", help="Run a Python or R analysis script")
    anal.add_argument("--script", required=True, help="Path to .py or .R script")
    anal.add_argument("--bids", default=None, help="BIDS directory (passed as env var BIDS_DIR)")
    anal.add_argument("--derivatives", default=None, help="Derivatives dir (passed as env var DERIVATIVES_DIR)")
    anal.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="Extra args passed to script")

    # --- status ---
    stat = subparsers.add_parser("status", help="Show conversion/preprocessing status")
    stat.add_argument("--state", default="delta_state.json", help="State file path")

    # --- helper ---
    helper = subparsers.add_parser("helper", help="Explore DICOM headers to build dcm2bids config")
    helper.add_argument("--dicom", required=True, help="DICOM directory for one subject/session")
    helper.add_argument("--output", default="./helper_output", help="Where to write helper output")

    # --- bids-validator ---
    valid = subparsers.add_parser("bids-validator", help="Validate a BIDS directory")
    valid.add_argument("bids_dir", help="BIDS directory to validate")
    valid.add_argument("validator_args", nargs=argparse.REMAINDER,
                       help="Extra flags passed to bids-validator (e.g. --ignoreWarnings)")

    return parser


def cmd_status(args):
    state_file = Path(args.state)
    state = load_state(state_file)
    print_status(state)


def cmd_convert(args):
    # Import here so CLI loads fast even if deps missing
    from convert import run_convert
    run_convert(args)


def cmd_preprocess(args):
    from preprocess import run_preprocess
    run_preprocess(args)


def cmd_analyze(args):
    from analyze import run_analyze
    run_analyze(args)


def cmd_helper(args):
    from helper import run_helper
    run_helper(args)


def cmd_validate(args):
    from validate import run_validate
    run_validate(args)


COMMANDS = {
    "status": cmd_status,
    "convert": cmd_convert,
    "preprocess": cmd_preprocess,
    "analyze": cmd_analyze,
    "helper": cmd_helper,
    "bids-validator": cmd_validate,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    handler(args)


if __name__ == "__main__":
    main()
