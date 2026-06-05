#!/usr/bin/env python3
"""
enrich_subject_data.py

Enriches a raw (or manually-transcribed) subject response CSV with:

  1. cloze_py          — GPT-2 probability of word_response given sentence prefix
  2. cloze_py_target   — GPT-2 probability of the target word (same prefix)
  3. cloze_distance    — |log(cloze_py) - log(cloze_py_target)|  semantic distance
                         between participant's response and the target in cloze-
                         probability space (NEW)
  4. channels          — channel number from noise_condition (or "CATCH")
  5. target_word       — from sentence bank
  6. cloze_pu          — normed cloze probability from sentence bank
  7. entropy           — sentence entropy from sentence bank
  8. correct           — 1 if word_response == target_word (case-insensitive)
                         falls back to response_eval if already present (NEW)

Prerequisite
────────────
Run ``delta transcribe`` (transcribe_responses.py) first to populate the
``word_response`` and ``response_eval`` columns before running this script.

Usage
─────
  python3 src/enrich_subject_data.py \\
      <subj_response_csv> \\
      <sentence_bank_csv> \\
      <output_csv> \\
      [--model gpt2|gpt2-medium|gpt2-large] \\
      [--cache-dir /path/to/hf_cache] \\
      [--no-gpu]
"""

from __future__ import annotations

import argparse
import math
import re
import sys

import pandas as pd
import torch
import torch.nn.functional as F
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_MODEL   = "gpt2"
MODEL_CACHE_DIR = "/mnt/nfs/stricon_data/DELTA_files/models"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_sentence_number(sentence_file: str) -> int | None:
    m = re.search(r"Sentence_(\d+)\.wav", str(sentence_file))
    return int(m.group(1)) if m else None


def extract_channel_number(noise_condition: str) -> str | int:
    s = str(noise_condition).strip()
    if s.upper() == "CATCH":
        return "CATCH"
    m = re.search(r"word_n_ch(\d+)", s)
    return int(m.group(1)) if m else s


def get_cloze_prob(
    model,
    tokenizer,
    sentence_prefix: str,
    target_word: str,
    device,
) -> float:
    """P(target_word | sentence_prefix) via a causal LM."""
    sentence_prefix  = sentence_prefix.strip()
    target_with_space = " " + target_word.strip()

    prefix_ids = tokenizer.encode(sentence_prefix, add_special_tokens=False)
    target_ids = tokenizer.encode(target_with_space, add_special_tokens=False)

    if len(target_ids) == 0:
        return 0.0

    full_ids     = prefix_ids + target_ids
    input_tensor = torch.tensor([full_ids], dtype=torch.long).to(device)

    with torch.no_grad():
        logits = model(input_tensor).logits  # (1, seq_len, vocab_size)

    log_prob = 0.0
    for k, tid in enumerate(target_ids):
        pos = len(prefix_ids) + k - 1
        log_prob += F.log_softmax(logits[0, pos, :], dim=-1)[tid].item()

    return math.exp(log_prob)


