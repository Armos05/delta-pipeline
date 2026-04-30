"""
convert.py — Wraps dcm2bids to convert DICOM → BIDS incrementally.
Only processes subjects/sessions not already in delta_state.json.
"""

import subprocess
import sys
from pathlib import Path

from state import load_state, save_state, needs_conversion, mark_converted


MODALITY_FOLDERS = ["T1", "fMRI", "DTI", "MRSpec", "Neuromelanin"]


def discover_subjects(raw_dir: Path) -> list[str]:
    """Find all sub-XX directories under raw_dir."""
    return sorted(
        d.name for d in raw_dir.iterdir()
        if d.is_dir() and d.name.startswith("sub-")
    )


def discover_sessions(subject_dir: Path) -> list[str]:
    """Find all ses-XX directories under a subject directory."""
    sessions = sorted(
        d.name for d in subject_dir.iterdir()
        if d.is_dir() and d.name.startswith("ses-")
    )
    return sessions if sessions else ["ses-01"]


def run_dcm2bids(raw_dir: Path, bids_dir: Path, config: Path, subject: str, session: str) -> bool:
    """Run dcm2bids for one subject/session. Returns True on success."""
    # dcm2bids expects subject ID without the 'sub-' prefix
    subject_id = subject.removeprefix("sub-")
    session_id = session.removeprefix("ses-")

    dicom_dir = raw_dir / subject / session
    if not dicom_dir.exists():
        dicom_dir = raw_dir / subject  # fallback: no session level

    cmd = [
        "dcm2bids",
        "--dicom_dir", str(dicom_dir),
        "--participant", subject_id,
        "--session", session_id,
        "--config", str(config),
        "--output_dir", str(bids_dir),
        "--force_dcm2niix",
        "--skip_validate",
    ]

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    return result.returncode == 0


def run_convert(args) -> None:
    raw_dir = Path(args.raw).resolve()
    bids_dir = Path(args.bids).resolve()
    config = Path(args.config).resolve()
    state_file = Path(args.state)
    force = args.force
    dry_run = args.dry_run

    if not raw_dir.exists():
        print(f"ERROR: raw dir not found: {raw_dir}")
        sys.exit(1)
    if not config.exists():
        print(f"ERROR: dcm2bids config not found: {config}")
        sys.exit(1)

    bids_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(state_file)

    subjects = args.subjects if args.subjects else discover_subjects(raw_dir)
    if not subjects:
        print("No subjects found. Check --raw directory structure (expects sub-XX folders).")
        sys.exit(1)

    print(f"\nDELTA Convert — {len(subjects)} subject(s) found")
    print(f"  Raw:    {raw_dir}")
    print(f"  BIDS:   {bids_dir}")
    print(f"  Config: {config}\n")

    converted_count = 0
    skipped_count = 0
    failed = []

    for subject in subjects:
        subject_dir = raw_dir / subject
        sessions = args.sessions if args.sessions else discover_sessions(subject_dir)

        for session in sessions:
            dicom_dir = subject_dir / session if (subject_dir / session).exists() else subject_dir

            if not needs_conversion(state, subject, session, dicom_dir, force=force):
                print(f"  SKIP  {subject}/{session} — already converted (use --force to redo)")
                skipped_count += 1
                continue

            print(f"  START {subject}/{session}")

            if dry_run:
                print(f"  [dry-run] would convert {subject}/{session}")
                continue

            success = run_dcm2bids(raw_dir, bids_dir, config, subject, session)

            if success:
                mark_converted(state, subject, session, dicom_dir, MODALITY_FOLDERS)
                save_state(state, state_file)
                print(f"  DONE  {subject}/{session}")
                converted_count += 1
            else:
                print(f"  FAIL  {subject}/{session} — check dcm2bids output above")
                failed.append(f"{subject}/{session}")

    print(f"\nSummary: {converted_count} converted, {skipped_count} skipped, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)
