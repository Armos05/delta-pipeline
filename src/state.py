"""
state.py — Tracks which subjects/sessions have been converted or preprocessed.
Reads/writes delta_state.json. Never modifies DICOM or BIDS data directly.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path


STATE_VERSION = 1
DEFAULT_STATE_FILE = "delta_state.json"


def _hash_dicom_dir(dicom_dir: Path) -> str:
    """Hash a DICOM directory by combining all filenames + sizes (fast, no full read)."""
    h = hashlib.md5()
    entries = sorted(dicom_dir.rglob("*"))
    for entry in entries:
        if entry.is_file():
            stat = entry.stat()
            h.update(f"{entry.name}{stat.st_size}".encode())
    return h.hexdigest()


def load_state(state_file: Path) -> dict:
    """Load state from JSON file. Returns empty state if file doesn't exist."""
    if not state_file.exists():
        return {"version": STATE_VERSION, "subjects": {}}
    with open(state_file) as f:
        return json.load(f)


def save_state(state: dict, state_file: Path) -> None:
    """Write state to JSON file atomically."""
    tmp = state_file.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(state_file)


def get_subject_session(state: dict, subject: str, session: str) -> dict:
    """Return the state entry for a subject/session, or empty dict if new."""
    return state["subjects"].get(subject, {}).get(session, {})


def mark_converted(state: dict, subject: str, session: str, dicom_dir: Path, modalities: list) -> None:
    """Record that a subject/session has been converted to BIDS."""
    if subject not in state["subjects"]:
        state["subjects"][subject] = {}
    state["subjects"][subject][session] = {
        "hash": _hash_dicom_dir(dicom_dir),
        "converted": modalities,
        "preprocessed": False,
        "converted_at": datetime.now().isoformat(),
        "preprocessed_at": None,
    }


def mark_preprocessed(state: dict, subject: str, session: str) -> None:
    """Record that a subject/session has been run through fMRIPrep."""
    entry = state["subjects"].get(subject, {}).get(session)
    if entry is None:
        raise ValueError(f"{subject}/{session} not in state — convert first")
    entry["preprocessed"] = True
    entry["preprocessed_at"] = datetime.now().isoformat()


def mark_fmriprep_done(state: dict, subject: str) -> None:
    """Record that fMRIPrep has completed for a subject (all sessions)."""
    if subject not in state["subjects"]:
        state["subjects"][subject] = {}
    state["subjects"][subject]["_fmriprep"] = {
        "done": True,
        "preprocessed_at": datetime.now().isoformat(),
    }


def needs_fmriprep(state: dict, subject: str, force: bool = False) -> bool:
    """Return True if fMRIPrep has not yet run for this subject."""
    if force:
        return True
    return not state.get("subjects", {}).get(subject, {}).get("_fmriprep", {}).get("done", False)


def needs_conversion(state: dict, subject: str, session: str, dicom_dir: Path, force: bool = False) -> bool:
    """Return True if this subject/session needs (re)conversion."""
    if force:
        return True
    entry = get_subject_session(state, subject, session)
    if not entry:
        return True
    current_hash = _hash_dicom_dir(dicom_dir)
    return entry.get("hash") != current_hash


def needs_preprocessing(state: dict, subject: str, session: str, force: bool = False) -> bool:
    """Return True if this subject/session needs (re)preprocessing."""
    if force:
        return True
    entry = get_subject_session(state, subject, session)
    if not entry:
        return False  # must convert first
    return not entry.get("preprocessed", False)


def print_status(state: dict) -> None:
    """Print a summary table of all subjects/sessions."""
    subjects = state.get("subjects", {})
    if not subjects:
        print("No subjects tracked yet.")
        return

    print(f"\n{'Subject':<12} {'Session':<12} {'Converted':<12} {'Preprocessed':<14} {'Converted At'}")
    print("-" * 70)
    for subject, sessions in sorted(subjects.items()):
        for session, entry in sorted(sessions.items()):
            converted = "yes" if entry.get("converted") else "no"
            preprocessed = "yes" if entry.get("preprocessed") else "no"
            converted_at = entry.get("converted_at", "")[:19]
            print(f"{subject:<12} {session:<12} {converted:<12} {preprocessed:<14} {converted_at}")
    print()
