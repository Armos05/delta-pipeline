# DELTA Neuroimaging Pipeline — Physio Integration Patch

This document describes all changes made to integrate physiological regressor
generation into the DELTA pipeline.

---

## What changed

| File | Change |
|------|--------|
| `src/physio.py` | **New** — pure-Python RETROICOR implementation (replaces MATLAB/TAPAS) |
| `src/cli.py` | Added `physio` sub-command |
| `src/state.py` | Added `physio_done` / `physio_at` fields + `mark_physio_done` / `needs_physio` helpers |
| `Dockerfile` | **New** — containerises the full pipeline without MATLAB |
| `Apptainer.def` | **New** — Apptainer image definition for HPC use |

---

## Expected source-data layout

Place raw Siemens physiological log files under `sourcedata/physio/`:

```
<bids_root>/
  sub-01/
    ses-01/
      physio/
        sub-01_ses-01_task-sentencecompletion_recording-cardiac_physio.puls
        sub-01_ses-01_task-sentencecompletion_recording-respiratory_physio.resp
```

The naming follows BIDS convention for physio source data.

---

## Step 4 — Generate physiological regressors

Run **after** BIDS conversion (Step 1) and ideally after fMRIPrep (Step 3).

### All subjects, default task (`sentencecompletion`)

```bash
apptainer run \
  --bind /path/to/src:/opt/delta/src \
  --bind /path/to/bids:/data/bids \
  delta.sif physio \
    --bids /data/bids \
    --state /data/bids/deltastate.json
```

### One subject, one session, explicit TR and volume count

```bash
apptainer run \
  --bind /path/to/src:/opt/delta/src \
  --bind /path/to/bids:/data/bids \
  delta.sif physio \
    --bids   /data/bids \
    --subjects sub-01 \
    --sessions ses-01 \
    --tasks    sentencecompletion \
    --tr       2.0 \
    --n-vols   206 \
    --state    /data/bids/deltastate.json
```

### Multiple tasks

```bash
delta.sif physio --bids /data/bids --tasks sentencecompletion podcast rest
```

### Force re-generation

```bash
delta.sif physio --bids /data/bids --force
```

---

## Output

For each run, two files are written to `<bids>/sub-XX/ses-XX/func/`:

| File | Description |
|------|-------------|
| `sub-XX_ses-XX_task-<task>_desc-retroicor_regressors.tsv` | 18-column regressor matrix (one row per volume) |
| `sub-XX_ses-XX_task-<task>_desc-retroicor_regressors.json` | BIDS sidecar with TR, n_vols, column names, and source paths |

### Regressor columns (18 total, matching original TAPAS configuration)

| Group | Columns | Count |
|-------|---------|-------|
| Cardiac (3rd order) | `cardiac_sin1/cos1` … `cardiac_sin3/cos3` | 6 |
| Respiratory (4th order) | `resp_sin1/cos1` … `resp_sin4/cos4` | 8 |
| Interaction (1st order) | `card_resp_sin1`, `card_resp_cos1` | 2 |
| **Total** | | **18** |

Pass the TSV path directly to nilearn's `FirstLevelModel` via
`confounds` or as an additional design matrix column set.

---

## Building the container

### Docker
```bash
docker build -t delta:latest .
```

### Apptainer (HPC)
```bash
# Build from definition file
apptainer build delta.sif Apptainer.def

# Or convert from Docker Hub / local Docker daemon
apptainer build delta.sif docker-daemon://delta:latest
```

---

## TR and volume-count resolution order

1. `--tr` / `--n-vols` CLI arguments (highest priority)
2. `RepetitionTime` in `<bids>/sub-XX/ses-XX/func/*_bold.json` sidecar
3. NIfTI header of `*_bold.nii.gz` (for `n_vols`)
4. Error with a descriptive message

---

## UZH stricon server — physio command

```bash
apptainer run \
  --bind /home/akmis/rd.uzh.ch/scripts/DELTA/scripts/analysis/pipeline/src:/opt/delta/src \
  --bind /mnt/nfs/stricon/data:/mnt/nfs/stricon/data \
  /mnt/data/docker/custom/delta.sif physio \
    --bids  /mnt/nfs/stricon/data/DELTA/files/DELTA/rawdata/bidsoutput \
    --tasks sentencecompletion \
    --state /mnt/nfs/stricon/data/DELTA/files/DELTA/rawdata/deltastate.json
```
