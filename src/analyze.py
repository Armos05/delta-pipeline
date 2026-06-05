"""
analyze.py — Dispatch table for DELTA analysis scripts.

Implemented:
  firstlevel-entropy — First-level GLM for the sentence-completion entropy model
  firstlevel-prior   — First-level GLM for the sentence-completion prior model
"""


def run_analyze(args) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = Path(args.script).resolve()
    if not script.exists():
        print(f"ERROR: script not found: {script}")
        sys.exit(1)

    env = os.environ.copy()
    if args.bids:
        env["BIDS_DIR"] = str(Path(args.bids).resolve())
    if args.derivatives:
        env["DERIVATIVES_DIR"] = str(Path(args.derivatives).resolve())

    suffix = script.suffix.lower()
    if suffix == ".py":
        cmd = [sys.executable, str(script)] + (args.args or [])
    elif suffix == ".r":
        cmd = ["Rscript", str(script)] + (args.args or [])
    else:
        print(f"ERROR: unsupported script type '{suffix}' (expected .py or .R)")
        sys.exit(1)

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env)
    sys.exit(result.returncode)


def run_first_level_entropy(args) -> None:
    from first_level_entropy import run_first_level_entropy as _run
    _run(args)


def run_first_level_prior(args) -> None:
    from first_level_prior import run_first_level_prior as _run
    _run(args)
