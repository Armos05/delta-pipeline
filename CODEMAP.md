# CODEMAP — Raw DICOM → BIDS (one subject, one session)

## Overview

Input: raw DICOM folders on disk, one per MRI series.
Output: BIDS-compliant directory with NIfTI files, JSON sidecars, and metadata.
Orchestrator: `src/convert.py` called via `src/cli.py convert`.

---

## Step-by-step flow

### 0. Entry point

```
apptainer run delta.sif convert --raw <raw_dir> --bids <bids_dir> --config configs/dcm2bids_config.json --state delta_state.json
```

`cli.py:main()` → parses args → calls `cmd_convert(args)` → calls `convert.run_convert(args)`.

---

### 1. BIDS root bootstrap (`convert.init_bids_root`)

Runs once when `bids_dir/dataset_description.json` does not yet exist.

Copies from `configs/`:
- `dataset_description.json` → `bids_dir/dataset_description.json`
- `bidsignore` → `bids_dir/.bidsignore`  (tells validator to ignore `nm/` and `mrs/`)

---

### 2. State check (`state.needs_conversion`)

For each subject/session, `state.py` computes an MD5 hash of all filenames and file sizes inside the DICOM directory.

Compares against `delta_state.json`. If hash matches → **SKIP** (already converted). If different or absent → **proceed**.

Hash logic (`state._hash_dicom_dir`): iterates `dicom_dir.rglob("*")`, accumulates `filename + size` strings, returns hexdigest. Full file reads are never done — fast even for large DICOM sets.

---

### 3. Subject/session discovery

`convert.discover_subjects(raw_dir)` — lists `sub-*` folders under raw root.
`convert.discover_sessions(subject_dir)` — lists `ses-*` folders; defaults to `ses-01` if none found.

Expected raw layout:
```
raw_dir/
  sub-01/
    ses-01/
      t1_mp2rage_sag_iso07_UNI-DEN/   ← one folder per series
      ep2d_bold_tra_p2_podcast/
      gre_field_mapping_tra/
      fl3d_mt_tra_tr55_*/              ← NM series
      ...
```

---

### 4. dcm2bids conversion (`convert.run_dcm2bids`)

Calls `dcm2bids` for one subject/session:

```
dcm2bids \
  --dicom_dir raw_dir/sub-01/ses-01 \
  --participant 01 \
  --session 01 \
  --config configs/dcm2bids_config.json \
  --output_dir bids_dir \
  --clobber
```

dcm2bids internally runs `dcm2niix` on every DICOM series to extract NIfTI + JSON sidecar, then matches each sidecar against `dcm2bids_config.json`.

#### How matching works

Each `descriptions` entry in the config specifies:
- `criteria` — fields that must match in the DICOM sidecar JSON (e.g. `SeriesDescription`, `ImageType`, `EchoNumber`, `PhaseEncodingDirection`)
- `datatype` + `suffix` — the BIDS folder and file suffix
- `custom_entities` — extra BIDS entities inserted into the filename (e.g. `task-podcast`, `dir-AP`)
- `sidecar_changes` — key/value pairs merged into the output JSON sidecar after conversion

Matching rules (dcm2bids v3.2.0):
- `SeriesDescription` — exact string match (no regex)
- `ImageType` — exact full array match, e.g. `["ORIGINAL","PRIMARY","M","NONE"]` (not a subset)
- `EchoNumber` — integer equality
- `PhaseEncodingDirection` — exact string, e.g. `"j-"` for AP

#### Config entries and their BIDS output

| Config match | BIDS output path |
|---|---|
| `t1_mp2rage_sag_iso07_UNI-DEN` | `anat/sub-01_ses-01_T1w.{nii.gz,json}` |
| `t2_haste_sag_scout` | `anat/sub-01_ses-01_T2w.{nii.gz,json}` |
| `ep2d_bold_tra_p2_podcast` | `func/sub-01_ses-01_task-podcast_bold.{nii.gz,json}` |
| `rs_cmrr_1p5_fs_300_dyn` | `func/sub-01_ses-01_task-rest_bold.{nii.gz,json}` |
| `rs_cmrr_1p5_fs_380_dyn` | `func/sub-01_ses-01_task-rest_bold.{nii.gz,json}` (alternate TR) |
| `ep2d_bold_tra_p2_ref_thresholding` | `func/sub-01_ses-01_task-thresholding_bold.{nii.gz,json}` |
| `ep2d_bold_tra_p2_completion_task` | `func/sub-01_ses-01_task-sentencecompletion_bold.{nii.gz,json}` |
| `ep2d_bold_tra_p2_ref_podcast` (j-) | `fmap/sub-01_ses-01_acq-podcast_dir-AP_epi.{nii.gz,json}` |
| `ep2d_bold_tra_p2_ref_podcast_PA` | `fmap/sub-01_ses-01_acq-podcast_dir-PA_epi.{nii.gz,json}` |
| `rs_cmrr_1p5_fs_PA` | `fmap/sub-01_ses-01_acq-rest_dir-PA_epi.{nii.gz,json}` |
| `ep2d_bold_tra_p2_ref_PA` | `fmap/sub-01_ses-01_acq-sentencecompletion_dir-PA_epi.{nii.gz,json}` |
| `gre_field_mapping_tra` EchoNumber 1 M | `fmap/sub-01_ses-01_magnitude1.{nii.gz,json}` |
| `gre_field_mapping_tra` EchoNumber 2 M | `fmap/sub-01_ses-01_magnitude2.{nii.gz,json}` |
| `gre_field_mapping_tra` PHASE | `fmap/sub-01_ses-01_phasediff.{nii.gz,json}` |
| `ep2d_diff_b2000_q64_AP` | `dwi/sub-01_ses-01_dir-AP_dwi.{nii.gz,bvec,bval,json}` |
| `ep2d_diff_b2000_q64_PA` | `fmap/sub-01_ses-01_acq-dwi_dir-PA_epi.{nii.gz,json}` |

