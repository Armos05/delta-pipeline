#!/usr/bin/env python3
"""
transcribe_responses.py — Manual audio transcription helper for DELTA.

Opens each audio response recording in sequence (or in a filtered subset),
lets the researcher type the word that was spoken, evaluates correctness
against the target word from the sentence bank, and writes two new columns
to the raw task CSV:

  word_response   — the manually transcribed spoken word (str)
  response_eval   — 1 = correct (matches target), 0 = incorrect (int)

The script is resumable: rows that already have a non-empty ``word_response``
are skipped by default (use --redo to overwrite all).

Audio playback uses the system default player via ``playsound``; falls back
to ``afplay`` (macOS) / ``aplay`` (Linux) if the package is absent.

Usage
-----
  python3 src/transcribe_responses.py \\
      --csv     /path/to/main_task_data_sub-811422_PIER_SARTI_ses-001_2026-04-30_10h14.52.300.csv \\
      --bank    /path/to/All_sentences_translated_with_en_cloze_prob_and_entropy.csv \\
      --audio-dir /path/to/audio_responses \\
      [--output /path/to/enriched_output.csv] \\
      [--audio-col response_audio_file] \\
      [--redo]

After completion the script prints a summary and saves the CSV in-place
(or to --output if given).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Audio playback
# ─────────────────────────────────────────────────────────────────────────────

def play_audio(path: Path) -> None:
    """Play an audio file, trying playsound then platform CLI fallback."""
    if not path.exists():
        print(f"  [audio not found: {path}]")
        return
    try:
        from playsound import playsound  # type: ignore
        playsound(str(path), block=True)
        return
    except ImportError:
        pass
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["afplay", str(path)], check=False)
    elif system == "Linux":
        # try aplay (wav), then mpg123 (mp3), then paplay
        for player, args in [
            ("aplay",  [str(path)]),
            ("mpg123", ["-q", str(path)]),
            ("paplay", [str(path)]),
        ]:
            if subprocess.run(["which", player], capture_output=True).returncode == 0:
                subprocess.run([player] + args, check=False)
                return
        print("  [no audio player found — install playsound, aplay, or mpg123]")
    else:
        print(f"  [audio playback not supported on {system}]")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_sentence_number(sentence_file: str) -> int | None:
    m = re.search(r"Sentence_(\d+)\.wav", str(sentence_file))
    return int(m.group(1)) if m else None


def find_audio_file(audio_dir: Path, row: pd.Series, audio_col: str | None) -> Path | None:
    """Locate the audio recording for a trial row.

    Strategy (in order):
    1. If ``audio_col`` is set and the column exists, use that path directly
       (absolute, or relative to audio_dir).
    2. Try ``audio_dir / <trial_num>.wav`` and ``audio_dir / <trial_num>.mp3``.
    3. Try ``audio_dir / response_<trial_num>.*``.
    """
    if audio_col and audio_col in row.index and pd.notna(row[audio_col]):
        candidate = Path(str(row[audio_col]))
        if candidate.is_absolute() and candidate.exists():
            return candidate
        joined = audio_dir / candidate
        if joined.exists():
            return joined

    trial = row.get("trial_num", row.name)
    for stem in [str(trial), f"response_{trial}"]:
        for ext in [".wav", ".mp3", ".ogg", ".flac"]:
            p = audio_dir / f"{stem}{ext}"
            if p.exists():
                return p
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main transcription loop
# ─────────────────────────────────────────────────────────────────────────────

def run_transcription(args) -> None:
    csv_path  = Path(args.csv).resolve()
    bank_path = Path(args.bank).resolve()
    audio_dir = Path(args.audio_dir).resolve() if args.audio_dir else None
    out_path  = Path(args.output).resolve() if args.output else csv_path
    audio_col = args.audio_col
    redo      = args.redo

    df   = pd.read_csv(csv_path)
    bank = pd.read_csv(bank_path)

    # ── Required column checks ────────────────────────────────────────────────
    for col in ["sentence_file"]:
        if col not in df.columns:
            sys.exit(f"ERROR: subject CSV is missing required column '{col}'")
    for col in ["index", "en_target_word"]:
        if col not in bank.columns:
            sys.exit(f"ERROR: sentence bank is missing required column '{col}'")

    # ── Build target-word lookup ──────────────────────────────────────────────
    lookup_target = bank.set_index("index")["en_target_word"].to_dict()

    # ── Ensure output columns exist ───────────────────────────────────────────
    if "word_response" not in df.columns:
        df["word_response"] = ""
    if "response_eval" not in df.columns:
        df["response_eval"] = pd.NA

    # ── Identify rows that need transcription ─────────────────────────────────
    if redo:
        pending = df.index.tolist()
    else:
        pending = df[
            df["word_response"].isna() | (df["word_response"].astype(str).str.strip() == "")
        ].index.tolist()

    if not pending:
        print("All rows already transcribed. Use --redo to overwrite.")
        return

    n_total = len(pending)
    print(f"\nDELTA transcription helper — {n_total} rows to transcribe")
    print("Commands: [Enter] = accept  |  r = replay  |  s = skip  |  q = quit & save\n")

    completed = 0
    skipped   = 0

    for pos, idx in enumerate(pending, 1):
        row = df.loc[idx]
        sent_num = extract_sentence_number(str(row.get("sentence_file", "")))
        target   = lookup_target.get(sent_num, "???")

        print(f"─── Trial {pos}/{n_total}  (row {idx})  sentence #{sent_num}  target: [{target}] ───")

        # Play audio
        if audio_dir is not None:
            audio_path = find_audio_file(audio_dir, row, audio_col)
            if audio_path:
                print(f"  Playing: {audio_path.name}")
                play_audio(audio_path)
            else:
                print("  [no audio file found for this trial]")

        # Input loop
        while True:
            try:
                raw = input("  Word heard (or r/s/q): ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "q"

            if raw.lower() == "q":
                df.to_csv(out_path, index=False)
                print(f"\nSaved and quit. {completed} transcribed, {skipped} skipped.")
                print(f"Output: {out_path}")
                return

            if raw.lower() == "s":
                skipped += 1
                break

            if raw.lower() == "r":
                if audio_dir is not None and audio_path:
                    play_audio(audio_path)
                else:
                    print("  [no audio to replay]")
                continue

            # Accept the transcription
            word = raw.lower().strip()
            is_correct = int(word == str(target).lower().strip())
            df.at[idx, "word_response"]  = word
            df.at[idx, "response_eval"]  = is_correct
            label = "CORRECT ✓" if is_correct else "INCORRECT ✗"
            print(f"  → {label}  (typed: '{word}'  target: '{target}')")
            completed += 1
            break

        # Auto-save every 10 rows
        if pos % 10 == 0:
            df.to_csv(out_path, index=False)
            print(f"  [auto-saved at row {pos}]")

    df.to_csv(out_path, index=False)
    print(f"\nDone. {completed} transcribed, {skipped} skipped.")
    print(f"Output: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Manual audio transcription helper — adds word_response and response_eval to task CSV"
    )
    p.add_argument("--csv",       required=True,
                   help="Raw task CSV (e.g. main_task_data_sub-811422_...csv)")
    p.add_argument("--bank",      required=True,
                   help="Sentence bank CSV (All_sentences_translated_with_en_cloze_prob_and_entropy.csv)")
    p.add_argument("--audio-dir", default=None, dest="audio_dir",
                   help="Directory containing audio response recordings")
    p.add_argument("--audio-col", default=None, dest="audio_col",
                   help="Column in CSV holding the audio filename (optional)")
    p.add_argument("--output",    default=None,
                   help="Output path for annotated CSV (default: overwrite input)")
    p.add_argument("--redo",      action="store_true",
                   help="Re-transcribe rows that already have a word_response")
    return p


if __name__ == "__main__":
    run_transcription(_build_parser().parse_args())
