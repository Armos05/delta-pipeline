# DELTA — the Delta of Language or Data Extraction, Labelling and Transfer Automation 

End-to-end pipeline for MRI/physio conversion, preprocessing, behavioural data processing, first-level GLM analysis, and voxelwise encoding-model analysis. All steps are invoked via the unified `delta` CLI.

---

## Repository layout

```
delta/
├── src/
│   ├── cli.py                      # Unified CLI entry-point
│   ├── convert.py                  # DICOM → BIDS conversion (dcm2bids)
│   ├── preprocess.py               # fMRIPrep wrapper
│   ├── physio.py                   # Physiological noise regressors (RETROICOR)
│   ├── transcribe_responses.py     # Manual audio transcription helper
│   ├── enrich_subject_data.py      # Automated cloze enrichment
│   ├── first_level_entropy.py      # First-level GLM (sentence-completion task)
│   ├── encoding_model.py           # Voxelwise encoding model (listening task)
│   ├── analyze.py                  # Generic script dispatcher + sub-analysis shims
│   ├── validate.py                 # BIDS validator wrapper
│   ├── state.py                    # Run-state tracking
│   └── helper.py                   # Shared path helpers
├── stan/
│   └── final_stan_model.stan       # Hierarchical Bayesian Stan model
├── scripts/
│   └── 6_final_task_modelling_script.R
├── notebooks/
│   ├── brain_vs_shift_beta.ipynb
│   └── simes_test.ipynb
├── delta.def                       # Singularity/Apptainer container definition
└── README.md
```

---

## Full pipeline overview

```
DICOM data
    │
    ▼
delta convert              DICOM → BIDS; creates anat/ func/ fmap/ dwi/ behav/
    │
    ▼
delta validate             BIDS validator
    │
    ▼
delta preprocess           fMRIPrep (T1w + functional preprocessing)
    │
    ├──────────────────────────────────────────┬──────────────────────────────────
    │  SENTENCE-COMPLETION TASK                │  LISTENING TASK
    ▼                                          ▼
delta transcribe  ← manual             delta encoding
    │  (audio → word_response,                 │  voxelwise LLM ridge model
    │   response_eval)                         │
    ▼                                          ▼
delta enrich                           corrs.npy / weights.npy
    │  (cloze_py, cloze_distance …)            │
    ▼                                          │
delta physio                                   │
    │  (RETROICOR regressors)                  │
    ▼                                          │
delta firstlevel                               │
    │  (first-level GLM)                       │
    └──────────────────┬───────────────────────┘
                       ▼
             notebooks/brain_vs_shift_beta.ipynb
             notebooks/simes_test.ipynb
                       │
                       ▼
             delta bayesian  (R/Stan hierarchical model)
```

---

## Step-by-step usage

### 1 — `delta convert`

Converts DICOM data to BIDS and creates an empty `behav/` folder alongside `anat/`, `func/`, and `fmap/`.

```bash
delta convert \
  --dicom-dir  /data/dicoms/sub-01/ses-01 \
  --bids-dir   /data/bids \
  --subject    sub-01 \
  --session    ses-01
```

if you skip the subject and session tag it will run it for all the subjects in the folder and will skip those for which the bids directory already exists, although if you want to force the changes you can use the `--force` tag

**Output structure**

```
bids/
└── sub-01/
    └── ses-01/
        ├── anat/
        ├── func/
        ├── fmap/
        ├── dwi/
        └── behav/   ← created automatically; audio recordings placed here manually
```

---

### 2 — `delta validate`

Before you run validation copy the `bidsignore` file from the config folder to the bids_folder as `.bidsignore`.

```bash
delta validate --bids /data/bids
```
you can ignore the warning using the `--ignoreWarnings` tags to ignore Warning from the output.

---

### 3 — `delta preprocess`

Runs fMRIPrep for one subject/session.

```bash
delta preprocess \
  --bids         /data/bids \
  --derivatives  /data/derivatives \
  --subject      sub-01 \
  --session      ses-01
```

**fMRIPrep outputs** (`derivatives/fmriprep/sub-01/ses-01/`)

```
func/
  sub-01_ses-01_task-*_space-T1w_desc-preproc_bold.nii.gz
  sub-01_ses-01_task-*_space-T1w_desc-brain_mask.nii.gz
  sub-01_ses-01_task-*_desc-confounds_timeseries.tsv
anat/
  sub-01_ses-01_space-MNI152NLin2009cAsym_desc-preproc_T1w.nii.gz
```

