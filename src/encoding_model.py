#!/usr/bin/env python3
"""
encoding_model.py — DELTA voxelwise encoding model for the listening task.

Pipeline
────────
1.  Load preprocessed BOLD from fMRIPrep (listening task, T1w or MNI space).
2.  Extract voxel time-series within a brain mask; z-score each voxel across time.
3.  Load forced-alignment TextGrid + TR-timing report for each story.
4.  Build word-level DataSequence objects and extract contextual LLM features
    (LLaMA/GPT-style) using a sliding-window accumulator.
5.  Downsample feature matrices to TR resolution (Lanczos interpolation, window=3).
6.  Apply FIR delay embedding (delays 1-4 TRs) to model haemodynamic lag.
7.  Split stories into train/test sets and run bootstrap ridge regression
    (ridge_utils.bootstrap_ridge) with log-spaced alphas.
8.  Save per-voxel prediction correlations, regression weights, and best alphas
    as numpy arrays in the subject encoding output directory.
9.  (Optional) produce glass-brain PNG of voxelwise prediction correlation.

Outputs (derivatives/encoding/<subject>/<session>/)
────────────────────────────────────────────────────
  corrs.npy           — shape (n_voxels,)  prediction correlation per voxel
  weights.npy         — shape (n_features_delayed, n_voxels)  regression weights
  best_alphas.npy     — shape (n_voxels,)  selected regularisation parameter
  bootstrap_corrs.npy — shape (n_alphas, n_voxels, n_boots)  CV correlation matrix
  voxel_mask.npy      — shape (n_voxels, 3) MNI voxel coordinates (if MNI space)
  corr_glass_brain.png — glass-brain visualisation of corrs.npy (if mask available)

Usage (standalone)
──────────────────
  python3 src/encoding_model.py \
      --bids         /data/bids \
      --derivatives  /data/derivatives \
      --subject      sub-01 \
      --session      ses-01 \
      --stories      stimStory01 stimStory02 stimStory03 \
      --test-stories stimStory03 \
      --grid-dir     /data/bids/stimuli/grids \
      --tr-report-dir /data/bids/stimuli/tr_reports \
      --model-name   meta-llama/Llama-2-7b-hf \
      --layer        16 \
      --lookback1    4 \
      --lookback2    20 \
      [--space T1w] \
      [--delays 1 2 3 4] \
      [--n-boots 15] \
      [--output /data/derivatives/encoding]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from nilearn import image, plotting

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("encoding")


# ─────────────────────────────────────────────────────────────────────────────
# 1. fMRIPrep BOLD loading
# ─────────────────────────────────────────────────────────────────────────────

def load_bold_voxels(bold_path: Path, mask_path: Path, z_score: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Load preprocessed BOLD and extract voxel time-series within mask.

    Returns
    -------
    data : (T, V) float32 z-scored voxel array
    coords : (V, 3) integer voxel coordinates within the mask
    """
    bold_img = nib.load(str(bold_path))
    mask_img = nib.load(str(mask_path))

    bold_data = bold_img.get_fdata(dtype=np.float32)          # (X, Y, Z, T)
    mask_data = mask_img.get_fdata().astype(bool)              # (X, Y, Z)

    # Resample mask to BOLD space if shapes differ
    if bold_data.shape[:3] != mask_data.shape:
        log.warning("Mask shape %s ≠ BOLD shape %s — resampling mask.",
                    mask_data.shape, bold_data.shape[:3])
        mask_img = image.resample_to_img(mask_img, bold_img, interpolation="nearest")
        mask_data = mask_img.get_fdata().astype(bool)

    coords = np.argwhere(mask_data)                            # (V, 3)
    data = bold_data[coords[:, 0], coords[:, 1], coords[:, 2], :].T  # (T, V)

    if z_score:
        mu = data.mean(0, keepdims=True)
        sd = data.std(0, keepdims=True)
        sd[sd < 1e-10] = 1e-10
        data = (data - mu) / sd

    log.info("BOLD loaded: %d TRs × %d voxels", data.shape[0], data.shape[1])
    return data.astype(np.float32), coords


# ─────────────────────────────────────────────────────────────────────────────
# 2. Stimulus feature extraction
# ─────────────────────────────────────────────────────────────────────────────

