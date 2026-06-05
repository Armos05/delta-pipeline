#!/usr/bin/env python3
"""
first_level_base.py — Shared infrastructure for DELTA first-level GLM models.

Provides:
  - fMRIPrep derivative path resolution
  - behavioural file loading/merging
  - confound loading (motion + aCompCor + FD + optional RETROICOR)
  - Nilearn FirstLevelModel fitting
  - contrast computation and output saving
  - common CLI orchestration helper for model-specific wrappers
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from nilearn import image, plotting
from nilearn.glm import threshold_stats_img
from nilearn.glm.first_level import FirstLevelModel
from nilearn.image import load_img, new_img_like
from nilearn.plotting import plot_design_matrix

TASK = "sentencecompletion"
TASK_LABEL = f"task-{TASK}"
DEFAULT_SPACE = "MNI152NLin2009cAsym_res-2"
TR = 1.18
SLICE_TIME_REF = 0.59

CONFOUND_COLS = [
    "trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z",
    "trans_x_derivative1", "trans_y_derivative1", "trans_z_derivative1",
    "rot_x_derivative1", "rot_y_derivative1", "rot_z_derivative1",
    "a_comp_cor_00", "a_comp_cor_01", "a_comp_cor_02",
    "a_comp_cor_03", "a_comp_cor_04",
    "framewise_displacement",
]


def resolve_fmriprep_paths(derivatives: Path, subject: str, session: str, space: str) -> dict[str, Path]:
    fmriprep_root = derivatives / "fmriprep"
    func_dir = fmriprep_root / subject / session / "func"
    prefix = f"{subject}_{session}_{TASK_LABEL}"
    return {
        "func_dir": func_dir,
        "bold": func_dir / f"{prefix}_space-{space}_desc-preproc_bold.nii.gz",
        "brain_mask": func_dir / f"{prefix}_space-{space}_desc-brain_mask.nii.gz",
        "confounds": func_dir / f"{prefix}_desc-confounds_timeseries.tsv",
        "retroicor": func_dir / f"{prefix}_desc-retroicor_regressors.tsv",
    }


def check_required_paths(paths: dict[str, Path], skip_keys: tuple = ()) -> None:
    missing = [str(p) for k, p in paths.items() if k not in skip_keys and not p.exists()]
    if missing:
        print("ERROR: The following required files are missing:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)


def load_behavioural_data(events_csv: Path, sentences_xlsx: Path, prior_csv: Path) -> pd.DataFrame:
    raw = pd.read_csv(events_csv)
    prior_df = pd.read_csv(prior_csv)
    if "trial_num" not in raw.columns:
        raw["trial_num"] = np.arange(1, len(raw) + 1)
    raw = raw.merge(prior_df[["trial_num", "lognu_mean"]], on="trial_num", how="left")

    sentences_df = pd.read_excel(sentences_xlsx)[["Sentence_audio_file", "entropy"]]
    sentences_df["key"] = sentences_df["Sentence_audio_file"].str.split("/").str[-1]
    raw["key"] = raw["sentence_file"].str.split("/").str[-1]
    raw = raw.merge(sentences_df[["key", "entropy"]], on="key", how="left").drop(columns="key")
    raw["channels"] = raw["noise_condition"].str.extract(r"ch(\d+)").astype(float)
    return raw


def load_confounds(confounds_tsv: Path, retroicor_tsv: Path | None) -> pd.DataFrame:
    conf_df = pd.read_csv(confounds_tsv, sep="\t")
    conf_cols = [c for c in CONFOUND_COLS if c in conf_df.columns]
    confounds = conf_df[conf_cols].copy()
    confounds.iloc[0] = confounds.iloc[0].fillna(0)
    confounds = confounds.fillna(0)
    print(f"  Motion/physiological confounds loaded: {len(conf_cols)} columns")

    if retroicor_tsv is not None and retroicor_tsv.exists():
        physio_df = pd.read_csv(retroicor_tsv, sep="\t")
        physio_df = physio_df.dropna(axis=1, how="all")
        nan_count = physio_df.isna().sum().sum()
        if nan_count > 0:
            print(f"  WARNING: {nan_count} NaN values in RETROICOR regressors — imputing.")
            physio_df = physio_df.ffill().bfill().fillna(0)
        if len(physio_df) == len(confounds):
            confounds = pd.concat([confounds, physio_df], axis=1)
            print(f"  RETROICOR regressors appended: {physio_df.shape[1]} columns")
        else:
            print(f"  WARNING: RETROICOR length ({len(physio_df)}) ≠ BOLD ({len(confounds)}) — skipping physiological denoising.")
    else:
        print("  WARNING: RETROICOR regressors not found — run `delta physio` first for full denoising.")
    return confounds


def fit_glm(bold_path: Path, mask_path: Path, events: pd.DataFrame, confounds: pd.DataFrame) -> FirstLevelModel:
    fmri_glm = FirstLevelModel(
        t_r=TR,
        slice_time_ref=SLICE_TIME_REF,
        hrf_model="spm",
        drift_model="cosine",
        high_pass=1.0 / 128.0,
        mask_img=str(mask_path),
        standardize=False,
        signal_scaling=0,
        noise_model="ar1",
        minimize_memory=True,
        memory="nilearn_cache",
        n_jobs=1,
        verbose=1,
    )
    print(f"  Loading BOLD into RAM: {bold_path.name}")
    raw_img = load_img(str(bold_path))
    bold_data = raw_img.get_fdata()
    bold_img_safe = new_img_like(raw_img, bold_data, affine=raw_img.affine)
    print("  Fitting GLM …")
    fmri_glm.fit(run_imgs=bold_img_safe, events=events, confounds=confounds)
    print("  GLM fit complete.")
    return fmri_glm


def save_design_matrix(fmri_glm: FirstLevelModel, output_dir: Path, subject: str, session: str, model_name: str) -> None:
    prefix = f"{subject}_{session}_{TASK_LABEL}_model-{model_name}"
    dm = fmri_glm.design_matrices_[0]
    dm.to_csv(output_dir / f"{prefix}_design_matrix.csv")
    fig, ax = plt.subplots(figsize=(18, 8))
    plot_design_matrix(dm, axes=ax)
    ax.set_title(f"First-level design matrix — {model_name}", fontsize=12)
    plt.tight_layout()
    fig.savefig(output_dir / f"{prefix}_design_matrix.png", dpi=150)
    plt.close(fig)
    print(f"  Design matrix saved: {prefix}_design_matrix.{{csv,png}}")


def compute_and_save_contrasts(fmri_glm: FirstLevelModel, gm_mask_path: Path | None, output_dir: Path,
                               subject: str, session: str, model_name: str, contrasts: dict[str, str]) -> None:
    apply_mask = gm_mask_path is not None and gm_mask_path.exists()
    if not apply_mask:
        print("  NOTE: no GM mask applied — saving whole-brain maps.")
    prefix = f"{subject}_{session}_{TASK_LABEL}_model-{model_name}"

    for name, contrast_def in contrasts.items():
        print(f"  Contrast: {name}")
        z_map = fmri_glm.compute_contrast(contrast_def, stat_type="t", output_type="z_score")
        t_map = fmri_glm.compute_contrast(contrast_def, stat_type="t", output_type="stat")
        eff_map = fmri_glm.compute_contrast(contrast_def, stat_type="t", output_type="effect_size")
        var_map = fmri_glm.compute_contrast(contrast_def, stat_type="t", output_type="effect_variance")

        if apply_mask:
            z_map = image.math_img("img1 * img2", img1=z_map, img2=str(gm_mask_path))
            t_map = image.math_img("img1 * img2", img1=t_map, img2=str(gm_mask_path))

        out_prefix = output_dir / f"{prefix}_contrast-{name}"
        for img_type, img in [
            ("z_score", z_map), ("t_stat", t_map),
            ("effect_size", eff_map), ("effect_variance", var_map),
        ]:
            img.to_filename(str(out_prefix) + f"_{img_type}.nii.gz")

        thr_z, threshold_val = threshold_stats_img(z_map, alpha=0.05, height_control="fpr")
        thr_z.to_filename(str(out_prefix) + "_z_score_FDR.nii.gz")
        print(f"    FDR threshold: z > {threshold_val:.2f}")

        plotting.plot_glass_brain(
            thr_z, display_mode="lyrz", colorbar=True, plot_abs=False,
            title=f"{name} (p<0.05 FDR, z>{threshold_val:.1f})",
            output_file=str(out_prefix) + "_glass_brain_FDR.png",
        )
        fig = plt.figure(figsize=(14, 4))
        plotting.plot_glass_brain(
            z_map, threshold=3.1, colorbar=True, display_mode="lyrz",
            title=f"z-map: {name} (z > 3.1, uncorr.)", figure=fig,
        )
        fig.savefig(str(out_prefix) + "_glass_brain_uncorr.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"    → Saved 4 NIfTI + 2 PNG files for: {name}")


def run_model(args, model_name: str, build_events_func, contrasts: dict[str, str],
              needs_binned_entropy: bool = False) -> None:
    bids = Path(args.bids).resolve()
    derivatives = Path(args.derivatives).resolve()
    subject = args.subject
    session = args.session
    space = getattr(args, "space", DEFAULT_SPACE)
    force = getattr(args, "force", False)

    output_root = Path(args.output).resolve() if getattr(args, "output", None) else derivatives / "first_level"
    output_dir = output_root / subject / session
    output_dir.mkdir(parents=True, exist_ok=True)

    sentinel = output_dir / f"{subject}_{session}_{TASK_LABEL}_model-{model_name}_design_matrix.csv"
    if sentinel.exists() and not force:
        print(f"SKIP {subject}/{session} ({model_name}) — output exists (use --force to redo).")
        return

    print(f"\nDELTA First-Level — {subject}/{session} ({model_name})")
    print(f"  BIDS:        {bids}")
    print(f"  Derivatives: {derivatives}")
    print(f"  Space:       {space}")
    print(f"  Output:      {output_dir}\n")

    fmriprep_paths = resolve_fmriprep_paths(derivatives, subject, session, space)
    check_required_paths(fmriprep_paths, skip_keys=("retroicor",))

    mask_path = Path(args.gm_mask).resolve() if getattr(args, "gm_mask", None) and Path(args.gm_mask).exists() else fmriprep_paths["brain_mask"]
    print(f"  Mask: {mask_path.name}")

    behav_paths = {
        "events_csv": Path(args.events_csv).resolve(),
        "sentences_xlsx": Path(args.sentences_xlsx).resolve(),
        "prior_csv": Path(args.prior_csv).resolve(),
    }
    if needs_binned_entropy:
        behav_paths["binned_entropy_csv"] = Path(args.binned_entropy_csv).resolve()
    check_required_paths(behav_paths)

    print("  Loading behavioural data …")
    trial_df = load_behavioural_data(
        events_csv=behav_paths["events_csv"],
        sentences_xlsx=behav_paths["sentences_xlsx"],
        prior_csv=behav_paths["prior_csv"],
    )

    print("  Building events DataFrame …")
    if needs_binned_entropy:
        binned_entropy_df = pd.read_csv(behav_paths["binned_entropy_csv"])
        events, center_value = build_events_func(trial_df, binned_entropy_df)
        print(f"  Mean entropy_sum (centering value): {center_value:.4f}")
    else:
        events, center_value = build_events_func(trial_df)
        print(f"  Mean prior (centering value): {center_value:.4f}")
    print(f"  Events per trial_type:\n{events['trial_type'].value_counts().sort_index().to_string()}\n")

    print("  Loading confounds …")
    confounds = load_confounds(fmriprep_paths["confounds"], fmriprep_paths["retroicor"])

    fmri_glm = fit_glm(fmriprep_paths["bold"], mask_path, events, confounds)
    save_design_matrix(fmri_glm, output_dir, subject, session, model_name)

    print("\n  Computing contrasts …")
    compute_and_save_contrasts(fmri_glm, mask_path, output_dir, subject, session, model_name, contrasts)
    print(f"\n✓ First-level complete. Outputs in: {output_dir}")
