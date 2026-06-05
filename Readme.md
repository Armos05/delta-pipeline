# DELTA — Data Extraction, Labelling and Transfer Automation

End-to-end pipeline for MRI/physio conversion, preprocessing, first-level GLM, and voxelwise encoding-model analysis. All steps are invoked via the unified `delta` CLI.

---

## Repository layout

```
delta/
├── src/
│   ├── cli.py                  # Unified CLI entry-point
│   ├── convert.py              # DICOM → BIDS conversion (dcm2bids)
│   ├── preprocess.py           # fMRIPrep wrapper
│   ├── physio.py               # Physiological noise regressors (RETROICOR)
│   ├── first_level_entropy.py  # First-level GLM (sentence-completion task)
│   ├── encoding_model.py       # Voxelwise encoding model (listening task) ← NEW
│   ├── analyze.py              # Generic script dispatcher + sub-analysis shims
│   ├── validate.py             # BIDS validator wrapper
│   ├── state.py                # Run-state tracking
│   ├── helper.py               # Shared path helpers
│   └── ridge_utils/            # Encoding-model support library
│       ├── ridge.py            # Bootstrap ridge regression
│       ├── DataSequence.py     # Word/TR sequence container
│       ├── dsutils.py          # Word/phoneme DataSequence builders
│       ├── stimulus_utils.py   # TextGrid + TRFile loaders
│       ├── tokenization_helpers.py  # LLM accumulator & feature extraction
│       ├── util.py             # Delay embedding, misc utilities
│       ├── utils.py            # make_delayed, mult_diag
│       ├── SemanticModel.py    # Legacy semantic model container
│       ├── textgrid.py         # Praat TextGrid parser
│       └── npp.py              # Noise-power projection
├── notebooks/
│   ├── brain_vs_shift_beta.ipynb   # Encoding model results visualisation ← NEW
│   └── simes_test.ipynb            # Whole-brain significance testing ← NEW
├── delta.def                   # Singularity/Apptainer container definition
└── README.md
```

---

## Pipeline overview

```
DICOM data
    │
    ▼
delta convert          # DICOM → BIDS (dcm2bids); creates behav/ folder
    │
    ▼
delta validate         # BIDS validator
    │
    ▼
delta preprocess       # fMRIPrep (T1w + functional preprocessing)
    │
    ├─────────────────────────────────────────┐
    ▼                                         ▼
delta firstlevel           delta encoding
(sentence-completion GLM)  (listening-task ridge regression)
    │                                         │
    ▼                                         ▼
GLM contrast maps          corrs.npy / weights.npy
    │                                         │
    └─────────────────┬───────────────────────┘
                      ▼
              notebooks/
              brain_vs_shift_beta.ipynb
              simes_test.ipynb
```

---

## Step-by-step usage

### 1. Convert

```bash
delta convert \
  --dicom-dir  /data/dicoms/sub-01/ses-01 \
  --bids-dir   /data/bids \
  --subject    sub-01 \
  --session    ses-01
```

Creates the standard BIDS output plus an empty `behav/` directory alongside `anat/`, `func/`, and `fmap/`.

### 2. Validate

```bash
delta validate --bids /data/bids
```

### 3. Preprocess

```bash
delta preprocess \
  --bids         /data/bids \
  --derivatives  /data/derivatives \
  --subject      sub-01 \
  --session      ses-01
```

fMRIPrep outputs land in `derivatives/fmriprep/sub-01/ses-01/`.

### 4. First-level GLM (sentence-completion task)

> **Required manual files** — these must be placed in the subject's `behav/` folder
> before running `delta firstlevel`. They are not generated automatically.

| File | Description | Required |
|---|---|---|
| `{sub}_{ses}_task-sentencecompletion_events.tsv` | Trial onset/duration/condition table (BIDS events format) | ✓ |
| `{sub}_{ses}_task-sentencecompletion_sentences.xlsx` | Full sentence list with entropy values per sentence | ✓ |
| `{sub}_{ses}_task-sentencecompletion_binned-entropy.csv` | Pre-binned entropy values aligned to trial order | ✓ |
| `{sub}_{ses}_task-sentencecompletion_prior.csv` | Prior probability estimates used as a parametric regressor | ✓ |
| `{sub}_{ses}_task-sentencecompletion_desc-retroicor_regressors.tsv` | RETROICOR noise regressors from `delta physio` | ✗ (optional) |

```bash
delta firstlevel \
  --bids                /data/bids \
  --derivatives         /data/derivatives \
  --subject             sub-01 \
  --session             ses-01 \
  --events-csv          /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_events.tsv \
  --sentences-xlsx      /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_sentences.xlsx \
  --binned-entropy-csv  /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_binned-entropy.csv \
  --prior-csv           /data/bids/sub-01/ses-01/behav/sub-01_ses-01_task-sentencecompletion_prior.csv
```

**What the analysis does**

The first-level GLM models BOLD responses during the sentence-completion task. Each trial is modelled with a boxcar regressor convolved with the canonical HRF. Parametric modulators derived from trial-wise information-theoretic entropy (from `--binned-entropy-csv`) and prior probability (from `--prior-csv`) are included as separate regressors. RETROICOR regressors are appended as nuisance covariates if the file is present. The design matrix is estimated in SPM12-style using Nilearn's `FirstLevelModel`.

**Contrasts estimated:** `entropy_modulation`, `prior_modulation`, `task > rest`, and their sum/difference.

