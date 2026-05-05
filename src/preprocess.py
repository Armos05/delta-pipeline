"""preprocess.py — Runs fMRIPrep via Apptainer for each unconverted subject."""

import shutil
import subprocess
import sys
from pathlib import Path

from state import load_state, save_state, needs_fmriprep, mark_fmriprep_done


def _check_not_in_container() -> None:
    """Warn and exit if called from inside an Apptainer/Singularity container.
    Nested apptainer fails due to missing host shared libraries in the container."""
    import os
    if os.environ.get("APPTAINER_CONTAINER") or os.environ.get("SINGULARITY_CONTAINER"):
        print("ERROR: preprocess must be run on the host, not inside delta.sif.")
        print("Nested Apptainer fails due to missing host libraries.")
        print("")
        print("Run directly on the host:")
        print("  python3 /path/to/src/cli.py preprocess --bids ... --output ... [args]")
        sys.exit(1)


def _find_apptainer() -> str | None:
    for binary in ("apptainer", "singularity"):
        found = shutil.which(binary)
        if found:
            return found
    return None


def run_fmriprep(
    bids_dir: Path,
    output_dir: Path,
    work_dir: Path,
    fmriprep_sif: Path,
    fs_license: Path,
    subject_id: str,
    output_spaces: str,
    fs_reconall: bool,
) -> bool:
    """Run fMRIPrep for one subject. Returns True on success."""
    runner = _find_apptainer()
    if runner is None:
        print("ERROR: apptainer/singularity not found in PATH.")
        print("When running inside delta.sif add: --bind /usr/bin/apptainer:/usr/bin/apptainer")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        runner, "run", "--cleanenv",
        "--bind", f"{bids_dir}:/data",
        "--bind", f"{output_dir}:/out",
        "--bind", f"{work_dir}:/work",
        "--bind", f"{fs_license}:/opt/freesurfer/license.txt",
        str(fmriprep_sif),
        "/data", "/out", "participant",
        "--participant-label", subject_id,
        "--output-spaces", output_spaces,
        "--work-dir", "/work",
        "--notrack",
        "--skip-bids-validation",
    ]

    if not fs_reconall:
        cmd.append("--fs-no-reconall")

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def run_preprocess(args) -> None:
    bids_dir = Path(args.bids).resolve()
    output_dir = Path(args.output).resolve()
    work_dir = Path(args.work_dir).resolve()
    fmriprep_sif = Path(args.fmriprep_sif).resolve()
    fs_license = Path(args.fs_license).resolve()
    state_file = Path(args.state)
    force = args.force
    dry_run = args.dry_run

    _check_not_in_container()

    if not bids_dir.exists():
        print(f"ERROR: BIDS dir not found: {bids_dir}")
        sys.exit(1)
    if not fmriprep_sif.exists():
        print(f"ERROR: fmriprep .sif not found: {fmriprep_sif}")
        sys.exit(1)
    if not fs_license.exists():
        print(f"ERROR: FreeSurfer license not found: {fs_license}")
        sys.exit(1)

    state = load_state(state_file)

    if args.subjects:
        subjects = args.subjects
    else:
        subjects = sorted(d.name for d in bids_dir.iterdir() if d.is_dir() and d.name.startswith("sub-"))

    if not subjects:
        print(f"No sub-* directories found in {bids_dir}")
        sys.exit(1)

    print(f"\nDELTA Preprocess — {len(subjects)} subject(s)")
    print(f"  BIDS:    {bids_dir}")
    print(f"  Output:  {output_dir}")
    print(f"  Work:    {work_dir}")
    print(f"  SIF:     {fmriprep_sif}")
    print(f"  Spaces:  {args.output_spaces}\n")

    done_count = 0
    skipped_count = 0
    failed = []

    for subject in subjects:
        subject_id = subject.removeprefix("sub-")

        if not needs_fmriprep(state, subject, force=force):
            print(f"  SKIP  {subject} — already preprocessed (use --force to redo)")
            skipped_count += 1
            continue

        print(f"  START {subject}")

        if dry_run:
            print(f"  [dry-run] would run fmriprep for {subject}")
            continue

        success = run_fmriprep(
            bids_dir=bids_dir,
            output_dir=output_dir,
            work_dir=work_dir,
            fmriprep_sif=fmriprep_sif,
            fs_license=fs_license,
            subject_id=subject_id,
            output_spaces=args.output_spaces,
            fs_reconall=args.fs_reconall,
        )

        if success:
            mark_fmriprep_done(state, subject)
            save_state(state, state_file)
            print(f"  DONE  {subject}")
            done_count += 1
        else:
            print(f"  FAIL  {subject} — check fmriprep output above")
            failed.append(subject)

    print(f"\nSummary: {done_count} done, {skipped_count} skipped, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)
