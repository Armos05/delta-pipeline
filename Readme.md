# DELTA Neuroimaging Pipeline

DICOM → BIDS → fMRIPrep → Physiological Denoising → First-Level GLM

---

## Pipeline Overview

```text
Raw DICOM
    │
    ▼  delta convert
BIDS directory  (sub-XX/ses-XX/{anat,func,fmap,dwi,behav,physio,nm}/)
    │
    ▼  delta preprocess
derivatives/fmriprep/  (sub-XX/ses-XX/func/  …_desc-preproc_bold.nii.gz etc.)
    │
    ▼  delta physio
derivatives/fmriprep/sub-XX/ses-XX/func/  …_desc-retroicor_regressors.tsv
    │
    ▼  [manual: place behav files — see §Required Manual Files]
BIDS/sub-XX/ses-XX/behav/
    │
    ├─► delta firstlevel-entropy
    │     derivatives/first_level/sub-XX/ses-XX/  (model-entropy_*)
    │
    └─► delta firstlevel-prior
          derivatives/first_level/sub-XX/ses-XX/  (model-prior_*)
```

---

## Commands

### 1. Convert DICOM → BIDS

```bash
delta convert \
  --raw   /data/raw \
  --bids  /data/bids \
  --config configs/dcm2bids_config.json \
  [--subjects sub-01 sub-02] \
  [--sessions ses-01] \
  [--force] [--dry-run]
```

Creates `sub-XX/ses-XX/{anat,func,fmap,dwi,behav}/` under `--bids`.
The `behav/` folder is created empty.

### 2. Preprocess with fMRIPrep

```bash
delta preprocess \
  --bids    /data/bids \
  --output  /data/derivatives \
  --output-spaces "MNI152NLin2009cAsym_res-2:res-2 T1w" \
  [--subjects sub-01] [--force] [--dry-run]
```

Outputs land in `derivatives/fmriprep/sub-XX/ses-XX/func/`.

### 3. Generate RETROICOR Regressors

```bash
delta physio \
  --bids /data/bids \
  --subjects sub-01 \
  --sessions ses-01 \
  --tasks sentencecompletion
```

Reads `.puls` / `.resp` logs from `bids/sub-XX/ses-XX/physio/` and writes
`…_desc-retroicor_regressors.tsv` into `derivatives/fmriprep/sub-XX/ses-XX/func/`.

---

## Required Manual Files

Before running either first-level model, place the following files into
`bids/sub-XX/ses-XX/behav/` manually:

| File | Description | Required for entropy | Required for prior |
|------|-------------|----------------------|--------------------|
| `main_task_data_sub-XX_ses-XX_*.csv` | PsychoPy task log with trial-level timings | Yes | Yes |
| `All_sentences_translated.xlsx` | Sentence lookup table with entropy column | Yes | Yes |
| `trial_by_trial_prior_posterior.csv` | HGF model output with `trial_num`, `lognu_mean` | Yes | Yes |
| `all_trials_tr_binned_entropy.csv` | TR-binned cumulative entropy per sentence | Yes | No |

These files are not produced by the DELTA pipeline. They come from the task software,
linguistic corpus processing, and HGF model fitting.

---

## First-Level Models

Both models use the same infrastructure:
- preprocessed BOLD from `derivatives/fmriprep/sub-XX/ses-XX/func/`
- fMRIPrep confounds (`…_desc-confounds_timeseries.tsv`)
- optional RETROICOR regressors from `delta physio`
- Nilearn `FirstLevelModel` with SPM HRF, cosine drift, 128 s high-pass, AR(1)

### Entropy model

Focus: linguistic predictability / entropy accumulation.

#### Main regressors and contrasts
- `sentence_base`
- `delay`
- `word_prior_int_correct`
- `word_prior_int_incorrect`
- `spoken_response`
- `catch_prediction_error`
- `pmod_entropy_sum`

Contrasts:
- `sentence`
- `delay`
- `word_prior_integration`
- `spoken_response`
- `word_correct_vs_incorrect`
- `catch_prediction_error`
- `effect_of_entropy_sum`

#### Run command

```bash
delta firstlevel-entropy \
  --bids /data/bids \
  --derivatives /data/derivatives \
  --subject sub-01 \
  --session ses-01 \
  --events-csv /data/bids/sub-01/ses-01/behav/main_task_data_sub-01_ses-01.csv \
  --sentences-xlsx /data/bids/sub-01/ses-01/behav/All_sentences_translated.xlsx \
  --binned-entropy-csv /data/bids/sub-01/ses-01/behav/all_trials_tr_binned_entropy.csv \
  --prior-csv /data/bids/sub-01/ses-01/behav/trial_by_trial_prior_posterior.csv
```

### Prior model

Focus: Bayesian prior activation / prior-match versus prior-mismatch.

#### Main regressors and contrasts
- `sentence_base`
- `delay`
- `word_prior_int_correct`
- `word_prior_int_incorrect`
- `spoken_response`
- `catch_prediction_error`
- `pmod_bayesian_prior`
- `pmod_el_vs_elmm`

Contrasts:
- `sentence`
- `delay`
- `word_prior_integration`
- `spoken_response`
- `word_correct_vs_incorrect`
- `catch_prediction_error`
- `lognu_mean_main`
- `el_vs_elmm`
- `elmm_vs_el`

#### Run command

```bash
delta firstlevel-prior \
  --bids /data/bids \
  --derivatives /data/derivatives \
  --subject sub-01 \
  --session ses-01 \
  --events-csv /data/bids/sub-01/ses-01/behav/main_task_data_sub-01_ses-01.csv \
  --sentences-xlsx /data/bids/sub-01/ses-01/behav/All_sentences_translated.xlsx \
  --prior-csv /data/bids/sub-01/ses-01/behav/trial_by_trial_prior_posterior.csv
```

---

## Output structure

Both first-level models write to:

```text
derivatives/first_level/
  sub-XX/
    ses-XX/
      sub-XX_ses-XX_task-sentencecompletion_model-entropy_design_matrix.csv
      sub-XX_ses-XX_task-sentencecompletion_model-entropy_design_matrix.png
      sub-XX_ses-XX_task-sentencecompletion_model-prior_design_matrix.csv
      sub-XX_ses-XX_task-sentencecompletion_model-prior_design_matrix.png
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_z_score.nii.gz
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_t_stat.nii.gz
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_effect_size.nii.gz
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_effect_variance.nii.gz
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_z_score_FDR.nii.gz
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_glass_brain_FDR.png
      sub-XX_ses-XX_task-sentencecompletion_model-<model>_contrast-<name>_glass_brain_uncorr.png
```

`<model>` is either `entropy` or `prior`.

---

## Source Files

| File | Purpose |
|------|---------|
| `src/cli.py` | Argument parsing and command dispatch |
| `src/convert.py` | DICOM → BIDS via dcm2bids |
| `src/preprocess.py` | fMRIPrep via Apptainer |
| `src/physio.py` | RETROICOR regressors from Siemens `.puls`/`.resp` |
| `src/first_level_base.py` | Shared first-level infrastructure |
| `src/first_level_entropy.py` | Entropy first-level model |
| `src/first_level_prior.py` | Prior first-level model |
| `src/analyze.py` | Analysis dispatch |
| `src/state.py` | JSON state tracking |
| `src/validate.py` | BIDS validator wrapper |
| `src/helper.py` | dcm2bids_helper wrapper |
