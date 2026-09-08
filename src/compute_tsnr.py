#!/usr/bin/env python3
"""
compute_tsnr.py — Per-region tSNR QC from preprocessed fMRI.

tSNR = Tmean / Tstd, computed on the BOLD in its own grid (the 4D data is never
resampled). Scanner drift is removed with a 128 s high-pass before Tstd on runs
long enough to allow it. Mean tSNR is extracted per Harvard-Oxford region; the
atlas reaches BOLD space through a T1w→MNI registration (the BOLD is already in
T1w space, so a T1-contrast registration is used, not EPI→MNI), resampled onto
the BOLD grid with nearest-neighbour.

Two entry points:
  * run_tsnr_batch()  — `delta tsnr`: scans an fMRIPrep tree, one map per run,
    one tracking TSV per subject in <derivatives>/sub-<id>/tsnr_maps/
  * main()            — single run, interactive prompts, writes the legacy
    fMRI_QC_Scoresheet.xlsx "QC Ratings" sheet

Requires: FSL (flirt, fslmaths, fslstats, fslval, convert_xfm) + Harvard-Oxford
atlases on PATH. Run with /mnt/data/miniconda3/envs/delta/bin/python3.
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

def _find_apptainer() -> str | None:
    for binary in ("apptainer", "singularity"):
        found = shutil.which(binary)
        if found:
            return found
    return None


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

def _nvols(img: Path) -> int:
    """Number of volumes in a 4D NIfTI, via `fslval`."""
    return int(_run(["fslval", str(img), "dim4"], quiet=True).strip())


# Inline script run inside the fMRIPrep container: 2nd-order Legendre polynomial
# detrend per voxel (identical to nipype/MRIQC TSNR regress_poly=2), then
# tSNR = mean(raw) / std(residuals). Fast — a [nvols x 3] lstsq — unlike the
# per-voxel FIR that `fslmaths -bptf` runs.
_POLY_TSNR_PY = r"""
import sys, numpy as np, nibabel as nib
in_file, mask_file, out_file, poly = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
img = nib.load(in_file); data = img.get_fdata(dtype=np.float32)
sh = data.shape; n = sh[-1]
if mask_file != "-":
    m = np.asarray(nib.load(mask_file).dataobj) > 0
else:
    m = np.ones(sh[:-1], bool)
