"""
analyze.py — updated to include encoding model dispatch
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_first_level(args) -> None:
    from first_level_entropy import run_first_level as _run
    _run(args)


def run_encoding_model(args) -> None:
    from encoding_model import run_encoding_model as _run
    _run(args)


def run_analyze(args) -> None:
    """Generic script runner (.py → python3, .R → Rscript)."""
    script = Path(args.script)
    if not script.exists():
        print(f"Script not found: {script}", file=sys.stderr)
        sys.exit(1)

    env_extra = {
        "BIDS_DIR": str(args.bids),
        "DERIVATIVES_DIR": str(args.derivatives),
    }
    import os
    env = {**os.environ, **env_extra}

    if script.suffix == ".py":
        cmd = [sys.executable, str(script)]
    elif script.suffix == ".R":
        cmd = ["Rscript", str(script)]
    else:
        print(f"Unsupported script type: {script.suffix}", file=sys.stderr)
        sys.exit(1)

    cmd += [getattr(args, "script_args", None) or []]
    subprocess.run(cmd, env=env, check=True)
