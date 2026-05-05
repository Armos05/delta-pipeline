# DELTA Neuroimaging Pipeline

DICOM → BIDS → fMRIPrep pipeline packaged as an Apptainer container.

---

## Requirements

- Apptainer installed on the host
- `delta.sif` — main pipeline container
- `fmriprep-25.2.4.sif` — for preprocessing (separate download)
- FreeSurfer license file (`license.txt`)

---

## Expected raw data folder structure

The pipeline expects raw DICOM data organized by subject and session:

```
raw_data/
  sub-01/
    ses-01/
      t1_mp2rage_sag_iso07_UNI-DEN/        ← one folder per MRI series
      t2_haste_sag_scout/
      ep2d_bold_tra_p2_podcast/
      rs_cmrr_1p5_fs_300_dyn/
      ep2d_bold_tra_p2_ref_podcast/
      ep2d_bold_tra_p2_ref_podcast_PA/
      rs_cmrr_1p5_fs_PA/
      gre_field_mapping_tra/
      ep2d_diff_b2000_q64_AP/
      ep2d_diff_b2000_q64_PA/
      fl3d_mt_tra_tr55_*/                   ← neuromelanin series
  sub-02/
    ses-01/
      ...
```

Each series folder contains raw DICOM files (`.dcm` or no extension).  
Subject folders must be named `sub-XX`. Session folders must be named `ses-XX`.

---

## BIDS output structure (what you get after conversion)

```
bids_output/
  dataset_description.json
  .bidsignore
  sub-01/
    ses-01/
      anat/
        sub-01_ses-01_T1w.nii.gz
        sub-01_ses-01_T1w.json
        sub-01_ses-01_T2w.nii.gz
        sub-01_ses-01_T2w.json
      func/
        sub-01_ses-01_task-podcast_bold.nii.gz
        sub-01_ses-01_task-podcast_bold.json
        sub-01_ses-01_task-rest_bold.nii.gz
        sub-01_ses-01_task-rest_bold.json
        sub-01_ses-01_task-thresholding_bold.nii.gz
        sub-01_ses-01_task-thresholding_bold.json
        sub-01_ses-01_task-sentencecompletion_bold.nii.gz
        sub-01_ses-01_task-sentencecompletion_bold.json
      fmap/
        sub-01_ses-01_acq-podcast_dir-AP_epi.nii.gz
        sub-01_ses-01_acq-podcast_dir-PA_epi.nii.gz
        sub-01_ses-01_acq-rest_dir-PA_epi.nii.gz
        sub-01_ses-01_acq-sentencecompletion_dir-PA_epi.nii.gz
        sub-01_ses-01_magnitude1.nii.gz
        sub-01_ses-01_magnitude2.nii.gz
        sub-01_ses-01_phasediff.nii.gz
        (+ matching .json files for all above)
      dwi/
        sub-01_ses-01_dir-AP_dwi.nii.gz
        sub-01_ses-01_dir-AP_dwi.bvec
        sub-01_ses-01_dir-AP_dwi.bval
        sub-01_ses-01_dir-AP_dwi.json
      nm/
        sub-01_ses-01_NM.nii.gz
        sub-01_ses-01_NM.json
```

---

## Step 1 — Convert DICOM to BIDS

### All subjects (automatic discovery):
```bash
apptainer run \
  --bind /path/to/src:/opt/delta/src \
  --bind /path/to/raw_data:/path/to/raw_data \
  --bind /path/to/bids_output:/path/to/bids_output \
  delta.sif convert \
  --raw /path/to/raw_data \
  --bids /path/to/bids_output \
  --config /opt/delta/configs/dcm2bids_config.json \
  --state /path/to/delta_state.json
```

### One subject only:
```bash
apptainer run \
  --bind /path/to/src:/opt/delta/src \
  --bind /path/to/raw_data:/path/to/raw_data \
  --bind /path/to/bids_output:/path/to/bids_output \
  delta.sif convert \
  --raw /path/to/raw_data \
  --bids /path/to/bids_output \
  --config /opt/delta/configs/dcm2bids_config.json \
  --state /path/to/delta_state.json \
  --subjects sub-01
```

