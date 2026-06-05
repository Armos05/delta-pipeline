"""
convert.py — Wraps dcm2bids to convert DICOM → BIDS incrementally.
Only processes subjects/sessions not already in delta_state.json.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from state import load_state, save_state, needs_conversion, mark_converted

MODALITY_FOLDERS = ["anat", "func", "fmap", "dwi", "behav"]


def init_bids_root(bids_dir: Path, configs_dir: Path) -> None:
    """Write dataset_description.json and .bidsignore to BIDS root if absent.

    Copies from configs_dir (same folder as the dcm2bids config file),
    so this works both on the host and inside the Apptainer container.
    """
    desc_src = configs_dir / "dataset_description.json"
    desc_dst = bids_dir / "dataset_description.json"
    if desc_src.exists() and not desc_dst.exists():
        shutil.copy(desc_src, desc_dst)

    ignore_src = configs_dir / "bidsignore"
    ignore_dst = bids_dir / ".bidsignore"
    if ignore_src.exists() and not ignore_dst.exists():
        shutil.copy(ignore_src, ignore_dst)


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


def find_nm_dicom(session_dicom_dir: Path) -> Path | None:
    """Return primary fl3d_mt_tra_tr55 DICOM folder; skip MPR/ND reconstructions."""
    candidates = sorted(
        d for d in session_dicom_dir.iterdir()
        if d.is_dir()
        and d.name.startswith("fl3d_mt_tra_tr55")
        and "_MPR_" not in d.name
        and "_ND" not in d.name
    )
    return candidates[0] if candidates else None


def run_nm_conversion(dicom_dir: Path, bids_dir: Path, subject: str, session: str) -> bool:
    """Run dcm2niix on NM DICOM → bids_dir/sub-XX/ses-XX/nm/. Returns True on success."""
    nm_dicom = find_nm_dicom(dicom_dir)
    if nm_dicom is None:
        print(f"  NM  no fl3d_mt_tra_tr55 folder found in {dicom_dir}, skipping")
        return True

    subject_id = subject.removeprefix("sub-")
    session_id = session.removeprefix("ses-")
    nm_out = bids_dir / subject / session / "nm"
    nm_out.mkdir(parents=True, exist_ok=True)

    filename = f"sub-{subject_id}_ses-{session_id}_NM"
    cmd = [
        "dcm2niix",
        "-z", "y",
        "-f", filename,
        "-o", str(nm_out),
        str(nm_dicom),
    ]
    print(f"  NM  {nm_dicom.name} → nm/")
    result = subprocess.run(cmd, capture_output=False, text=True)
    return result.returncode == 0


def ensure_behav_dir(bids_dir: Path, subject: str, session: str) -> None:
    """Create the behav/ directory for a subject/session if it does not exist.

    dcm2bids does not create behav/ automatically (no DICOM source maps to it).
    The directory is created empty here so the BIDS tree is complete and ready
    for behavioural log files to be placed there manually or by a later step.
    """
    behav_dir = bids_dir / subject / session / "behav"
    behav_dir.mkdir(parents=True, exist_ok=True)
    print(f"  BEHAV  {behav_dir} (created)")


def patch_phasediff_echotimes(bids_dir: Path, subject: str, session: str) -> None:
    """Read EchoTime from magnitude1/2 JSONs and write EchoTime1/EchoTime2 into phasediff JSON.
    Needed because dcm2bids sidecar_changes is static — values can't be derived at config time."""
    fmap_dir = bids_dir / subject / session / "fmap"
    if not fmap_dir.exists():
        return

    mag1 = next(fmap_dir.glob("*_magnitude1.json"), None)
    mag2 = next(fmap_dir.glob("*_magnitude2.json"), None)
    phasediff = next(fmap_dir.glob("*_phasediff.json"), None)

    if not (mag1 and mag2 and phasediff):
        return

    with open(mag1) as f:
        et1 = json.load(f).get("EchoTime")
    with open(mag2) as f:
        et2 = json.load(f).get("EchoTime")

    if et1 is None or et2 is None:
        print(f"  WARN  EchoTime missing in magnitude JSONs — phasediff EchoTime1/2 not patched")
        return

    with open(phasediff) as f:
        pd_data = json.load(f)
    pd_data["EchoTime1"] = et1
    pd_data["EchoTime2"] = et2
    with open(phasediff, "w") as f:
        json.dump(pd_data, f, indent=2)
    print(f"  FMAP  phasediff patched — EchoTime1={et1} EchoTime2={et2}")


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
        "--clobber",
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
    init_bids_root(bids_dir, config.parent)
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
                patch_phasediff_echotimes(bids_dir, subject, session)
                ensure_behav_dir(bids_dir, subject, session)
                success = run_nm_conversion(dicom_dir, bids_dir, subject, session)

            if success:
                mark_converted(state, subject, session, dicom_dir, MODALITY_FOLDERS)
                save_state(state, state_file)
                print(f"  DONE  {subject}/{session}")
                converted_count += 1
            else:
                print(f"  FAIL  {subject}/{session} — check output above")
                failed.append(f"{subject}/{session}")

    print(f"\nSummary: {converted_count} converted, {skipped_count} skipped, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)
