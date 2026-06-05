#!/usr/bin/env python3
"""
compute_tsnr.py — Compute per-region tSNR from preprocessed fMRI and append to QC scoresheet.

Registers the Harvard-Oxford cortical and subcortical atlases (FSL 2mm MNI space) into
native BOLD space via flirt, computes tSNR = Tmean/Tstd from the 4D BOLD timeseries,
extracts mean tSNR per ROI, and writes results as new columns in fMRI_QC_Scoresheet.xlsx.

Requires: FSL (flirt, fslmaths, fslstats, convert_xfm) on PATH.
Run with: /mnt/data/miniconda3/envs/delta/bin/python3 src/compute_tsnr.py [args]

Usage:
    python3 src/compute_tsnr.py \\
        --mean-bold  <boldref.nii.gz> \\
        --bold       <preproc_bold.nii.gz> \\
        --output-dir <outdir> \\
        --scoresheet qc/fMRI_QC_Scoresheet.xlsx
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

# ── FSL path resolution ────────────────────────────────────────────────────────

def _fsl_paths(fsldir: Path) -> dict:
    """Build dict of all required FSL file paths from a given FSLDIR."""
    atlases = fsldir / "data" / "atlases"
    ho = atlases / "HarvardOxford"
    return {
        "mni_brain":  fsldir / "data" / "standard" / "MNI152_T1_2mm_brain.nii.gz",
        "cort_atlas": ho / "HarvardOxford-cort-maxprob-thr25-2mm.nii.gz",
        "sub_atlas":  ho / "HarvardOxford-sub-maxprob-thr25-2mm.nii.gz",
        "cort_xml":   atlases / "HarvardOxford-Cortical.xml",
        "sub_xml":    atlases / "HarvardOxford-Subcortical.xml",
    }


# ── Shell helper ───────────────────────────────────────────────────────────────

def _run(cmd: list, desc: str = "", quiet: bool = False) -> str:
    """Run a shell command. Prints desc unless quiet. Exits on non-zero return code."""
    if not quiet:
        print(f"  + {desc or Path(cmd[0]).name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\nERROR: {desc or cmd[0]} failed")
        print(result.stderr.strip())
        sys.exit(1)
    return result.stdout


# ── Environment check ──────────────────────────────────────────────────────────

def check_environment(fsl: dict) -> None:
    """Verify FSL binaries are on PATH and all required atlas/template files exist."""
    errors = []
    for tool in ("flirt", "fslmaths", "fslstats", "convert_xfm"):
        if not shutil.which(tool):
            errors.append(f"FSL tool not in PATH: {tool}")
    for key, path in fsl.items():
        if not Path(path).exists():
            errors.append(f"Missing {key}: {path}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        sys.exit(1)


# ── Atlas label parsing ────────────────────────────────────────────────────────

def parse_xml_labels(xml_path: Path) -> dict:
    """
    Parse a Harvard-Oxford XML label file into {nifti_label_value: region_name}.
    XML index is 0-based; corresponding NIfTI label value is index + 1.
    """
    tree = ET.parse(xml_path)
    return {
        int(lbl.get("index")) + 1: lbl.text.strip()
        for lbl in tree.findall(".//label")
    }


# ── tSNR computation ───────────────────────────────────────────────────────────

def compute_tsnr(bold4d: Path, outdir: Path) -> Path:
    """
    Compute tSNR = temporal mean / temporal std from a 4D BOLD image.
    Intermediate Tmean and Tstd files are removed. Returns path to tSNR map.
    """
    tmean = outdir / "_tmp_tmean.nii.gz"
    tstd  = outdir / "_tmp_tstd.nii.gz"
    tsnr  = outdir / "_tmp_tsnr.nii.gz"

    _run(["fslmaths", str(bold4d), "-Tmean", str(tmean)], "fslmaths: temporal mean")
    _run(["fslmaths", str(bold4d), "-Tstd",  str(tstd)],  "fslmaths: temporal std")
    _run(["fslmaths", str(tmean), "-div", str(tstd), str(tsnr)], "fslmaths: tSNR = Tmean / Tstd")

    tmean.unlink(missing_ok=True)
    tstd.unlink(missing_ok=True)
    return tsnr


# ── Atlas registration ─────────────────────────────────────────────────────────

def register_atlases(mean_bold: Path, outdir: Path, fsl: dict) -> tuple:
    """
    Register mean BOLD to MNI152 (12 DOF linear), invert the transform,
    and apply it to both HO atlases using nearest-neighbour interpolation
    to avoid blurring integer labels.
    Returns (cort_in_bold_path, sub_in_bold_path).
    Cleans up the .mat files after use.
    """
    bold2mni = outdir / "_tmp_bold2mni.mat"
    mni2bold = outdir / "_tmp_mni2bold.mat"
    cort     = outdir / "_tmp_cort_in_bold.nii.gz"
    sub      = outdir / "_tmp_sub_in_bold.nii.gz"

    _run([
        "flirt", "-in", str(mean_bold), "-ref", str(fsl["mni_brain"]),
        "-omat", str(bold2mni), "-dof", "12",
    ], "flirt: mean BOLD → MNI152 (12 DOF)")

    _run([
        "convert_xfm", "-omat", str(mni2bold), "-inverse", str(bold2mni),
    ], "convert_xfm: invert → MNI152 → native BOLD")

    for atlas_key, out_path, label in [
        ("cort_atlas", cort, "cortical"),
        ("sub_atlas",  sub,  "subcortical"),
    ]:
        _run([
            "flirt",
            "-in",    str(fsl[atlas_key]),
            "-ref",   str(mean_bold),
            "-applyxfm", "-init", str(mni2bold),
            "-interp", "nearestneighbour",
            "-o",     str(out_path),
        ], f"flirt: {label} atlas → native BOLD space")

    bold2mni.unlink(missing_ok=True)
    mni2bold.unlink(missing_ok=True)
    return cort, sub


# ── Per-region tSNR extraction ─────────────────────────────────────────────────

def extract_region_tsnr(atlas: Path, tsnr: Path, labels: dict, outdir: Path) -> dict:
    """
    For each integer label in the atlas, create a binary mask and compute mean tSNR.
    Uses individual fslmaths + fslstats calls per region for correct label isolation.
    Returns {region_name: mean_tsnr} — nan where the label has no voxels in BOLD FOV.
    """
    tmp_mask = outdir / "_tmp_roi_mask.nii.gz"
    results = {}

    for nifti_val, name in labels.items():
        _run([
            "fslmaths", str(atlas),
            "-thr", str(nifti_val), "-uthr", str(nifti_val), "-bin",
            str(tmp_mask),
        ], quiet=True)

        out = _run(
            ["fslstats", "-k", str(tmp_mask), str(tsnr), "-M"],
            quiet=True,
        )

        try:
            val = float(out.strip().split()[0])
            # fslstats returns 0 when mask is empty; treat as missing
            results[name] = float("nan") if val == 0.0 else val
        except (ValueError, IndexError):
            results[name] = float("nan")

    tmp_mask.unlink(missing_ok=True)
    return results


# ── Scoresheet update ──────────────────────────────────────────────────────────

# QC Ratings sheet layout:
#   Row 1 — merged group headers  (Subject Info | MRIQC BOLD | MRIQC T1w | fMRIPrep | Decision | Notes)
#   Row 2 — short column IDs      (SubjID, sess_num, A, BA, ...)
#   Row 3 — column descriptions
#   Row 4+ — data rows

def _tsnr_start_col(ws) -> int:
    """Return column index where tSNR columns begin (existing or new after last col)."""
    for col in range(1, ws.max_column + 1):
        val = ws.cell(1, col).value
        if val and str(val).startswith("tSNR"):
            return col
    return ws.max_column + 1


def update_scoresheet(
    path: Path,
    subject_id: str,
    session: str,
    cort_tsnr: dict,
    sub_tsnr: dict,
) -> None:
    """
    Find the row matching subject_id + session in the QC Ratings sheet,
    then append/overwrite tSNR columns at the end with one column per ROI.
    Errors if the subject row is not found.
    """
    wb = openpyxl.load_workbook(path)
    ws = wb["QC Ratings"]

    # Locate the data row for this subject/session
    target_row = None
    for r in range(4, ws.max_row + 1):
        subj = str(ws.cell(r, 1).value or "").strip()
        sess = str(ws.cell(r, 2).value or "").strip()
        if subj == subject_id and sess == session:
            target_row = r
            break

    if target_row is None:
        print(f"ERROR: No row found for subject='{subject_id}' session='{session}' in {path.name}")
        print("       Add the subject row to the scoresheet first, then re-run.")
        sys.exit(1)

    # Determine column layout
    tsnr_start  = _tsnr_start_col(ws)
    cort_start  = tsnr_start
    cort_end    = cort_start + len(cort_tsnr) - 1
    sub_start   = cort_end + 1
    sub_end     = sub_start + len(sub_tsnr) - 1
    is_new_cols = (tsnr_start > ws.max_column)

    # Write group headers only on first run (new columns)
    if is_new_cols:
        # Unmerge any stale merges in the tSNR range before writing
        for merged in list(ws.merged_cells.ranges):
            if merged.min_col >= cort_start:
                ws.unmerge_cells(str(merged))

        ws.merge_cells(
            start_row=1, start_column=cort_start, end_row=1, end_column=cort_end
        )
        c = ws.cell(1, cort_start)
        c.value = "tSNR Cortical"
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center")

        ws.merge_cells(
            start_row=1, start_column=sub_start, end_row=1, end_column=sub_end
        )
        c = ws.cell(1, sub_start)
        c.value = "tSNR Subcortical"
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center")

        # Write region name headers (rows 2 + 3) once
        for i, name in enumerate(cort_tsnr):
            col = cort_start + i
            ws.cell(2, col).value = name
            ws.cell(3, col).value = name

        for i, name in enumerate(sub_tsnr):
            col = sub_start + i
            ws.cell(2, col).value = name
            ws.cell(3, col).value = name

    # Write tSNR values into the target data row
    for i, (name, val) in enumerate(cort_tsnr.items()):
        ws.cell(target_row, cort_start + i).value = (
            None if math.isnan(val) else round(val, 2)
        )

    for i, (name, val) in enumerate(sub_tsnr.items()):
        ws.cell(target_row, sub_start + i).value = (
            None if math.isnan(val) else round(val, 2)
        )

    wb.save(path)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compute region-wise tSNR and write to fMRI QC scoresheet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mean-bold",  required=True, help="3D boldref / mean BOLD (native space)")
    parser.add_argument("--bold",       required=True, help="4D preprocessed BOLD timeseries")
    parser.add_argument("--output-dir", required=True, help="Output directory for tSNR map + temp files")
    parser.add_argument("--scoresheet", required=True, help="Path to fMRI_QC_Scoresheet.xlsx")
    parser.add_argument(
        "--fsldir",
        default=os.environ.get("FSLDIR", "/mnt/data/fsl_new/fsl"),
        help="FSLDIR override (default: $FSLDIR or /mnt/data/fsl_new/fsl)",
    )
    args = parser.parse_args()

    fsl        = _fsl_paths(Path(args.fsldir))
    mean_bold  = Path(args.mean_bold).resolve()
    bold4d     = Path(args.bold).resolve()
    outdir     = Path(args.output_dir).resolve()
    scoresheet = Path(args.scoresheet).resolve()

    for p, flag in [
        (mean_bold,  "--mean-bold"),
        (bold4d,     "--bold"),
        (scoresheet, "--scoresheet"),
    ]:
        if not p.exists():
            print(f"ERROR: {flag} not found: {p}")
            sys.exit(1)

    outdir.mkdir(parents=True, exist_ok=True)
    check_environment(fsl)

    # ── Interactive subject/session prompt ──────────────────────────────────────
    print("\nDELTA tSNR — Region-wise Quality Control")
    print("─" * 45)
    subject_id = input("Subject ID  (e.g. sub-01) : ").strip()
    session    = input("Session     (e.g. ses-01) : ").strip()
    if not subject_id or not session:
        print("ERROR: Subject ID and session cannot be empty.")
        sys.exit(1)

    print(f"\n  Subject    : {subject_id}")
    print(f"  Session    : {session}")
    print(f"  Mean BOLD  : {mean_bold.name}")
    print(f"  4D BOLD    : {bold4d.name}")
    print(f"  Output dir : {outdir}\n")

    # ── Step 1: tSNR map ────────────────────────────────────────────────────────
    print("[1/4] Computing tSNR map...")
    tsnr_tmp = compute_tsnr(bold4d, outdir)

    # ── Step 2: Atlas registration ──────────────────────────────────────────────
    print("[2/4] Registering Harvard-Oxford atlas to native BOLD space...")
    cort_in_bold, sub_in_bold = register_atlases(mean_bold, outdir, fsl)

    # ── Step 3: Per-region extraction ───────────────────────────────────────────
    print("[3/4] Extracting per-region tSNR...")
    cort_labels = parse_xml_labels(fsl["cort_xml"])
    sub_labels  = parse_xml_labels(fsl["sub_xml"])

    print(f"      Cortical    : {len(cort_labels)} regions")
    cort_tsnr = extract_region_tsnr(cort_in_bold, tsnr_tmp, cort_labels, outdir)

    print(f"      Subcortical : {len(sub_labels)} regions")
    sub_tsnr  = extract_region_tsnr(sub_in_bold, tsnr_tmp, sub_labels, outdir)

    # ── Step 4: Write to scoresheet ─────────────────────────────────────────────
    print("[4/4] Updating scoresheet...")
    update_scoresheet(scoresheet, subject_id, session, cort_tsnr, sub_tsnr)

    # ── Finalise: rename tSNR map, remove atlas temp files ──────────────────────
    tsnr_final = outdir / f"tsnr_{subject_id}_{session}.nii.gz"
    tsnr_tmp.rename(tsnr_final)

    for tmp in [cort_in_bold, sub_in_bold]:
        tmp.unlink(missing_ok=True)

    print(f"\nDone.")
    print(f"  tSNR map   : {tsnr_final}")
    print(f"  Scoresheet : {scoresheet}")


if __name__ == "__main__":
    main()