### One subject, one session only:
```bash
apptainer run \
  ... \
  delta.sif convert \
  --raw /path/to/raw_data \
  --bids /path/to/bids_output \
  --config /opt/delta/configs/dcm2bids_config.json \
  --state /path/to/delta_state.json \
  --subjects sub-01 \
  --sessions ses-01
```

### Force re-convert (already converted subjects):
Add `--force` flag.

### Dry run (see what would happen without converting):
Add `--dry-run` flag.

### Interactive mode:
```bash
bash launch.sh
```
Prompts for all paths interactively, then runs conversion + BIDS validation.

---

## Step 2 — Validate BIDS output

```bash
apptainer run \
  --bind /path/to/src:/opt/delta/src \
  --bind /path/to/bids_output:/path/to/bids_output \
  delta.sif bids-validator /path/to/bids_output --ignoreWarnings
```

---

## Step 3 — Run fMRIPrep (preprocessing)

fMRIPrep must be run **directly on the host** (not inside the container) to avoid nested Apptainer issues.

```bash
python3 /path/to/src/cli.py preprocess \
  --bids /path/to/bids_output \
  --output /path/to/bids_output/derivatives/fmriprep \
  --work-dir /path/to/tmp \
  --fmriprep-sif /path/to/fmriprep-25.2.4.sif \
  --fs-license /path/to/freesurfer/license.txt \
  --state /path/to/delta_state_fmriprep.json
```

### One subject only:
Add `--subjects sub-01`.

### Enable FreeSurfer recon-all (disabled by default, adds hours):
Add `--fs-reconall`.

### Change output space (default is T1w native space):
Add `--output-spaces MNI152NLin2009cAsym` or any fMRIPrep-valid space.

fMRIPrep output lands in `bids_output/derivatives/fmriprep/`.  
Check the HTML report at `derivatives/fmriprep/sub-01.html` to verify distortion correction worked.

---

## Check conversion status

```bash
apptainer run delta.sif status --state /path/to/delta_state.json
```

---

## What to change and where

### Scanner series names changed (different protocol or site):
Edit `configs/dcm2bids_config.json` — update the `SeriesDescription` field in the relevant entry.  
Each entry matches one MRI series to one BIDS output file.

### Add a new task or modality:
Add a new entry to `configs/dcm2bids_config.json` following the existing pattern.

### Subject folder naming convention changed (e.g. `participant-01` instead of `sub-01`):
The pipeline hardcodes `sub-` prefix in `src/convert.py:discover_subjects()` and `src/preprocess.py`.  
Update those two functions.

### Session folder naming changed:
Update `src/convert.py:discover_sessions()`.

### Add a new pipeline step (e.g. MRS conversion):
Add a new function in `src/convert.py` and call it after `run_nm_conversion()` in `run_convert()`.

### Change where code changes take effect without rebuilding the container:
Bind-mount the src directory at runtime:
```bash
--bind /path/to/src:/opt/delta/src
```
Changes to Python files take effect immediately — no container rebuild needed.

---

## UZH lab run commands (stricon server)

### Convert (all subjects on NFS):
```bash
apptainer run \
  --bind /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/src:/opt/delta/src \
  --bind /mnt/nfs/stricon_data:/mnt/nfs/stricon_data \
  /mnt/data/docker/custom/delta.sif convert \
  --raw /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data \
  --bids /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/bids_output \
  --config /opt/delta/configs/dcm2bids_config.json \
  --state /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/delta_state.json
```

### BIDS Validation:
```bash
apptainer run \
  --bind /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/src:/opt/delta/src \
  --bind /mnt/nfs/stricon_data:/mnt/nfs/stricon_data \
  /mnt/data/docker/custom/delta.sif bids-validator \
  /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/bids_output --ignoreWarnings
```

### fMRIPrep (run on host, not inside container):
```bash
python3 /home/akmisr@d.uzh.ch/scripts/DELTA_scripts/analysis_pipeline/src/cli.py preprocess \
  --bids /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/bids_output \
  --output /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/bids_output/derivatives/fmriprep \
  --work-dir /mnt/nfs/stricon_data/DELTA_files/tmp \
  --fmriprep-sif /mnt/data/docker/fmriprep-25.2.4.sif \
  --fs-license /mnt/data/freesurfer/freesurfer/license.txt \
  --state /mnt/nfs/stricon_data/DELTA_files/DELTA_raw_data/delta_state_fmriprep.json
```
