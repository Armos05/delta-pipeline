#!/usr/bin/env python3
"""first_level_prior.py — DELTA first-level GLM (prior model)."""

from __future__ import annotations

import argparse
import numpy as np
import pandas as pd

from first_level_base import DEFAULT_SPACE, run_model


def build_events(df: pd.DataFrame):
    rows = []
    mean_prior = float(df["lognu_mean"].mean()) if "lognu_mean" in df.columns else 0.0

    pe_vals = []
    for _, r in df.iterrows():
        prior_prec = float(np.exp(-r["entropy"])) if not np.isnan(r["entropy"]) else 0.0
        is_catch = bool(r.get("is_catch_trial", 0) == 1)
        if is_catch:
            outcome_surprise = 1.0
        elif "response_eval" in df.columns and pd.notna(r["response_eval"]):
            outcome_surprise = 1.0 - float(r["response_eval"])
        else:
            outcome_surprise = np.nan
        pe_vals.append(prior_prec * outcome_surprise if not np.isnan(outcome_surprise) else np.nan)

    pe_series = pd.Series(pe_vals, index=df.index)

    for _, r in df.iterrows():
        rows.append(dict(onset=r["fixation_onset"], duration=r["fixation_duration"], trial_type="fixation", modulation=1.0))
        rows.append(dict(onset=r["sentence_onset"], duration=r["sentence_duration"], trial_type="sentence_base", modulation=1.0))
        rows.append(dict(onset=r["delay_onset"], duration=r["delay_duration"], trial_type="delay", modulation=1.0))

        if "is_catch_trial" in df.columns and r["is_catch_trial"] == 1:
            rows.append(dict(onset=r["delay_offset"], duration=0.0, trial_type="catch_prediction_error", modulation=1.0))
            continue

        merged_onset = float(r["word_onset"])
        merged_duration = float(r["rating_delay_offset"] - r["word_onset"])
        is_correct = (r["response_eval"] == 1) if "response_eval" in df.columns and pd.notna(r["response_eval"]) else True
        word_type = "word_prior_int_correct" if is_correct else "word_prior_int_incorrect"
        rows.append(dict(onset=merged_onset, duration=merged_duration, trial_type=word_type, modulation=1.0))

        col_name = "entropy_condition"
        if col_name in df.columns and pd.notna(r.get(col_name)):
            econd = str(r[col_name]).strip().lower()
            if econd == "el":
                rows.append(dict(onset=merged_onset, duration=merged_duration, trial_type="pmod_el_vs_elmm", modulation=1.0))
            elif econd == "elmm":
                rows.append(dict(onset=merged_onset, duration=merged_duration, trial_type="pmod_el_vs_elmm", modulation=-1.0))

        if "lognu_mean" in r and pd.notna(r["lognu_mean"]):
            rows.append(dict(onset=merged_onset, duration=merged_duration, trial_type="pmod_bayesian_prior", modulation=float(r["lognu_mean"] - mean_prior)))

        rows.append(dict(onset=r["rating_onset"], duration=r["rating_duration"], trial_type="rating", modulation=1.0))
        rows.append(dict(onset=r["response_onset"], duration=r["response_duration"], trial_type="spoken_response", modulation=1.0))

    events = pd.DataFrame(rows).sort_values("onset").reset_index(drop=True)
    return events, mean_prior


CONTRASTS = {
    "sentence": "sentence_base",
    "delay": "delay",
    "word_prior_integration": "0.5*word_prior_int_correct + 0.5*word_prior_int_incorrect",
    "spoken_response": "spoken_response",
    "word_correct_vs_incorrect": "word_prior_int_correct - word_prior_int_incorrect",
    "catch_prediction_error": "catch_prediction_error",
    "lognu_mean_main": "pmod_bayesian_prior",
    "el_vs_elmm": "pmod_el_vs_elmm",
    "elmm_vs_el": "-pmod_el_vs_elmm",
}


def run_first_level_prior(args) -> None:
    run_model(args, model_name="prior", build_events_func=build_events, contrasts=CONTRASTS, needs_binned_entropy=False)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DELTA first-level GLM — prior model")
    p.add_argument("--bids", required=True)
    p.add_argument("--derivatives", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--session", default="ses-01")
    p.add_argument("--events-csv", required=True, dest="events_csv")
    p.add_argument("--sentences-xlsx", required=True, dest="sentences_xlsx")
    p.add_argument("--prior-csv", required=True, dest="prior_csv")
    p.add_argument("--space", default=DEFAULT_SPACE)
    p.add_argument("--gm-mask", default=None, dest="gm_mask")
    p.add_argument("--output", default=None)
    p.add_argument("--force", action="store_true")
    return p


if __name__ == "__main__":
    run_first_level_prior(_build_parser().parse_args())