def build_stimulus_features(
    stories: list[str],
    grid_dir: Path,
    tr_report_dir: Path,
    model_name: str,
    layer: int,
    lookback1: int,
    lookback2: int,
    audio_start_time: float = 0.0,
    cache_dir: Path | None = None,
) -> dict[str, np.ndarray]:
    """Build per-story TR-resolution LLM contextual feature matrices.

    Returns
    -------
    features : dict[story_name -> (T_story, n_features) float32 array]
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from ridge_utils.dsutils import make_word_ds
    from ridge_utils.stimulus_utils import load_grids_for_stories, load_generic_trfiles
    from ridge_utils.tokenization_helpers import (
        generate_efficient_feat_dicts_llama_NEW,
        convert_to_feature_mats_llama_NEW,
    )

    log.info("Loading tokenizer and model: %s (layer %d)", model_name, layer)
    cache = str(cache_dir) if cache_dir else None
    tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache)
    model = AutoModelForCausalLM.from_pretrained(
        model_name, output_hidden_states=True,
        torch_dtype=torch.float16, device_map="auto",
        cache_dir=cache,
    )
    model.eval()

    grids = load_grids_for_stories(stories, str(grid_dir))
    trfiles = load_generic_trfiles(stories, str(tr_report_dir))
    wordseqs = make_word_ds(grids, trfiles)

    log.info("Building feature accumulator dictionaries …")
    text_dict, text_dict2, text_dict3 = generate_efficient_feat_dicts_llama_NEW(
        wordseqs, tokenizer, lookback1, lookback2
    )

    log.info("Extracting hidden-state features (layer %d) …", layer)
    with torch.no_grad():
        for key, token_ids in text_dict3.items():
            inputs = torch.tensor([token_ids])
            if hasattr(model, "device"):
                inputs = inputs.to(model.device)
            outputs = model(inputs)
            hidden = outputs.hidden_states[layer][0, -1, :].cpu().float().numpy()
            text_dict3[key] = hidden

    log.info("Downsampling features to TR resolution …")
    feature_mats = convert_to_feature_mats_llama_NEW(
        wordseqs, tokenizer, lookback1, lookback2, text_dict3, audio_start_time
    )

    return {story: mat.astype(np.float32) for story, mat in feature_mats.items()}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Delay embedding
# ─────────────────────────────────────────────────────────────────────────────

def apply_delays(stim: np.ndarray, delays: list[int]) -> np.ndarray:
    """Create delayed (lagged) copies of stimulus features.

    Parameters
    ----------
    stim : (T, N) array
    delays : list of integer TR delays (e.g. [1, 2, 3, 4])

    Returns
    -------
    delayed : (T, N * len(delays))
    """
    from ridge_utils.utils import make_delayed
    return make_delayed(stim, delays).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Ridge regression
# ─────────────────────────────────────────────────────────────────────────────

def run_ridge(
    train_stim: np.ndarray,
    test_stim: np.ndarray,
    train_resp: np.ndarray,
    test_resp: np.ndarray,
    alphas: np.ndarray,
    n_boots: int,
    chunk_len: int,
    n_chunks: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Bootstrapped ridge regression.

    Returns
    -------
    weights     : (N_delayed, V)
    corrs       : (V,)
    best_alphas : (V,)
    boot_corrs  : (A, V, B)
    """
    from ridge_utils.ridge import bootstrap_ridge

    wt, corrs, valphas, boot_corrs, _ = bootstrap_ridge(
        Rstim=train_stim,
        Rresp=train_resp,
        Pstim=test_stim,
        Presp=test_resp,
        alphas=alphas,
        nboots=n_boots,
        chunklen=chunk_len,
        nchunks=n_chunks,
        use_corr=True,
        return_wt=True,
    )
    return wt, corrs, valphas, boot_corrs


# ─────────────────────────────────────────────────────────────────────────────
# 5. Output saving
# ─────────────────────────────────────────────────────────────────────────────