---

### 4 — `delta physio`

Computes RETROICOR cardiac and respiratory noise regressors from physiological recordings.

```bash
delta physio \
  --bids       /data/bids \
  --subject    sub-01 \
  --session    ses-01 \
  --task       sentencecompletion
```

**Output** (`bids/sub-01/ses-01/behav/`)

```
sub-01_ses-01_task-sentencecompletion_desc-retroicor_regressors.tsv
```

---

## Behavioural data processing (sentence-completion task)

Before the first-level fMRI GLM can run, the raw task CSV must be enriched through two steps: manual audio transcription of spoken responses, then automated cloze computation.

### Required manual file placement

Before `delta transcribe` can be run, copy the following into the subject `behav/` folder:

| File | Notes |
|---|---|
| `main_task_data_sub-<id>_<name>_ses-<N>_<timestamp>.csv` | Raw experimental output placed automatically by task software |
| Audio response recordings (`.wav` / `.mp3`) | **Manually copied** from recording device; name as `<trial_num>.wav` or `response_<trial_num>.wav` |

The sentence bank CSV (`All_sentences_translated_with_en_cloze_prob_and_entropy.csv`) is a **study-level** file stored once in `bids/stimuli/` and shared across all subjects.

---

### 5 — `delta transcribe` (manual audio transcription)

A researcher listens to each audio response recording and types the word they heard. The script writes two new columns to the task CSV:

| Column | Type | Description |
|---|---|---|
| `word_response` | str | The word the researcher heard the participant say |
| `response_eval` | int | `1` = matched target word, `0` = did not match |

The script is **resumable**: rows with an existing `word_response` are skipped by default. Use `--redo` to overwrite all. Progress is **auto-saved every 10 rows**.

**Interactive commands**

| Key | Action |
|---|---|
| `<word> + Enter` | Accept the typed word as the response |
| `r + Enter` | Replay the audio |
| `s + Enter` | Skip this trial |
| `q + Enter` | Quit and save immediately |

**Usage**

```bash
delta transcribe \
    --csv       /data/bids/sub-01/ses-01/behav/main_task_data_sub-811422_PIER_SARTI_ses-001_2026-04-30_10h14.52.300.csv \
    --bank      /data/bids/stimuli/All_sentences_translated_with_en_cloze_prob_and_entropy.csv \
    --audio-dir /data/bids/sub-01/ses-01/behav/audio_responses \
    --output    /data/bids/sub-01/ses-01/behav/main_task_data_sub-811422_transcribed.csv
```

| Argument | Required | Description |
|---|---|---|
| `--csv` | ✓ | Raw task CSV from experimental software |
| `--bank` | ✓ | Sentence bank CSV |
| `--audio-dir` | — | Directory with audio response files |
| `--audio-col` | — | Column in CSV containing per-trial audio filename |
| `--output` | — | Output path (default: overwrite input) |
| `--redo` | — | Re-transcribe already-completed rows |

---

### 6 — `delta enrich` (automated cloze enrichment)

Computes GPT-2 cloze probabilities and merges sentence-level metadata. Requires `word_response` to already exist (from `delta transcribe`).

**Columns added**

| Column | Type | Description |
|---|---|---|
| `cloze_py` | float | GPT-2 P(word_response \| sentence prefix) |
| `cloze_py_target` | float | GPT-2 P(target_word \| sentence prefix) — model baseline |
| `cloze_distance` | float | \|log(cloze_py) − log(cloze_py_target)\| — deviation from target |
| `channels` | int / "CATCH" | Audio channel count from `noise_condition` |
| `target_word` | str | Correct completion word from sentence bank |
| `cloze_pu` | float | Normed human cloze probability from sentence bank |
| `entropy` | float | Sentence-level entropy from sentence bank |
| `correct` | int | `1` = word_response matches target (uses `response_eval` if present) |

`cloze_distance = |log(cloze_py) − log(cloze_py_target)|` is computed in log-probability space to handle the wide dynamic range of cloze values. A value of 0 means the spoken word was equally predictable as the target; larger values indicate greater deviation. This metric is usable as a continuous parametric regressor in the GLM.

**Usage**

```bash
delta enrich \
    --csv     /data/bids/sub-01/ses-01/behav/main_task_data_sub-811422_transcribed.csv \
    --bank    /data/bids/stimuli/All_sentences_translated_with_en_cloze_prob_and_entropy.csv \
    --output  /data/bids/sub-01/ses-01/behav/main_task_data_sub-811422_enriched.csv \
    [--model  gpt2-medium] \
    [--cache-dir /path/to/hf_cache] \
    [--no-gpu]
```

