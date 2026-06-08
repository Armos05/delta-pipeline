#!/usr/bin/env python3
"""cli.py – Main entry point for the DELTA pipeline.
Parses user commands and dispatches to the correct operation.
"""

import argparse
import sys
from pathlib import Path

from state import load_state, print_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delta",
        description="DELTA neuroimaging pipeline: DICOM → BIDS → fMRIPrep → Physio → First-Level GLM",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    conv = subparsers.add_parser("convert", help="Convert DICOM → BIDS (incremental)")
    conv.add_argument("--dicom-dir", required=True, dest="dicom_dir", help="Raw DICOM root directory")
    conv.add_argument("--bids-dir", required=True, dest="bids_dir", help="BIDS output directory")
    conv.add_argument("--config", required=True, help="dcm2bids config JSON file")
    conv.add_argument("--subject", nargs="*", default=None, dest="subject", help="Subject IDs to convert (default: all new)")
    conv.add_argument("--session", nargs="*", default=None, dest="session", help="Session IDs (default: all)")
    conv.add_argument("--state", required=True, help="State file path")
    conv.add_argument("--force", action="store_true", help="Re-convert even if already done")
    conv.add_argument("--dry-run", action="store_true", help="Show what would run, no changes")

    prep = subparsers.add_parser("preprocess", help="Run fMRIPrep on converted subjects")
    prep.add_argument("--bids", required=True, help="BIDS directory")
    prep.add_argument("--output", required=True, help="Derivatives output directory")
    prep.add_argument("--fmriprep-sif", default="/mnt/nfs/delta_data/Data/containers/fmriprep_25.2.5.sif", dest="fmriprep_sif", help="Path to fmriprep .sif")
    prep.add_argument("--fs-license", default="/mnt/data/freesurfer/freesurferlicense.txt", help="FreeSurfer license file")
    prep.add_argument("--work-dir", default="tmp/fmriprep_work", help="fMRIPrep working directory")
    prep.add_argument("--output-spaces", default="MNI152NLin2009cAsym_res-2:res-2 T1w", help="fMRIPrep --output-spaces")
    prep.add_argument("--subjects", nargs="*", default=None, help="Subject IDs (default: all converted)")
    prep.add_argument("--session", nargs="*", default=None, dest="sessions", help="Session IDs to process (default: all)")
    prep.add_argument("--state", default="deltastate.json", help="State file path")
    prep.add_argument("--force", action="store_true", help="Re-preprocess even if already done")
    prep.add_argument("--dry-run", action="store_true", help="Show what would run, no changes")
    prep.add_argument("--fs-reconall", action="store_true", help="Enable FreeSurfer recon-all")
    prep.add_argument("fmriprep_args", nargs=argparse.REMAINDER, help="Extra flags passed directly to fMRIPrep (after --)")

    phys = subparsers.add_parser("physio", help="Generate RETROICOR physiological regressors")
    phys.add_argument("--bids", required=True, help="BIDS root directory")
    phys.add_argument("--subjects", nargs="*", default=None, help="Subject IDs")
    phys.add_argument("--sessions", nargs="*", default=None, help="Session IDs")
    phys.add_argument("--tasks", nargs="*", default=["sentencecompletion"], help="Task labels")
    phys.add_argument("--tr", type=float, default=None, help="TR in seconds")
    phys.add_argument("--n-vols", type=int, default=None, dest="n_vols", help="Number of volumes")
    phys.add_argument("--state", default="deltastate.json", help="State file path")
    phys.add_argument("--force", action="store_true", help="Re-generate even if TSV already exists")
    phys.add_argument("--dry-run", action="store_true", help="Show what would run, no changes")

    tr = subparsers.add_parser(
    "transcribe",
    help="Manual audio transcription — adds word_response and response_eval to task CSV",
    )
    tr.add_argument("--csv",       required=True,
                    help="Raw task CSV (main_task_data_sub-<id>_...csv)")
    tr.add_argument("--bank",      required=True,
                    help="Sentence bank CSV (All_sentences_translated_...csv)")
    tr.add_argument("--audio-dir", default=None, dest="audio_dir",
                    help="Directory containing audio response recordings")
    tr.add_argument("--audio-col", default=None, dest="audio_col",
                    help="Column in CSV holding the audio filename per trial (optional)")
    tr.add_argument("--output",    default=None,
                    help="Output CSV path (default: overwrite input CSV)")
    tr.add_argument("--redo",      action="store_true",
                    help="Re-transcribe rows that already have word_response")
    tr.set_defaults(func=lambda a: __import__("transcribe_responses").run_transcription(a))

    en = subparsers.add_parser(
    "enrich",
    help="Compute cloze metrics and correctness; requires word_response from 'delta transcribe'",
    )
    en.add_argument("--csv",       required=True, dest="subj_response_csv",
                    help="Transcribed subject CSV (output of 'delta transcribe')")
    en.add_argument("--bank",      required=True, dest="sentence_bank_csv",
                    help="Sentence bank CSV")
    en.add_argument("--output",    required=True, dest="output_csv",
                    help="Enriched output CSV path")
    en.add_argument("--model",     default="gpt2", dest="model_name",
                    help="HuggingFace causal LM (default: gpt2)")
    en.add_argument("--cache-dir", default=None,  dest="cache_dir",
                    help="HuggingFace model cache directory")
    en.add_argument("--no-gpu",    action="store_true", dest="no_gpu",
                    help="Force CPU even if GPU is available")
    en.set_defaults(func=lambda a: __import__("enrich_subject_data").main(
        a.subj_response_csv, a.sentence_bank_csv, a.output_csv,
        a.model_name, a.cache_dir, a.no_gpu,
    ))

    fle = subparsers.add_parser("firstlevel-entropy", help="Run first-level GLM (entropy model)")
    fle.add_argument("--bids", required=True, help="BIDS source directory")
    fle.add_argument("--derivatives", required=True, help="Derivatives root")
    fle.add_argument("--subject", required=True, help="Subject ID")
    fle.add_argument("--session", default="ses-01", help="Session ID")
    fle.add_argument("--events-csv", required=True, dest="events_csv", help="Task log CSV")
    fle.add_argument("--sentences-xlsx", required=True, dest="sentences_xlsx", help="Sentence entropy lookup XLSX")
    fle.add_argument("--binned-entropy-csv", required=True, dest="binned_entropy_csv", help="TR-binned entropy CSV")
    fle.add_argument("--prior-csv", required=True, dest="prior_csv", help="HGF posterior CSV")
    fle.add_argument("--space", default="MNI152NLin2009cAsym_res-2", help="fMRIPrep output space label")
    fle.add_argument("--gm-mask", default=None, dest="gm_mask", help="Optional brain/GM mask override")
    fle.add_argument("--output", default=None, help="Output root")
    fle.add_argument("--force", action="store_true", help="Overwrite existing outputs")

    flp = subparsers.add_parser("firstlevel-prior", help="Run first-level GLM (prior model)")
    flp.add_argument("--bids", required=True, help="BIDS source directory")
    flp.add_argument("--derivatives", required=True, help="Derivatives root")
    flp.add_argument("--subject", required=True, help="Subject ID")
    flp.add_argument("--session", default="ses-01", help="Session ID")
    flp.add_argument("--events-csv", required=True, dest="events_csv", help="Task log CSV")
    flp.add_argument("--sentences-xlsx", required=True, dest="sentences_xlsx", help="Sentence entropy lookup XLSX")
    flp.add_argument("--prior-csv", required=True, dest="prior_csv", help="HGF posterior CSV")
    flp.add_argument("--space", default="MNI152NLin2009cAsym_res-2", help="fMRIPrep output space label")
    flp.add_argument("--gm-mask", default=None, dest="gm_mask", help="Optional brain/GM mask override")
    flp.add_argument("--output", default=None, help="Output root")
    flp.add_argument("--force", action="store_true", help="Overwrite existing outputs")

    enc = subparsers.add_parser("encoding", help="Voxelwise encoding model (listening task)")
    enc.add_argument("--bids",        required=True)
    enc.add_argument("--derivatives", required=True)
    enc.add_argument("--subject",     required=True)
    enc.add_argument("--session",     default="ses-01")
    enc.add_argument("--stories",     nargs="+", required=True,
                    help="All story names, in BOLD-run order")
    enc.add_argument("--test-stories", nargs="+", required=True, dest="test_stories",
                    help="Story names reserved for test (remainder = training)")
    enc.add_argument("--grid-dir",     required=True, dest="grid_dir",
                    help="Dir containing Praat TextGrid files (.TextGrid)")
    enc.add_argument("--tr-report-dir", required=True, dest="tr_report_dir",
                    help="Dir containing TR-timing report files (.report)")
    enc.add_argument("--model-name",  default="meta-llama/Llama-2-7b-hf", dest="model_name")
    enc.add_argument("--layer",       type=int, default=16)
    enc.add_argument("--lookback1",   type=int, default=4,  dest="lookback1")
    enc.add_argument("--lookback2",   type=int, default=20, dest="lookback2")
    enc.add_argument("--audio-start-time", type=float, default=0.0, dest="audio_start_time")
    enc.add_argument("--space",       default="T1w")
    enc.add_argument("--delays",      nargs="+", type=int, default=[1, 2, 3, 4])
    enc.add_argument("--n-boots",     type=int, default=15, dest="n_boots")
    enc.add_argument("--chunk-len",   type=int, default=10, dest="chunk_len")
    enc.add_argument("--n-chunks",    type=int, default=None, dest="n_chunks")
    enc.add_argument("--gm-mask",     default=None, dest="gm_mask")
    enc.add_argument("--cache-dir",   default=None, dest="cache_dir")
    enc.add_argument("--output",      default=None)
    enc.add_argument("--force",       action="store_true")
    enc.set_defaults(func=lambda a: __import__("encoding_model").run_encoding_model(a))

    anal = subparsers.add_parser("analyze", help="Run an arbitrary Python or R analysis script")
    anal.add_argument("--script", required=True, help="Path to .py or .R script")
    anal.add_argument("--bids", default=None, help="BIDS directory (passed as $BIDS_DIR)")
    anal.add_argument("--derivatives", default=None, help="Derivatives dir (passed as $DERIVATIVES_DIR)")
    anal.add_argument("--args", nargs=argparse.REMAINDER, default=[], help="Extra args passed to script")

    stat = subparsers.add_parser("status", help="Show conversion/preprocessing status")
    stat.add_argument("--state", default="deltastate.json", help="State file path")

    helper = subparsers.add_parser("helper", help="Explore DICOM headers to build dcm2bids config")
    helper.add_argument("--dicom", required=True, help="DICOM directory for one subject/session")
    helper.add_argument("--output", default=".helper_output", help="Where to write helper output")

    valid = subparsers.add_parser("validate", help="Validate a BIDS directory")
    valid.add_argument("--bids", required=True, dest="bids_dir", help="BIDS directory to validate")
    valid.add_argument("--ignoreWarnings", action="store_true", dest="ignore_warnings", help="Suppress bids-validator warnings")
    valid.add_argument("validator_args", nargs=argparse.REMAINDER, help="Extra flags passed to bids-validator")
    return parser


def cmd_status(args):
    state = load_state(Path(args.state))
    print_status(state)


def cmd_convert(args):
    from convert import run_convert
    run_convert(args)


def cmd_preprocess(args):
    from preprocess import run_preprocess
    run_preprocess(args)


def cmd_physio(args):
    from physio import run_physio
    run_physio(args)


def cmd_first_level_entropy(args):
    from analyze import run_first_level_entropy
    run_first_level_entropy(args)


def cmd_first_level_prior(args):
    from analyze import run_first_level_prior
    run_first_level_prior(args)


def cmd_analyze(args):
    from analyze import run_analyze
    run_analyze(args)


def cmd_helper(args):
    from helper import run_helper
    run_helper(args)


def cmd_validate(args):
    from validate import run_validate
    run_validate(args)


COMMANDS = {
    "status": cmd_status,
    "convert": cmd_convert,
    "preprocess": cmd_preprocess,
    "physio": cmd_physio,
    "firstlevel-entropy": cmd_first_level_entropy,
    "firstlevel-prior": cmd_first_level_prior,
    "analyze": cmd_analyze,
    "helper": cmd_helper,
    "validate": cmd_validate,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    handler(args)


if __name__ == "__main__":
    main()