def save_outputs(
    output_dir: Path,
    corrs: np.ndarray,
    weights: np.ndarray,
    best_alphas: np.ndarray,
    boot_corrs: np.ndarray,
    coords: np.ndarray,
    mask_path: Path | None,
    subject: str,
    session: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    np.save(output_dir / "corrs.npy", corrs)
    np.save(output_dir / "weights.npy", weights)
    np.save(output_dir / "best_alphas.npy", best_alphas)
    np.save(output_dir / "bootstrap_corrs.npy", boot_corrs)
    np.save(output_dir / "voxel_mask.npy", coords)
    log.info("Saved corrs, weights, best_alphas, bootstrap_corrs, voxel_mask to %s", output_dir)

    if mask_path is not None and mask_path.exists():
        _save_glass_brain(corrs, coords, mask_path, output_dir, subject, session)


def _save_glass_brain(
    corrs: np.ndarray,
    coords: np.ndarray,
    mask_path: Path,
    output_dir: Path,
    subject: str,
    session: str,
) -> None:
    mask_img = nib.load(str(mask_path))
    corr_vol = np.zeros(mask_img.shape[:3], dtype=np.float32)
    corr_vol[coords[:, 0], coords[:, 1], coords[:, 2]] = corrs
    corr_img = nib.Nifti1Image(corr_vol, mask_img.affine, mask_img.header)
    nii_path = output_dir / f"{subject}_{session}_task-listening_encoding_corrs.nii.gz"
    corr_img.to_filename(str(nii_path))

    fig = plt.figure(figsize=(14, 4))
    plotting.plot_glass_brain(
        corr_img,
        threshold=0.1,
        colorbar=True,
        display_mode="lyrz",
        title=f"{subject}/{session} encoding model prediction correlation",
        figure=fig,
        vmin=-0.3,
        vmax=0.5,
        plot_abs=False,
    )
    png_path = output_dir / "corr_glass_brain.png"
    fig.savefig(str(png_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("Saved glass-brain PNG: %s", png_path)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Main orchestration
# ─────────────────────────────────────────────────────────────────────────────

def run_encoding_model(args) -> None:
    bids = Path(args.bids).resolve()
    derivatives = Path(args.derivatives).resolve()
    subject = args.subject
    session = getattr(args, "session", "ses-01")
    space = getattr(args, "space", "T1w")
    force = getattr(args, "force", False)
    stories = list(args.stories)
    test_stories = list(args.test_stories)
    train_stories = [s for s in stories if s not in test_stories]

    output_root = Path(args.output).resolve() if getattr(args, "output", None) else derivatives / "encoding"
    output_dir = output_root / subject / session
    sentinel = output_dir / "corrs.npy"
    if sentinel.exists() and not force:
        log.info("SKIP %s/%s — output exists (use --force to redo).", subject, session)
        return

    log.info("\nDELTA Encoding Model — %s/%s", subject, session)
    log.info("  Train stories: %s", train_stories)
    log.info("  Test  stories: %s", test_stories)
    log.info("  Space: %s", space)

    # ── Paths ────────────────────────────────────────────────────────────────
    fmriprep_func = derivatives / "fmriprep" / subject / session / "func"
    task_label = "task-listening"

    bold_path = fmriprep_func / f"{subject}_{session}_{task_label}_space-{space}_desc-preproc_bold.nii.gz"
    mask_path = fmriprep_func / f"{subject}_{session}_{task_label}_space-{space}_desc-brain_mask.nii.gz"
    gm_mask = Path(args.gm_mask).resolve() if getattr(args, "gm_mask", None) and Path(args.gm_mask).exists() else mask_path

    for p in [bold_path, mask_path]:
        if not p.exists():
            log.error("Required file not found: %s", p)
            sys.exit(1)

    # ── Load BOLD ────────────────────────────────────────────────────────────
    log.info("Loading BOLD: %s", bold_path.name)
    bold_data, coords = load_bold_voxels(bold_path, gm_mask)

    # ── Stimulus features ────────────────────────────────────────────────────
    grid_dir = Path(args.grid_dir).resolve()
    tr_report_dir = Path(args.tr_report_dir).resolve()
    cache_dir = Path(args.cache_dir).resolve() if getattr(args, "cache_dir", None) else None
    audio_start = float(getattr(args, "audio_start_time", 0.0))

    log.info("Extracting LLM features …")
    feature_mats = build_stimulus_features(
        stories=stories,
        grid_dir=grid_dir,
        tr_report_dir=tr_report_dir,
        model_name=args.model_name,
        layer=int(args.layer),
        lookback1=int(args.lookback1),
        lookback2=int(args.lookback2),
        audio_start_time=audio_start,
        cache_dir=cache_dir,
    )

    # ── Align BOLD to stories ────────────────────────────────────────────────
    # Assumes BOLD runs are concatenated in story order; split by story TR count.
    story_tr_counts = {st: feature_mats[st].shape[0] for st in stories}
    total_stim_trs = sum(story_tr_counts.values())
    if bold_data.shape[0] < total_stim_trs:
        log.error(
            "BOLD has %d TRs but stimulus requires %d TRs total. "
            "Check that the correct BOLD run is specified and that stories match.",
            bold_data.shape[0], total_stim_trs,
        )
        sys.exit(1)
    # Trim BOLD to stimulus length (discard extra volumes)
    bold_data = bold_data[:total_stim_trs]

    # Build per-story BOLD slices
    bold_stories: dict[str, np.ndarray] = {}
    offset = 0
    for st in stories:
        n = story_tr_counts[st]
        bold_stories[st] = bold_data[offset:offset + n]
        offset += n

    # ── Delay embedding ──────────────────────────────────────────────────────
    delays = list(args.delays) if hasattr(args, "delays") and args.delays else [1, 2, 3, 4]
    log.info("Applying FIR delay embedding: delays = %s TRs", delays)

    def prep(story_list: list[str]) -> tuple[np.ndarray, np.ndarray]:
        stims = [apply_delays(feature_mats[s], delays) for s in story_list]
        resps = [bold_stories[s] for s in story_list]
        stim = np.vstack(stims)
        resp = np.vstack(resps)
        # z-score stimulus features across time (required by ridge)
        mu, sd = stim.mean(0), stim.std(0)
        sd[sd < 1e-10] = 1e-10
        return ((stim - mu) / sd).astype(np.float32), resp

    train_stim, train_resp = prep(train_stories)
    test_stim, test_resp = prep(test_stories)
    log.info("Train: stim %s  resp %s", train_stim.shape, train_resp.shape)
    log.info("Test:  stim %s  resp %s", test_stim.shape, test_resp.shape)

    # ── Ridge regression ─────────────────────────────────────────────────────
    alphas = np.logspace(1, 5, 20)
    n_boots = int(getattr(args, "n_boots", 15))
    chunk_len = int(getattr(args, "chunk_len", 10))
    n_chunks = int(getattr(args, "n_chunks", max(1, train_stim.shape[0] // (chunk_len * 5))))
    log.info("Bootstrap ridge: n_boots=%d  chunk_len=%d  n_chunks=%d  alphas=%d",
             n_boots, chunk_len, n_chunks, len(alphas))

    weights, corrs, best_alphas, boot_corrs = run_ridge(
        train_stim, test_stim, train_resp, test_resp, alphas, n_boots, chunk_len, n_chunks
    )
    log.info("Mean prediction correlation: %.4f  Max: %.4f", corrs.mean(), corrs.max())

    # ── Save ─────────────────────────────────────────────────────────────────
    save_outputs(output_dir, corrs, weights, best_alphas, boot_corrs, coords, gm_mask, subject, session)
    log.info("\n✓ Encoding model complete. Outputs in: %s", output_dir)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DELTA voxelwise encoding model (listening task)")
    p.add_argument("--bids", required=True, help="BIDS source directory")
    p.add_argument("--derivatives", required=True, help="Derivatives root")
    p.add_argument("--subject", required=True, help="Subject ID (e.g. sub-01)")
    p.add_argument("--session", default="ses-01", help="Session ID")
    p.add_argument("--stories", nargs="+", required=True, help="Story names (all stories, in run order)")
    p.add_argument("--test-stories", nargs="+", required=True, dest="test_stories",
                   help="Story names held out for test (remainder used for training)")
    p.add_argument("--grid-dir", required=True, dest="grid_dir",
                   help="Directory containing Praat TextGrid files (.TextGrid, one per story)")
    p.add_argument("--tr-report-dir", required=True, dest="tr_report_dir",
                   help="Directory containing TR-timing report files (.report, one per story)")
    p.add_argument("--model-name", default="meta-llama/Llama-2-7b-hf", dest="model_name",
                   help="HuggingFace model identifier for feature extraction")
    p.add_argument("--layer", type=int, default=16,
                   help="Transformer hidden layer index to extract features from (0-indexed)")
    p.add_argument("--lookback1", type=int, default=4, dest="lookback1",
                   help="Short context window size (words) for sliding-window accumulator")
    p.add_argument("--lookback2", type=int, default=20, dest="lookback2",
                   help="Long context window size (words); accumulator resets after this")
    p.add_argument("--audio-start-time", type=float, default=0.0, dest="audio_start_time",
                   help="Audio onset time relative to BOLD acquisition start (seconds)")
    p.add_argument("--space", default="T1w",
                   help="fMRIPrep output space label (T1w or MNI152NLin2009cAsym_res-2)")
    p.add_argument("--delays", nargs="+", type=int, default=[1, 2, 3, 4],
                   help="FIR delay embedding lags in TRs")
    p.add_argument("--n-boots", type=int, default=15, dest="n_boots",
                   help="Number of bootstrap samples for ridge cross-validation")
    p.add_argument("--chunk-len", type=int, default=10, dest="chunk_len",
                   help="Chunk length (TRs) for bootstrap held-out set construction")
    p.add_argument("--n-chunks", type=int, default=None, dest="n_chunks",
                   help="Number of chunks per bootstrap sample (default: ~20%% of training data)")
    p.add_argument("--gm-mask", default=None, dest="gm_mask",
                   help="Optional explicit brain/GM mask NIfTI to restrict voxels")
    p.add_argument("--cache-dir", default=None, dest="cache_dir",
                   help="HuggingFace model cache directory")
    p.add_argument("--output", default=None, help="Output root (default: derivatives/encoding/)")
    p.add_argument("--force", action="store_true", help="Overwrite existing outputs")
    return p


if __name__ == "__main__":
    run_encoding_model(_build_parser().parse_args())