| Argument | Required | Description |
|---|---|---|
| `--csv` | ✓ | Transcribed subject CSV (output of `delta transcribe`) |
| `--bank` | ✓ | Sentence bank CSV |
| `--output` | ✓ | Enriched output CSV path |
| `--model` | — | HuggingFace causal LM (default: `gpt2`) |
| `--cache-dir` | — | HuggingFace model cache directory |
| `--no-gpu` | — | Force CPU even if GPU is available |

---

### 7 — `delta firstlevel` (first-level fMRI GLM, sentence-completion task)

Fits a first-level GLM to the preprocessed BOLD from the sentence-completion task with parametric modulators from information-theoretic entropy and prior probability.

#### Required manual files in `behav/`

| File | Description | Required |
|---|---|---|
| `{sub}_{ses}_task-sentencecompletion_events.tsv` | Trial onset/duration/condition table (BIDS events format) | ✓ |
| `{sub}_{ses}_task-sentencecompletion_sentences.xlsx` | Full sentence list with per-sentence entropy values | ✓ |
| `{sub}_{ses}_task-sentencecompletion_binned-entropy.csv` | Pre-binned entropy values aligned to trial order | ✓ |
| `{sub}_{ses}_task-sentencecompletion_prior.csv` | Prior probability estimates for parametric regressor | ✓ |
| `{sub}_{ses}_task-sentencecompletion_desc-retroicor_regressors.tsv` | RETROICOR regressors from `delta physio` | — optional |

**fMRIPrep files read**

```
derivatives/fmriprep/{sub}/{ses}/func/
  {sub}_{ses}_task-sentencecompletion_space-T1w_desc-preproc_bold.nii.gz
  {sub}_{ses}_task-sentencecompletion_space-T1w_desc-brain_mask.nii.gz
  {sub}_{ses}_task-sentencecompletion_desc-confounds_timeseries.tsv
```

**What it does**

Each trial is modelled with a boxcar regressor convolved with the canonical HRF. Parametric modulators from trial-wise entropy (`--binned-entropy-csv`) and prior probability (`--prior-csv`) are included as separate regressors. RETROICOR regressors are appended if present. Design matrix estimated with Nilearn's `FirstLevelModel`.

Contrasts estimated: `entropy_modulation`, `prior_modulation`, `task > rest`, and their sum/difference.

**Usage**

```bash
delta firstlevel \
  --bids                /data/bids \
  --derivatives         /data/derivatives \
  --subject             sub-01 \
  --session             ses-01 \
  --events-csv          /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_events.tsv \
  --sentences-xlsx      /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_sentences.xlsx \
  --binned-entropy-csv  /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_binned-entropy.csv \
  --prior-csv           /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_prior.csv \
  [--space MNI152NLin2009cAsym_res-2]
```

**Output** (`derivatives/first_level/sub-01/ses-01/`)

```
sub-01_ses-01_contrast-entropy_stat-t_statmap.nii.gz
sub-01_ses-01_contrast-prior_stat-t_statmap.nii.gz
sub-01_ses-01_contrast-task_stat-t_statmap.nii.gz
sub-01_ses-01_design_matrix.png
sub-01_ses-01_report.html
```

---

### 8 — `delta encoding` (voxelwise encoding model, listening task)

Fits a voxelwise ridge regression encoding model to preprocessed BOLD from the listening task using contextual LLM features extracted from the stimulus transcript.

#### Required manual files

The following must be prepared once (study-level) in `bids/stimuli/`:

| File type | Location | Description |
|---|---|---|
| Praat TextGrid (`.TextGrid`) | `bids/stimuli/grids/` | Forced-alignment word boundaries; one per story named `{storyname}.TextGrid` |
| TR-timing report (`.report`) | `bids/stimuli/tr_reports/` | Scanner trigger log; one per story named `{storyname}.report`; must contain `trigger` / `sound-start` / `sound-stop` lines |

**fMRIPrep files read**

```
derivatives/fmriprep/{sub}/{ses}/func/
  {sub}_{ses}_task-listening_space-{space}_desc-preproc_bold.nii.gz
  {sub}_{ses}_task-listening_space-{space}_desc-brain_mask.nii.gz
```

**What it does**

