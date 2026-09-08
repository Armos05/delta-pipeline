"""qsiprep.py — Runs QSIPrep (diffusion MRI preprocessing) via Apptainer per subject.

Sibling of preprocess.py (which runs fMRIPrep). QSIPrep launches its own
container, so — like fMRIPrep and MRIQC — it must run on the HOST, not nested
inside delta.sif. The `delta` wrapper routes `qsiprep` to the host-exec branch.

Layout:
  <bids>/sub-*/ses-*/dwi/*_dwi.nii.gz            ← input
  <output>/sub-*/...                             ← QSIPrep derivatives
  <work-dir>/                                    ← scratch (large)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from state import load_state, save_state, mark_qsiprep_done, needs_qsiprep


def _check_not_in_container() -> None:
    """Exit if called from inside Apptainer/Singularity — nested apptainer fails."""
    if os.environ.get("APPTAINER_CONTAINER") or os.environ.get("SINGULARITY_CONTAINER"):
        print("ERROR: qsiprep must be run on the host, not inside delta.sif.")
        print("Run directly on the host:")
        print("  python3 /path/to/src/cli.py qsiprep --bids ... --output ... [args]")
        sys.exit(1)


def _find_apptainer() -> str | None:
    for binary in ("apptainer", "singularity"):
        found = shutil.which(binary)
        if found:
            return found
    return None


def run_qsiprep(
    bids_dir: Path,
    output_dir: Path,
    work_dir: Path,
    qsiprep_sif: Path,
    fs_license: Path,
    subject_id: str,
    output_resolution: float,
    templateflow_home: Path | None = None,
    session_ids: list[str] | None = None,
    nprocs: int | None = None,
    omp_nthreads: int | None = None,
    mem_mb: int | None = None,
    extra_args: list[str] | None = None,
) -> bool:
    """Run QSIPrep for one subject. Returns True on success."""
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
        "--bind", f"{fs_license}:/opt/freesurfer/license.txt",
    ]

    # --cleanenv wipes TEMPLATEFLOW_HOME; bind the host cache and re-point at it.
    if templateflow_home and templateflow_home.exists():
        cmd += ["--bind", f"{templateflow_home}:/templateflow",
                "--env", "TEMPLATEFLOW_HOME=/templateflow"]

    cmd += [
        str(qsiprep_sif),
        "/data", "/out", "participant",
        "--participant-label", subject_id,
        "--fs-license-file", "/opt/freesurfer/license.txt",
        "--output-resolution", str(output_resolution),
        "--work-dir", "/work",
        "--skip-bids-validation",
        "--notrack",
    ]

    if session_ids:
        for sid in session_ids:
            cmd += ["--session-id", sid.removeprefix("ses-")]
    if nprocs is not None:
        cmd += ["--nprocs", str(nprocs)]
    if omp_nthreads is not None:
        cmd += ["--omp-nthreads", str(omp_nthreads)]
    if mem_mb is not None:
        cmd += ["--mem-mb", str(mem_mb)]

    if extra_args:
        cmd += extra_args[1:] if extra_args[0:1] == ["--"] else extra_args

    print(f"  Running: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode == 0


def run_qsiprep_cmd(args) -> None:
    """Entry point called by cli.py cmd_qsiprep(args)."""
    bids_dir    = Path(args.bids).resolve()
    output_dir  = Path(args.output).resolve()
    work_dir    = Path(args.work_dir).resolve()
    qsiprep_sif = Path(args.qsiprep_sif).resolve()
    fs_license  = Path(args.fs_license).resolve()
    state_file  = Path(args.state)
    tf_home     = Path(args.templateflow_home).expanduser() if args.templateflow_home else None
    force       = args.force
    dry_run     = args.dry_run

    _check_not_in_container()

    if not bids_dir.exists():
        print(f"ERROR: BIDS dir not found: {bids_dir}")
        sys.exit(1)
    if not qsiprep_sif.exists():
        print(f"ERROR: qsiprep .sif not found: {qsiprep_sif}")
        sys.exit(1)
    if not fs_license.exists():
        print(f"ERROR: FreeSurfer license not found: {fs_license}")
        sys.exit(1)

    state = load_state(state_file)

    subjects = args.subjects or sorted(
        d.name for d in bids_dir.iterdir() if d.is_dir() and d.name.startswith("sub-")
    )
    if not subjects:
        print(f"No sub-* directories found in {bids_dir}")
        sys.exit(1)

    # Skip subjects with no DWI data — QSIPrep would just error out.
    subjects = [s for s in subjects if any((bids_dir / s).glob("ses-*/dwi/*_dwi.nii*"))
                or any((bids_dir / s).glob("dwi/*_dwi.nii*"))]
    if not subjects:
        print("No subjects with dwi/ data found in BIDS dir.")
        sys.exit(1)

    print(f"\nDELTA QSIPrep — {len(subjects)} subject(s)")
    print(f"  BIDS:       {bids_dir}")
    print(f"  Output:     {output_dir}")
    print(f"  Work:       {work_dir}")
    print(f"  SIF:        {qsiprep_sif}")
    print(f"  Resolution: {args.output_resolution} mm\n")

    done_count = skipped_count = 0
    failed = []

    for subject in subjects:
        if not needs_qsiprep(state, subject, force=force):
            print(f"  SKIP  {subject} — already qsiprep'd (use --force to redo)")
            skipped_count += 1
            continue

        print(f"  START {subject}")
        if dry_run:
            print(f"  [dry-run] would run qsiprep for {subject}")
            continue

        success = run_qsiprep(
            bids_dir=bids_dir,
            output_dir=output_dir,
            work_dir=work_dir,
            qsiprep_sif=qsiprep_sif,
            fs_license=fs_license,
            subject_id=subject.removeprefix("sub-"),
            output_resolution=args.output_resolution,
            templateflow_home=tf_home,
            session_ids=args.sessions,
            nprocs=args.nprocs,
            omp_nthreads=args.omp_nthreads,
            mem_mb=args.mem_mb,
            extra_args=args.qsiprep_args,
        )

        if success:
            mark_qsiprep_done(state, subject)
            save_state(state, state_file)
            print(f"  DONE  {subject}")
            done_count += 1
        else:
            print(f"  FAIL  {subject} — check qsiprep output above")
            failed.append(subject)

    print(f"\nSummary: {done_count} done, {skipped_count} skipped, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)