def cloze_distance(p_response: float, p_target: float) -> float:
    """Absolute log-probability distance between response and target cloze.

    cloze_distance = |log(p_response) - log(p_target)|

    Both inputs are floored at 1e-12 to avoid log(0).
    A distance of 0 means the response is exactly as predictable as the target.
    Higher values mean the response word is further from the target in
    language-model probability space.
    """
    floor = 1e-12
    p_resp   = max(p_response, floor)
    p_tgt    = max(p_target,   floor)
    return abs(math.log(p_resp) - math.log(p_tgt))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main(
    subj_path:  str,
    bank_path:  str,
    output_path: str,
    model_name: str,
    cache_dir:  str | None,
    no_gpu:     bool,
) -> None:

    subj = pd.read_csv(subj_path)
    bank = pd.read_csv(bank_path)

    # ── Column validation ─────────────────────────────────────────────────────
    required_subj = ["sentence_file", "word_response", "noise_condition"]
    required_bank = ["index", "en_sentence", "en_target_word", "cloze_pu", "entropy"]
    for col in required_subj:
        if col not in subj.columns:
            sys.exit(f"ERROR: subject CSV missing required column '{col}'. "
                     "Run 'delta transcribe' first to add word_response.")
    for col in required_bank:
        if col not in bank.columns:
            sys.exit(f"ERROR: sentence bank missing required column '{col}'")

    # ── Sentence-bank lookups ─────────────────────────────────────────────────
    bank_idx         = bank.set_index("index")
    lookup_sentence  = bank_idx["en_sentence"].to_dict()
    lookup_target    = bank_idx["en_target_word"].to_dict()
    lookup_cloze_pu  = bank_idx["cloze_pu"].to_dict()
    lookup_entropy   = bank_idx["entropy"].to_dict()

    subj["_sent_num"] = subj["sentence_file"].apply(extract_sentence_number)

    # ── Metadata columns ──────────────────────────────────────────────────────
    subj["channels"]    = subj["noise_condition"].apply(extract_channel_number)
    subj["target_word"] = subj["_sent_num"].map(lookup_target)
    subj["cloze_pu"]    = subj["_sent_num"].map(lookup_cloze_pu)
    subj["entropy"]     = subj["_sent_num"].map(lookup_entropy)

    # ── Load LM ───────────────────────────────────────────────────────────────
    print(f"Loading model: {model_name}")
    device = torch.device("cpu" if no_gpu or not torch.cuda.is_available() else "cuda")
    tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
    model = AutoModelForCausalLM.from_pretrained(
        model_name, cache_dir=cache_dir
    ).to(device)
    model.eval()
    print(f"Model loaded on {device}.\n")

    # ── Per-row cloze computation ─────────────────────────────────────────────
    cloze_py_vals        = []
    cloze_py_target_vals = []
    cloze_dist_vals      = []

    for _, row in tqdm(subj.iterrows(), total=len(subj), desc="Computing cloze"):
        sent_num  = row["_sent_num"]
        sentence  = lookup_sentence.get(sent_num)
        word_resp = str(row["word_response"]).strip()
        tgt_word  = str(row.get("target_word", "")).strip()

        if sentence is None or not word_resp or word_resp in ("nan", ""):
            cloze_py_vals.append(float("nan"))
            cloze_py_target_vals.append(float("nan"))
            cloze_dist_vals.append(float("nan"))
            continue

        p_resp = get_cloze_prob(model, tokenizer, sentence, word_resp, device)
        p_tgt  = (
            get_cloze_prob(model, tokenizer, sentence, tgt_word, device)
            if tgt_word and tgt_word != "nan"
            else float("nan")
        )
        dist = cloze_distance(p_resp, p_tgt) if not math.isnan(p_tgt) else float("nan")

        cloze_py_vals.append(p_resp)
        cloze_py_target_vals.append(p_tgt)
        cloze_dist_vals.append(dist)

    subj["cloze_py"]        = cloze_py_vals
    subj["cloze_py_target"] = cloze_py_target_vals
    subj["cloze_distance"]  = cloze_dist_vals

    # ── Correctness ───────────────────────────────────────────────────────────
    # Prefer response_eval if already set by transcribe_responses.py; otherwise
    # compute from string match.
    if "response_eval" in subj.columns and subj["response_eval"].notna().any():
        subj["correct"] = subj["response_eval"].fillna(
            (subj["word_response"].str.strip().str.lower()
             == subj["target_word"].str.strip().str.lower()).astype(int)
        ).astype(int)
    else:
        subj["correct"] = (
            subj["word_response"].str.strip().str.lower()
            == subj["target_word"].str.strip().str.lower()
        ).astype(int)

    # ── Clean up and save ─────────────────────────────────────────────────────
    subj = subj.drop(columns=["_sent_num"])
    subj.to_csv(output_path, index=False)

    print(f"\nSaved to: {output_path}")
    print(f"Rows:            {len(subj)}")
    print(f"cloze_py zeros:  {(subj['cloze_py'] == 0).sum()}")
    print(f"cloze_py NaNs:   {subj['cloze_py'].isna().sum()}")
    print(f"correct=1:       {subj['correct'].sum()} / {len(subj)}")
    print(f"\nMean cloze_distance: {subj['cloze_distance'].mean():.4f}")
    print("\nSample output:")
    print(
        subj[[
            "sentence_file", "word_response", "target_word",
            "cloze_py", "cloze_py_target", "cloze_distance",
            "cloze_pu", "entropy", "channels", "correct",
        ]].head(8).to_string()
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich subject response CSV with cloze metrics.")
    parser.add_argument("subj_response_csv",  help="Raw (transcribed) subject response CSV")
    parser.add_argument("sentence_bank_csv",  help="Sentence bank CSV")
    parser.add_argument("output_csv",         help="Path for enriched output CSV")
    parser.add_argument("--model",            default=DEFAULT_MODEL,
                        help=f"HuggingFace causal LM (default: {DEFAULT_MODEL})")
    parser.add_argument("--cache-dir",        default=MODEL_CACHE_DIR, dest="cache_dir",
                        help="HuggingFace model cache directory")
    parser.add_argument("--no-gpu",           action="store_true", dest="no_gpu",
                        help="Force CPU even if a GPU is available")
    args = parser.parse_args()

    main(
        subj_path   = args.subj_response_csv,
        bank_path   = args.sentence_bank_csv,
        output_path = args.output_csv,
        model_name  = args.model,
        cache_dir   = args.cache_dir,
        no_gpu      = args.no_gpu,
    )