1. **BOLD loading** — extracts voxel time-series within brain mask; z-scores each voxel across time
2. **Stimulus feature extraction** — loads TextGrids and TR reports; builds word DataSequences; extracts contextual LLM hidden-state features via a sliding-window accumulator; downsamples to TR resolution (Lanczos, window = 3)
3. **FIR delay embedding** — replicates features at lags of 1–4 TRs for haemodynamic delay modelling
4. **Bootstrap ridge regression** — log-spaced α ∈ [10, 10⁵]; per-voxel α selected from held-out Pearson correlations across bootstrap samples
5. **Output saving** — `.npy` arrays, NIfTI volume, glass-brain PNG

**Usage**

```bash
delta encoding \
  --bids           /data/bids \
  --derivatives    /data/derivatives \
  --subject        sub-01 \
  --session        ses-01 \
  --stories        stimStory01 stimStory02 stimStory03 stimStory04 \
  --test-stories   stimStory04 \
  --grid-dir       /data/bids/stimuli/grids \
  --tr-report-dir  /data/bids/stimuli/tr_reports \
  --model-name     meta-llama/Llama-2-7b-hf \
  --layer          16 \
  --lookback1      4 \
  --lookback2      20 \
  --space          T1w \
  --delays         1 2 3 4 \
  --n-boots        15 \
  [--gm-mask       /path/to/gm_mask.nii.gz] \
  [--cache-dir     /path/to/hf_cache] \
  [--force]
```

| Argument | Required | Description |
|---|---|---|
| `--stories` | ✓ | All story names in BOLD-run order |
| `--test-stories` | ✓ | Stories held out for evaluation |
| `--grid-dir` | ✓ | Directory with `.TextGrid` files |
| `--tr-report-dir` | ✓ | Directory with `.report` files |
| `--model-name` | ✓ | HuggingFace model for LLM feature extraction |
| `--layer` | — | Transformer layer index (default: 16) |
| `--lookback1` | — | Short context window in words (default: 4) |
| `--lookback2` | — | Long context window in words (default: 20) |
| `--space` | — | fMRIPrep output space (default: `T1w`) |
| `--delays` | — | FIR lag values in TRs (default: 1 2 3 4) |
| `--n-boots` | — | Bootstrap samples (default: 15) |
| `--gm-mask` | — | Explicit GM mask NIfTI (defaults to brain mask) |
| `--cache-dir` | — | HuggingFace model cache directory |
| `--force` | — | Overwrite existing outputs |

**Output** (`derivatives/encoding/sub-01/ses-01/`)

| File | Description |
|---|---|
| `corrs.npy` | `(n_voxels,)` Pearson r on held-out test story |
| `weights.npy` | `(n_features × n_delays, n_voxels)` regression weights |
| `best_alphas.npy` | `(n_voxels,)` selected regularisation parameter |
| `bootstrap_corrs.npy` | `(n_alphas, n_voxels, n_boots)` full CV correlation array |
| `voxel_mask.npy` | `(n_voxels, 3)` integer voxel coordinates |
| `sub-01_ses-01_task-listening_encoding_corrs.nii.gz` | NIfTI volume of prediction correlations |
| `corr_glass_brain.png` | Glass-brain summary plot |

---

### 9 — Statistical analysis notebooks

#### `brain_vs_shift_beta.ipynb`

Voxelwise prediction correlation maps, encoding performance comparison across temporal shift / lookback variants, and ridge regression beta weight profiles.

**Inputs:** `derivatives/encoding/sub-*/ses-*/corrs.npy`, `weights.npy`, `voxel_mask.npy`

#### `simes_test.ipynb`

Whole-brain significance testing across subjects using Simes' combined-p method. Produces a thresholded FWE-corrected NIfTI significance map.

**Inputs:** `derivatives/encoding/sub-*/ses-*/corrs.npy`, `bootstrap_corrs.npy`

---

### 10 — `delta bayesian` (hierarchical Bayesian modelling)

Fits a hierarchical Bayesian belief-updating model (Stan) to the enriched subject data following the Knolle/Mathys preprocessing pipeline. Operates on the CSV produced by `delta enrich`.

**Requirements:** R packages `dplyr`, `ggplot2`, `tidyr`, `rstan`

**Required columns in enriched CSV:** `trial_num`, `participant_private_id`, `channels`, `entropy`, `cloze_pu`, `clarity`, `cloze_py`, `correct`, `spq`, `noise_threshold`

**Usage**

