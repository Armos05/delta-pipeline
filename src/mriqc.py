"""mriqc.py — Runs MRIQC via Apptainer for each subject."""

import shutil
import subprocess
import sys
from pathlib import Path

from state import load_state, save_state, needs_mriqc, mark_mriqc_done


def _check_not_in_container() -> None:
    """Warn and exit if called from inside an Apptainer/Singularity container."""
    import os
    if os.environ.get("APPTAINER_CONTAINER") or os.environ.get("SINGULARITY_CONTAINER"):
        print("ERROR: mriqc must be run on the host, not inside delta.sif.")
        print("Run directly on the host:")
        print("  python3 /path/to/src/cli.py mriqc --bids ... --output ... [args]")
        sys.exit(1)


def _find_apptainer() -> str | None:
    for binary in ("apptainer", "singularity"):
        found = shutil.which(binary)
        if found:
            return found
    return None


def run_mriqc(
    bids_dir: Path,
    output_dir: Path,
    work_dir: Path,
    mriqc_sif: Path,
    subject_id: str,
    session_ids: list[str] | None = None,
    modalities: list[str] | None = None,
    nprocs: int | None = None,
    mem_gb: float | None = None,
    extra_args: list[str] | None = None,
) -> bool:
    """Run MRIQC for one subject. Returns True on success."""
    runner = _find_apptainer()
    if runner is None:
        print("ERROR: apptainer/singularity not found in PATH.")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        runner, "run", "--cleanenv",
        "--bind", f"{bids_dir}:/data:ro",
        "--bind", f"{output_dir}:/out",
        "--bind", f"{work_dir}:/work",
        str(mriqc_sif),
        "/data", "/out", "participant",
        "--participant-label", subject_id,
        "--work-dir", "/work",
        "--no-sub",
    ]

    if session_ids:
        for sid in session_ids:
            cmd += ["--session-id", sid.removeprefix("ses-")]

    if modalities:
        cmd += ["--modalities"] + modalities

    if nprocs is not None:
        cmd += ["--nprocs", str(nprocs)]

    if mem_gb is not None:
        cmd += ["--mem-gb", str(mem_gb)]

    if extra_args:
        args = extra_args[1:] if extra_args[0:1] == ["--"] else extra_args
        cmd += args

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_mriqc_group(
    output_dir: Path,
    mriqc_sif: Path,
    work_dir: Path,
) -> bool:
    """Run MRIQC group-level aggregation. Returns True on success."""
    runner = _find_apptainer()
    if runner is None:
        print("ERROR: apptainer/singularity not found in PATH.")
        return False

    cmd = [
        runner, "run", "--cleanenv",
        "--bind", f"{output_dir}:/out",
        "--bind", f"{work_dir}:/work",
        str(mriqc_sif),
        "/out", "/out", "group",
        "--no-sub",
    ]

    print(f"  Running group: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_mriqc_cmd(args) -> None:
    bids_dir = Path(args.bids).resolve()
    output_dir = Path(args.derivatives).resolve()
    work_dir = Path(args.work_dir).resolve()
    mriqc_sif = Path(args.mriqc_sif).resolve()
    state_file = Path(args.state)
    force = args.force
    dry_run = args.dry_run

    _check_not_in_container()

    if not bids_dir.exists():
        print(f"ERROR: BIDS dir not found: {bids_dir}")
        sys.exit(1)
    if not mriqc_sif.exists():
        print(f"ERROR: mriqc .sif not found: {mriqc_sif}")
        sys.exit(1)

    state = load_state(state_file)

    if args.subject:
        subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subject]
    else:
        subjects = sorted(d.name for d in bids_dir.iterdir() if d.is_dir() and d.name.startswith("sub-"))

    if not subjects:
        print(f"No sub-* directories found in {bids_dir}")
        sys.exit(1)

    print(f"\nDELTA MRIQC — {len(subjects)} subject(s)")
    print(f"  BIDS:        {bids_dir}")
    print(f"  Derivatives: {output_dir}")
    print(f"  Work:        {work_dir}")
    print(f"  SIF:         {mriqc_sif}\n")

    done_count = 0
    skipped_count = 0
    failed = []

    for subject in subjects:
        subject_id = subject.removeprefix("sub-")

        if not needs_mriqc(state, subject, force=force):
            print(f"  SKIP  {subject} — already QC'd (use --force to redo)")
            skipped_count += 1
            continue

        print(f"  START {subject}")

        if dry_run:
            print(f"  [dry-run] would run mriqc for {subject}")
            continue

        success = run_mriqc(
            bids_dir=bids_dir,
            output_dir=output_dir,
            work_dir=work_dir,
            mriqc_sif=mriqc_sif,
            subject_id=subject_id,
            session_ids=args.sessions,
            modalities=args.modalities or None,
            nprocs=args.nprocs,
            mem_gb=args.mem_gb,
            extra_args=args.mriqc_args,
        )

        if success:
            mark_mriqc_done(state, subject)
            save_state(state, state_file)
            print(f"  DONE  {subject}")
            done_count += 1
        else:
            print(f"  FAIL  {subject} — check mriqc output above")
            failed.append(subject)

    print(f"\nSummary: {done_count} done, {skipped_count} skipped, {len(failed)} failed")

    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)

    if not dry_run and done_count > 0 and args.group:
        print("\n─── Running MRIQC group-level ───")
        if not run_mriqc_group(output_dir, mriqc_sif, work_dir):
            print("WARN: group-level MRIQC failed")