Unmatched series land in `bids_dir/tmp_dcm2bids/` — not in final BIDS output.

#### B0Field / IntendedFor (fieldmap linkage)

BOLD JSON sidecars get `B0FieldSource` injected via `sidecar_changes`:
- podcast BOLD → `"B0FieldSource": "pe_polar_podcast"`
- rest BOLD → `"B0FieldSource": "pe_polar_rest"`
- sentencecompletion BOLD → `"B0FieldSource": "pe_polar_sentencecompletion"`

PA EPI fieldmaps get `B0FieldIdentifier` + `IntendedFor` injected:
- rest PA EPI → `"B0FieldIdentifier": "pe_polar_rest"`, `"IntendedFor": [<BIDS URI to rest_bold>]`
- etc.

This tells fMRIPrep to use PEPOLAR/TOPUP distortion correction (AP+PA EPI pair) for each BOLD run.

GRE fieldmap (magnitude1/2 + phasediff) also gets `B0FieldIdentifier: "gre_fmap"`, but no BOLD references it via `B0FieldSource` — so fMRIPrep uses PEPOLAR, not GRE, for SDC.

---

### 5. NM (Neuromelanin) conversion (`convert.run_nm_conversion`)

dcm2bids cannot write to non-standard BIDS folders. NM data uses series `fl3d_mt_tra_tr55` and belongs in `nm/` — not a BIDS-standard folder. So it is handled separately after dcm2bids.

`find_nm_dicom(session_dicom_dir)` finds the primary series folder:
- name starts with `fl3d_mt_tra_tr55`
- excludes `_MPR_` reconstructions
- excludes `_ND` reconstructions
- returns first alphabetical match

Runs `dcm2niix` directly:
```
dcm2niix -z y -f sub-01_ses-01_NM -o bids_dir/sub-01/ses-01/nm/ <nm_dicom_dir>
```

Output: `bids_dir/sub-01/ses-01/nm/sub-01_ses-01_NM.nii.gz` + `.json`

---

### 6. State update (`state.mark_converted`, `state.save_state`)

On success, writes to `delta_state.json`:
```json
{
  "subjects": {
    "sub-01": {
      "ses-01": {
        "hash": "<md5>",
        "converted": ["anat", "func", "fmap", "dwi"],
        "preprocessed": false,
        "converted_at": "2026-05-05T...",
        "preprocessed_at": null
      }
    }
  }
}
```

State is written atomically: written to `.tmp` file first, then renamed. If a future run sees the same hash, the session is skipped.

---

## Final BIDS output structure (sub-01/ses-01)

```
bids_dir/
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
        sub-01_ses-01_acq-podcast_dir-AP_epi.json
        sub-01_ses-01_acq-podcast_dir-PA_epi.nii.gz
        sub-01_ses-01_acq-podcast_dir-PA_epi.json
        sub-01_ses-01_acq-rest_dir-PA_epi.nii.gz
        sub-01_ses-01_acq-rest_dir-PA_epi.json
        sub-01_ses-01_acq-sentencecompletion_dir-PA_epi.nii.gz
        sub-01_ses-01_acq-sentencecompletion_dir-PA_epi.json
        sub-01_ses-01_magnitude1.nii.gz
        sub-01_ses-01_magnitude1.json
        sub-01_ses-01_magnitude2.nii.gz
        sub-01_ses-01_magnitude2.json
        sub-01_ses-01_phasediff.nii.gz
        sub-01_ses-01_phasediff.json
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

## Key code locations

| What | File | Function |
|---|---|---|
| CLI routing | `src/cli.py` | `cmd_convert()` |
| Main loop | `src/convert.py` | `run_convert()` |
| BIDS root init | `src/convert.py` | `init_bids_root()` |
| State check | `src/state.py` | `needs_conversion()` |
| DICOM hash | `src/state.py` | `_hash_dicom_dir()` |
| dcm2bids call | `src/convert.py` | `run_dcm2bids()` |
| NM detection | `src/convert.py` | `find_nm_dicom()` |
| NM conversion | `src/convert.py` | `run_nm_conversion()` |
| State save | `src/state.py` | `mark_converted()`, `save_state()` |
| Series mapping | `configs/dcm2bids_config.json` | — |