```bash
Rscript src/6_final_task_modelling_script.R \
    /path/to/main_task_data_sub-811422_enriched.csv \
    /path/to/output_dir
```

**What it does**

1. Loads and validates the enriched CSV
2. Removes CATCH trials automatically
3. Preprocesses predictors (Knolle et al.): log + z-score for `channels` and `entropy`; floor at 0.01 for `cloze_pu` and `cloze_py`; z-score for `clarity` and `spq`
4. Fits Stan model (4 chains, 2000 iterations, 1000 warmup)
5. Saves outputs

**Output** (`<output_dir>/`)

| File | Location | Description |
|---|---|---|
| `<tag>_trial_by_trial_prior_posterior.csv` | `<output_dir>/` | Per-trial prior (`cloze_py`), posterior (`post_py_mean`), log prior weight (`lognu_mean`) |
| `<tag>_traceplot.png` | `<output_dir>/plot/` | MCMC trace plot for gamma parameters |
| `<tag>_forest_plot.png` | `<output_dir>/plot/` | Forest plot of posterior estimates (median + 50/95% CI) |

**Notes**
- Auto-detects CPU cores for parallel chains
- On first run may write `~/.R/Makevars` for a compiler fix on CentOS — restart R and rerun if prompted
- Stan model cache (`.rds`, `.so`) is cleared automatically on each run

---

## Full BIDS + derivatives directory structure

```
bids/
├── stimuli/
│   ├── grids/                         ← .TextGrid files (manual, study-level)
│   ├── tr_reports/                    ← .report files (manual, study-level)
│   └── All_sentences_translated_with_en_cloze_prob_and_entropy.csv
└── sub-01/
    └── ses-01/
        ├── anat/
        ├── func/
        ├── fmap/
        ├── dwi/
        └── behav/
            ├── main_task_data_sub-811422_..._raw.csv          ← task software output
            ├── audio_responses/                                ← manually copied
            ├── main_task_data_sub-811422_..._transcribed.csv  ← after delta transcribe
            ├── main_task_data_sub-811422_..._enriched.csv     ← after delta enrich
            ├── sub-01_ses-01_task-sentencecompletion_events.tsv
            ├── sub-01_ses-01_task-sentencecompletion_sentences.xlsx
            ├── sub-01_ses-01_task-sentencecompletion_binned-entropy.csv
            ├── sub-01_ses-01_task-sentencecompletion_prior.csv
            └── sub-01_ses-01_task-sentencecompletion_desc-retroicor_regressors.tsv

derivatives/
├── fmriprep/sub-01/ses-01/
│   ├── anat/
│   └── func/
├── first_level/sub-01/ses-01/
│   ├── sub-01_ses-01_contrast-entropy_stat-t_statmap.nii.gz
│   ├── sub-01_ses-01_contrast-prior_stat-t_statmap.nii.gz
│   ├── sub-01_ses-01_contrast-task_stat-t_statmap.nii.gz
│   ├── sub-01_ses-01_design_matrix.png
│   └── sub-01_ses-01_report.html
└── encoding/sub-01/ses-01/
    ├── corrs.npy
    ├── weights.npy
    ├── best_alphas.npy
    ├── bootstrap_corrs.npy
    ├── voxel_mask.npy
    ├── sub-01_ses-01_task-listening_encoding_corrs.nii.gz
    └── corr_glass_brain.png
```

---

## Container (Singularity / Apptainer)

```bash
apptainer build delta.sif delta.def

apptainer exec --bind /data:/data delta.sif \
  delta encoding --bids /data/bids ...
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `dcm2bids` | DICOM → BIDS conversion |
| `fmriprep` | Structural + functional preprocessing |
| `nilearn` | NIfTI I/O, first-level GLM, glass-brain plotting |
| `nibabel` | NIfTI loading |
| `numpy`, `scipy` | Numerical computation |
| `torch`, `transformers` | LLM feature extraction (encoding model + cloze) |
| `pandas`, `openpyxl` | Behavioural data loading |
| `tqdm` | Progress bars |
| `playsound` | Audio playback during transcription (optional; falls back to `aplay`/`afplay`) |
| `joblib` | Parallelisation / caching |
| `matplotlib` | Visualisation |
| `rstan`, `dplyr`, `ggplot2`, `tidyr` | Bayesian modelling (R) |
| `Freesurfer version >= 7.40` | FSL and Freesurfer |

All Python dependencies are pinned in the Singularity container via `delta.def`.

---