Y = data[m].T                                               # [n, nvox_in_mask]
V = np.polynomial.legendre.legvander(np.linspace(-1, 1, n), poly)
beta, *_ = np.linalg.lstsq(V, Y, rcond=None)
resid = Y - V @ beta
mean = Y.mean(0)
std = resid.std(0, ddof=1)
vals = np.zeros_like(mean); nz = std > 0
vals[nz] = mean[nz] / std[nz]
out = np.zeros(sh[:-1], np.float32); out[m] = vals
nib.save(nib.Nifti1Image(out, img.affine), out_file)
"""


def compute_tsnr(bold4d: Path, outdir: Path, sif: Path | None = None,
                 mask: Path | None = None, n_vols: int | None = None,
                 poly: int = 2) -> Path:
    """
    tSNR = temporal mean / temporal std from a 4D BOLD image, computed on the
    BOLD's own grid (never resampled). Returns path to the tSNR map.

    Drift removal: with `sif` (an fMRIPrep .sif) and a long-enough run, a
    2nd-order polynomial detrend is applied per voxel before the std — the same
    method nipype/MRIQC use — run inside the container (needs numpy+nibabel),
    restricted to `mask` (fMRIPrep brain mask) when given. Without `sif`, or on
    a short run, it falls back to plain FSL Tmean/Tstd with a warning (no
    detrend → tSNR slightly underestimated).
    """
    tmean = outdir / "_tmp_tmean.nii.gz"
    tstd  = outdir / "_tmp_tstd.nii.gz"
    tsnr  = outdir / "_tmp_tsnr.nii.gz"

    long_enough = not n_vols or n_vols >= 3 * (poly + 1)   # need > free params
    if sif and sif.exists() and long_enough:
        runner = _find_apptainer() or "apptainer"
        bind_dirs = {bold4d.parent, outdir}
        if mask and mask.exists():
            bind_dirs.add(mask.parent)
        binds = [x for d in bind_dirs for x in ("--bind", f"{d}:{d}")]
        _run([
            runner, "exec", "--cleanenv", *binds,
            str(sif), "python3", "-c", _POLY_TSNR_PY,
            str(bold4d), str(mask) if (mask and mask.exists()) else "-",
            str(tsnr), str(poly),
        ], f"poly-{poly} detrend + tSNR (in fMRIPrep container)")
        return tsnr

    if sif:
        print(f"  WARN  run too short for poly-{poly} detrend (n_vols={n_vols}) "
              "— plain Tmean/Tstd")
    else:
        print("  WARN  no fMRIPrep .sif — plain Tmean/Tstd, no drift removal")
    _run(["fslmaths", str(bold4d), "-Tmean", str(tmean)], "fslmaths: temporal mean")
    _run(["fslmaths", str(bold4d), "-Tstd", str(tstd)], "fslmaths: temporal std")
    _run(["fslmaths", str(tmean), "-div", str(tstd), str(tsnr)], "fslmaths: tSNR")
    tmean.unlink(missing_ok=True)
    tstd.unlink(missing_ok=True)
    return tsnr


# ── Atlas registration ─────────────────────────────────────────────────────────

def _dims(img: Path) -> str:
    return " ".join(_run(["fslval", str(img), d], quiet=True).strip()
                    for d in ("dim1", "dim2", "dim3"))


def register_atlases_ants(ref_grid: Path, mni2t1_h5: Path, sif: Path, outdir: Path,
                          fsl: dict, cache_prefix: Path | None = None) -> tuple:
    """
    Put the Harvard-Oxford atlases into `ref_grid` space using fMRIPrep's OWN
    nonlinear MNI→T1w warp (`mni2t1_h5`, the composite h5 from anat/), applied
    with `antsApplyTransforms` inside the fMRIPrep container. The BOLD is already
    in T1w space, so this warp lands the atlas correctly — a plain 12-DOF flirt
    T1w→MNI drops ~a quarter of the cortical labels on 7T MP2RAGE data.

    (The FSL HO atlas is in MNI152NLin6Asym and the warp expects NLin2009cAsym;
    the ~1-2 mm difference is immaterial for QC ROI means.)

    Results are cached at `<cache_prefix>_{cort,sub}.nii.gz` and reused for later
    runs whose `ref_grid` has the same matrix size.
    Returns (cort_in_grid_path, sub_in_grid_path).
    """
    cort = (cache_prefix.with_name(cache_prefix.name + "_cort.nii.gz")
            if cache_prefix else outdir / "_tmp_cort_in_grid.nii.gz")
    sub  = (cache_prefix.with_name(cache_prefix.name + "_sub.nii.gz")
            if cache_prefix else outdir / "_tmp_sub_in_grid.nii.gz")

    if cache_prefix and cort.exists() and sub.exists() and _dims(cort) == _dims(ref_grid):
        print(f"  + atlas: reuse cached {cort.name}")
        return cort, sub

    runner = _find_apptainer() or "apptainer"
    bind_dirs = {ref_grid.parent, mni2t1_h5.parent, outdir,
                 Path(fsl["cort_atlas"]).parent, cort.parent}
    binds = [x for d in bind_dirs for x in ("--bind", f"{d}:{d}")]
    for atlas_key, out_path, label in [("cort_atlas", cort, "cortical"),
                                       ("sub_atlas",  sub,  "subcortical")]:
        _run([
            runner, "exec", "--cleanenv", *binds, str(sif),
            "antsApplyTransforms", "-d", "3", "-e", "0", "-n", "GenericLabel",
            "-i", str(fsl[atlas_key]), "-r", str(ref_grid),
            "-t", str(mni2t1_h5), "-o", str(out_path),
        ], f"antsApplyTransforms: {label} HO atlas → BOLD grid")
    return cort, sub


def register_atlases(reg_src: Path, ref_grid: Path, outdir: Path, fsl: dict,
                     src2mni_mat: Path | None = None) -> tuple:
    """
    Fallback linear path (used by the interactive `main()` only): register
    `reg_src` to MNI152 with a 12-DOF flirt, invert, resample the HO atlases
    onto `ref_grid` nearest-neighbour. `delta tsnr` uses register_atlases_ants.
    """
    own_mat  = src2mni_mat is None
    src2mni  = src2mni_mat or (outdir / "_tmp_src2mni.mat")
    mni2src  = outdir / "_tmp_mni2src.mat"
    cort     = outdir / "_tmp_cort_in_grid.nii.gz"
    sub      = outdir / "_tmp_sub_in_grid.nii.gz"

    if not src2mni.exists():
        _run(["flirt", "-in", str(reg_src), "-ref", str(fsl["mni_brain"]),
              "-omat", str(src2mni), "-dof", "12"],
             f"flirt: {reg_src.name} → MNI152 (12 DOF)")
    _run(["convert_xfm", "-omat", str(mni2src), "-inverse", str(src2mni)],
         "convert_xfm: invert → MNI152 → target grid")
    for atlas_key, out_path, label in [("cort_atlas", cort, "cortical"),
                                       ("sub_atlas",  sub,  "subcortical")]:
        _run(["flirt", "-in", str(fsl[atlas_key]), "-ref", str(ref_grid),
              "-applyxfm", "-init", str(mni2src),
              "-interp", "nearestneighbour", "-o", str(out_path)],
             f"flirt: {label} atlas → target grid")
    mni2src.unlink(missing_ok=True)
    if own_mat:
        src2mni.unlink(missing_ok=True)
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
            ["fslstats", str(tsnr), "-k", str(tmp_mask), "-M"],
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


# ── Batch mode: `delta tsnr` ──────────────────────────────────────────────────
#
# Scans an fMRIPrep derivatives tree, computes a tSNR map for every preprocessed
# BOLD run it finds (any number of sessions/tasks/runs), and keeps ONE tracking
# table per subject with mean tSNR per Harvard-Oxford region across every run.
#
# Outputs, per subject:
#   <derivatives>/sub-<id>/tsnr_maps/
#     sub-<id>_ses-<s>_task-<t>[_run-<r>]_space-<sp>_tsnr.nii.gz   ← tSNR map, one per run
#     sub-<id>_tsnr.tsv   ← rows = region, cols = region · atlas · index · <ses_task_run>/run
#                            (`index` = the atlas NIfTI label value for that region)


def _parse_entities(name: str) -> dict:
    """Parse BIDS `key-value` entities from a filename stem into a dict."""
    ent = {}
    for tok in name.split("_"):
        if "-" in tok:
            k, v = tok.split("-", 1)
            ent[k] = v
    return ent


def _discover_bold_pairs(derivatives: Path, space: str, subjects: list[str] | None):
    """Yield (subject_dir_name, bold4d, boldref, stem) for each preproc BOLD run.

    `space` filters on the `space-<space>` entity; pass "native" for files with
    no space entity (fMRIPrep boldref/preproc in native BOLD grid).
    """
    sub_dirs = sorted(d for d in derivatives.iterdir()
                      if d.is_dir() and d.name.startswith("sub-"))
    if subjects:
        want = {s if s.startswith("sub-") else f"sub-{s}" for s in subjects}
        sub_dirs = [d for d in sub_dirs if d.name in want]

    for sub_dir in sub_dirs:
        for bold in sorted(sub_dir.rglob("func/*_desc-preproc_bold.nii.gz")):
            stem = bold.name[: -len("_desc-preproc_bold.nii.gz")]
            ent = _parse_entities(stem)
            if space == "native":
                if "space" in ent:
                    continue
            elif ent.get("space") != space:
                continue
            boldref = bold.with_name(f"{stem}_boldref.nii.gz")
            if not boldref.exists():
                print(f"  WARN  no boldref for {bold.name} — skipping")
                continue
            yield sub_dir.name, bold, boldref, stem


def _run_column(ent: dict) -> str:
    """Build the per-run column name from BIDS entities, e.g. 'ses-02_task-podcast_run-02'."""
    parts = []
    if "ses" in ent:
        parts.append(f"ses-{ent['ses']}")
    parts.append(f"task-{ent.get('task', 'unknown')}")
    if "run" in ent:
        parts.append(f"run-{ent['run']}")
    return "_".join(parts)


_TSV_FIXED = 3   # region, atlas, index


def update_subject_tsnr_tsv(
    tsv_path: Path, run_col: str, cort_tsnr: dict, sub_tsnr: dict,
    cort_idx: dict, sub_idx: dict,
) -> None:
    """Merge one run's per-region tSNR into the subject's tracking TSV.

    Layout: rows = region, columns = `region <tab> atlas <tab> index <tab> <run_col>...`
    where `index` is that region's NIfTI label value in its atlas — the value to
    threshold the warped `sub-<id>_atlas-HO_space-BOLD_{cort,sub}.nii.gz` on.
    Cortical and subcortical numbering are independent (both start at 1).
    Existing run columns are preserved; `run_col` is added or overwritten.
    """
    regions = [(r, "cortical") for r in cort_tsnr] + [(r, "subcortical") for r in sub_tsnr]
    new_vals = {("cortical", r): v for r, v in cort_tsnr.items()}
    new_vals.update({("subcortical", r): v for r, v in sub_tsnr.items()})
    idx = {"cortical": cort_idx, "subcortical": sub_idx}

    # read existing table → {region: {col: "value string"}}
    table: dict[str, dict[str, str]] = {}
    old_cols: list[str] = []
    if tsv_path.exists():
        rows = [ln.split("\t") for ln in tsv_path.read_text().splitlines() if ln]
        if rows:
            old_cols = rows[0][_TSV_FIXED:]
            for row in rows[1:]:
                cells = row + [""] * (_TSV_FIXED + len(old_cols) - len(row))
                table[cells[0]] = dict(zip(old_cols, cells[_TSV_FIXED:]))

    cols = old_cols + ([run_col] if run_col not in old_cols else [])
    for region, atlas in regions:
        v = new_vals[(atlas, region)]
        table.setdefault(region, {})[run_col] = "" if math.isnan(v) else f"{v:.2f}"

    lines = ["region\tatlas\tindex\t" + "\t".join(cols)]
    for region, atlas in regions:
        cells = [table.get(region, {}).get(c, "") for c in cols]
        lines.append(f"{region}\t{atlas}\t{idx[atlas].get(region, '')}\t" + "\t".join(cells))

    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.write_text("\n".join(lines) + "\n")


def run_tsnr_batch(args) -> None:
    """Entry point for `delta tsnr` — called by cli.py cmd_tsnr(args)."""
    derivatives  = Path(args.derivatives).resolve()
    fsl          = _fsl_paths(Path(args.fsldir))
    space        = args.space
    fmriprep_sif = Path(args.fmriprep_sif).resolve()

    if not derivatives.exists():
        print(f"ERROR: derivatives dir not found: {derivatives}")
        sys.exit(1)
    if not fmriprep_sif.exists():
        print(f"WARN: fMRIPrep .sif not found ({fmriprep_sif}) — tSNR will skip drift removal")
        fmriprep_sif = None
    check_environment(fsl)
    cort_labels = parse_xml_labels(fsl["cort_xml"])
    sub_labels  = parse_xml_labels(fsl["sub_xml"])

    pairs = list(_discover_bold_pairs(derivatives, space, args.subjects))
    if not pairs:
        print(f"No *_space-{space}_desc-preproc_bold.nii.gz found under {derivatives}")
        sys.exit(1)

    print(f"\nDELTA tSNR — {len(pairs)} BOLD run(s)")
    print(f"  Derivatives: {derivatives}")
    print(f"  Space:       {space}")
    print(f"  Detrend:     poly-2 (nipype/MRIQC method)" if fmriprep_sif else "  Detrend:     none (no sif)")
    print(f"  Atlas:       Harvard-Oxford via fMRIPrep nonlinear MNI→T1w warp (antsApplyTransforms)\n")

    done, skipped, failed = 0, 0, []

    for sub_name, bold4d, mean_bold, stem in pairs:
        outdir   = derivatives / sub_name / "tsnr_maps"
        tsnr_out = outdir / f"{stem}_tsnr.nii.gz"
        ent      = _parse_entities(stem)
        run_col  = _run_column(ent)
        tsv_path = outdir / f"{sub_name}_tsnr.tsv"

        if tsnr_out.exists() and not args.force:
            print(f"  SKIP  {stem} (exists — use --force)")
            skipped += 1
            continue

        nv = _nvols(bold4d)
        if nv < args.min_vols:
            print(f"  SKIP  {stem} — only {nv} volumes (< --min-vols {args.min_vols})")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  DRY   {stem} ({nv} vols) → {tsnr_out}  [{tsv_path.name} col {run_col}]")
            continue

        # fMRIPrep's own nonlinear MNI→T1w warp — BOLD is in T1w space, so this
        # places the atlas correctly (a linear flirt drops ~1/4 of the labels)
        h5 = next((derivatives / sub_name / "anat").glob(
            f"{sub_name}*_from-MNI152NLin2009cAsym_to-T1w_mode-image_xfm.h5"), None)
        if h5 is None or fmriprep_sif is None:
            print(f"  FAIL  {stem}: need the fMRIPrep MNI→T1w .h5 + sif for atlas registration")
            failed.append(stem)
            continue

        print(f"  START {stem}")
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            # ponytail: sequential — helpers share _tmp_* names in outdir, no parallel
            mask = bold4d.with_name(stem + "_desc-brain_mask.nii.gz")
            tsnr_tmp = compute_tsnr(bold4d, outdir, sif=fmriprep_sif,
                                    mask=mask if mask.exists() else None, n_vols=nv)
            cort_in_bold, sub_in_bold = register_atlases_ants(
                mean_bold, h5, fmriprep_sif, outdir, fsl,
                cache_prefix=outdir / f"{sub_name}_atlas-HO_space-BOLD",
            )
            cort_tsnr = extract_region_tsnr(cort_in_bold, tsnr_tmp, cort_labels, outdir)
            sub_tsnr  = extract_region_tsnr(sub_in_bold, tsnr_tmp, sub_labels, outdir)

            tsnr_tmp.rename(tsnr_out)

            cort_idx = {name: val for val, name in cort_labels.items()}
            sub_idx  = {name: val for val, name in sub_labels.items()}
            update_subject_tsnr_tsv(tsv_path, run_col, cort_tsnr, sub_tsnr, cort_idx, sub_idx)
            print(f"  DONE  {stem} → {tsnr_out.name}")
            done += 1
        except SystemExit:
            failed.append(stem)
        except Exception as exc:
            print(f"  FAIL  {stem}: {exc}")
            failed.append(stem)

    print(f"\nSummary: {done} done, {skipped} skipped, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)


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
    parser.add_argument("--fmriprep-sif", default=None, dest="fmriprep_sif",
                        help="fMRIPrep .sif for poly-2 detrend (nipype/MRIQC method); omit for plain Tmean/Tstd")
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
    tsnr_tmp = compute_tsnr(bold4d, outdir, sif=(Path(args.fmriprep_sif) if args.fmriprep_sif else None),
                            n_vols=_nvols(bold4d))

    # ── Step 2: Atlas registration ──────────────────────────────────────────────
    print("[2/4] Registering Harvard-Oxford atlas to BOLD space...")
    cort_in_bold, sub_in_bold = register_atlases(mean_bold, mean_bold, outdir, fsl)

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