**Output** (`derivatives/first_level/sub-01/ses-01/`)

```
sub-01_ses-01_contrast-entropy_stat-t_statmap.nii.gz
sub-01_ses-01_contrast-prior_stat-t_statmap.nii.gz
sub-01_ses-01_contrast-task_stat-t_statmap.nii.gz
sub-01_ses-01_design_matrix.png
sub-01_ses-01_report.html
```

---

### 5. Encoding model (listening task) ← NEW

#### Required manual files

The following stimulus-level files must be prepared once (not per-subject) and placed in
`bids/stimuli/` subdirectories before running `delta encoding`.

| File type | Location | Description |
|---|---|---|
| Praat TextGrid (`.TextGrid`) | `bids/stimuli/grids/` | Forced-alignment word boundaries for each story/run; one file per story named `{storyname}.TextGrid` |
| TR-timing report (`.report`) | `bids/stimuli/tr_reports/` | Scanner trigger log for each story; one file per story named `{storyname}.report`; must contain `trigger` / `sound-start` / `sound-stop` lines |

These files are the output of forced-alignment (e.g. Montreal Forced Aligner) and the stimulus presentation software respectively. They are **not** generated by DELTA and must be added manually.

#### Running the encoding model

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
  [--cache-dir     /path/to/hf_cache]
```

All stories listed in `--stories` must correspond to a `.TextGrid` and `.report` file in the
respective directories. The `--test-stories` set is held out from training; all remaining stories
are used as training data.

**What the analysis does**

1. **BOLD loading** — fMRIPrep preprocessed BOLD
   (`derivatives/fmriprep/sub/ses/func/sub_ses_task-listening_space-{space}_desc-preproc_bold.nii.gz`)
   is loaded and voxel time-series within the brain mask are extracted and z-scored.
2. **Stimulus feature extraction** — Contextual LLM hidden-state features are extracted for each
   word using a sliding-window accumulator (token context window controlled by `--lookback1` /
   `--lookback2`). Features are taken from the specified transformer layer. The word-level feature
   sequence is downsampled to TR resolution via Lanczos interpolation (window = 3).
3. **FIR delay embedding** — Feature matrices are replicated at lags of 1–4 TRs (or the values
   supplied via `--delays`) to account for haemodynamic delay.
4. **Bootstrap ridge regression** — `ridge_utils.bootstrap_ridge` is called with log-spaced
   regularisation parameters (α ∈ [10, 10⁵], 20 values). Per-voxel optimal α is selected by
   averaging held-out Pearson correlations across `--n-boots` bootstrap samples, each using random
   temporal chunks of length `--chunk-len` TRs. Final prediction correlation is evaluated on the
   held-out test story.

**Output** (`derivatives/encoding/sub-01/ses-01/`)

| File | Description |
|---|---|
| `corrs.npy` | `(n_voxels,)` — Pearson r between predicted and actual BOLD on test story |
| `weights.npy` | `(n_features × n_delays, n_voxels)` — ridge regression weight matrix |
| `best_alphas.npy` | `(n_voxels,)` — best regularisation parameter per voxel |
| `bootstrap_corrs.npy` | `(n_alphas, n_voxels, n_boots)` — full bootstrap CV correlation array |
| `voxel_mask.npy` | `(n_voxels, 3)` — integer voxel coordinates within the mask |
| `sub-01_ses-01_task-listening_encoding_corrs.nii.gz` | NIfTI volume of prediction correlations |
| `corr_glass_brain.png` | Glass-brain summary plot of prediction correlations |

---

### 6. Statistical analysis and visualisation (notebooks)

After running the encoding model across all subjects, the results can be statistically evaluated
and visualised using two Jupyter notebooks in `notebooks/`.

#### `brain_vs_shift_beta.ipynb`

Loads `corrs.npy` (and optionally `weights.npy`) for one or more subjects and visualises:
- Voxelwise prediction correlation maps on an MNI brain surface or volume.
- Comparison of encoding model performance across feature-extraction parameter variants
  (e.g. different temporal shifts / lookback windows).
- Ridge regression beta weight profiles across delays and feature dimensions.

**Required inputs:**
- `derivatives/encoding/sub-*/ses-*/corrs.npy`
- `derivatives/encoding/sub-*/ses-*/weights.npy`
- `derivatives/encoding/sub-*/ses-*/voxel_mask.npy`

#### `simes_test.ipynb`

Performs Simes' combined-p whole-brain significance testing across subjects:
- Converts per-voxel prediction correlations to z-scores / p-values.
- Applies Simes' method to combine p-values across subjects at each voxel.
- Produces a thresholded significance map (FWE-corrected via Bonferroni–Simes).
- Exports a NIfTI volume of significant voxels for further group-level analysis.

**Required inputs:**
- `derivatives/encoding/sub-*/ses-*/corrs.npy`
- `derivatives/encoding/sub-*/ses-*/bootstrap_corrs.npy` (used to estimate the null distribution)
- Subject-to-MNI affine matrices (read from fMRIPrep `confounds` or `voxel_mask.npy` coordinates)

---

## Container (Singularity / Apptainer)

Build and run the full pipeline inside the container defined in `delta.def`:

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
| `torch`, `transformers` | LLM feature extraction |
| `joblib` | Parallelisation / caching |
| `pandas`, `openpyxl` | Behavioural data loading |
| `matplotlib` | Visualisation |

All Python dependencies are pinned in the Singularity container via `delta.def`.
