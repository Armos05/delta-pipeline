# Comprehensive Data Storage Plan
## Longitudinal Neuroimaging Study

**Version:** 1.0  
**Date:** 2026-06-07  
**Status:** Draft

---

## 1. Study Overview

| Parameter | Details |
|-----------|---------|
| Design | Longitudinal, repeated-measures |
| Participants | ~30 subjects |
| Sessions per subject | 12 scans over 3 months |
| Total sessions | ~360 |
| Transfer method | Manual: scanner → external drive → central storage |

### 1.1 Data Modalities

| Modality | Type | Typical Raw Size (DICOM) | Typical Converted Size (NIfTI.gz) |
|----------|------|--------------------------|-----------------------------------|
| T1w | Structural MRI | ~50 MB | ~100 MB |
| T2w | Structural MRI | ~50 MB | ~100 MB |
| fMRI | Functional MRI | ~1 GB | ~1.5 GB |
| DTI | Diffusion MRI | ~100 MB | ~400-600 MB |
| NM (Neuromelanin) | Structural MRI | ~50 MB | ~500 MB |
| MRS | Spectroscopy | ~150 MB | ~200 MB  |
| Behavioural (PANSS etc.) | REDCap / CSV | Negligible | Negligible |

**Estimated storage per subject per session:** ~2-3 GB raw DICOM; ~4-5 GB converted  (Total ~ 10 Gbs)
- **Recommended provisioned storage: 5 TB** (raw + converted + derivatives + backups on central)

---

## 2. Naming Conventions

### 2.1 Subject IDs

- Format: `sub-XXX` (XXXX = zero-padded 3-digit number)
- Example: `sub-001`, `sub-030`
- IDs are pseudonymous. A separate, access-controlled **participant registry** maps real names to IDs.

### 2.2 Session Labels

- Format: `ses-XXX` (XXXX = zero-padded 3-digit number),
- Session numbering is per-subject and resets for each participant.

### 2.3 Run Labels (if multiple runs per session)

- Format: `run-01`, `run-02`, etc.

### 2.4 External Drive Label

- Format: `DELTA_DRIVE_<site>_<YYYYMMDD>` — e.g., `DELTA_DRIVE_UZH_20260610`
- Each drive should be labelled physically and electronically.

---

## 3. BIDS Directory Structure

All neuroimaging data must conform to [BIDS v1.9+](https://bids-specification.readthedocs.io/).

```
DELTA_study/
├── README                          # Plain-text study description
├── CHANGES                         # Version changelog
├── dataset_description.json        # BIDS-required metadata
├── participants.tsv                 # Pseudonymous participant table
├── participants.json                # Column descriptions for participants.tsv
├── sessions.tsv                     # Session-level metadata (date, site, scanner)
├── .bidsignore                      # Files excluded from BIDS validation
│
├── code/                            # Analysis scripts, SOPs, this plan
│   └── data_storage_plan.md
│
├── sourcedata/                      # Raw, unmodified scanner output (DICOM / vendor)
│   └── sub-001/
│       └── ses-01/
│           ├── anat/                # DICOM folders per sequence
│           ├── func/
│           ├── dwi/
│           ├── nm/
│           └── mrs/
│
├── rawdata/                         # BIDS-converted NIfTI data (the "primary" dataset)
│   ├── sub-001/
│   │   ├── ses-01/
│   │   │   ├── anat/
│   │   │   │   ├── sub-001_ses-01_T1w.nii.gz
│   │   │   │   ├── sub-001_ses-01_T1w.json
│   │   │   │   ├── sub-001_ses-01_T2w.nii.gz
│   │   │   │   ├── sub-001_ses-01_T2w.json
│   │   │   │   ├── sub-001_ses-01_acq-NM_T1w.nii.gz   # Neuromelanin
│   │   │   │   └── sub-001_ses-01_acq-NM_T1w.json
│   │   │   ├── func/
│   │   │   │   ├── sub-001_ses-01_task-rest_bold.nii.gz
│   │   │   │   ├── sub-001_ses-01_task-rest_bold.json
│   │   │   │   └── sub-001_ses-01_task-rest_events.tsv  # if task fMRI
│   │   │   ├── dwi/
│   │   │   │   ├── sub-001_ses-01_dwi.nii.gz
│   │   │   │   ├── sub-001_ses-01_dwi.bvec
│   │   │   │   ├── sub-001_ses-01_dwi.bval
│   │   │   │   └── sub-001_ses-01_dwi.json
│   │   │   ├── fmap/                                           # Field maps (if acquired)
│   │   │   │   ├── sub-001_ses-01_magnitude1.nii.gz
│   │   │   │   └── sub-001_ses-01_phasediff.nii.gz
│   │   │   └── mrs/                                            # BIDS-MRS extension (BEP021)
│   │   │       ├── sub-001_ses-01_svs.nii.gz
│   │   │       └── sub-001_ses-01_svs.json
│   │   ├── ses-02/ ... ses-12/
│   │   └── sub-001_sessions.tsv
│   └── sub-0002/ ... sub-0030/
│
├── phenotype/                       # Behavioural & clinical data (BIDS phenotype)
│   ├── panss.tsv                    # PANSS scores across participants/sessions
│   ├── panss.json                   # Variable descriptions
│   └── <other_measures>.tsv
│
└── derivatives/                     # Processed outputs (never overwrite rawdata)
    ├── fmriprep/
    ├── qsiprep/
    ├── mriqc/
    └── freesurfer/
```

### 3.1 Key BIDS Files

**`dataset_description.json`** (minimum required fields):
```json
{
  "Name": "DELTA Longitudinal Neuroimaging Study",
  "BIDSVersion": "1.9.0",
  "DatasetType": "raw",
  "License": "CC0",
  "Authors": ["Last, First", "..."],
  "Acknowledgements": "...",
  "HowToAcknowledge": "...",
  "ReferencesAndLinks": [],
  "DatasetDOI": ""
}
```

**`participants.tsv`** columns (minimum):
```
participant_id   age   sex   diagnosis   site   scanner_id
sub-001    ...   ...   ...         UZH    Prisma_01
```

**`sessions.tsv`** (per subject, placed in `sub-<label>/` folder) columns:
```
participant_id    session_id   scan_date   session_number   weeks_from_baseline   notes
sub-001  ses-01       2026-06-10  1                0                     baseline
```

---

## 4. Data Transfer Standard Operating Procedure (SOP)

### 4.1 Step-by-Step Transfer Protocol

```
[SCANNER] ──► [EXTERNAL DRIVE] ──► [VERIFY] ──► [CENTRAL STORAGE] ──► [VERIFY] ──► [LOG]
```

#### Step 1: Export from Scanner to the External Drive.

1. After each session, export DICOM data to a **study-specific folder** on the scanner console.
2. Folder name at export: `DELTA_<sub-ID>_<ses-ID>_<YYYYMMDD>` — e.g., `DELTA_sub-001_ses-01_20260610`
3. Include ALL sequences (T1w, T2w, fMRI, DTI, NM, MRS).
4. Do NOT anonymise on the scanner — pseudonymisation happens at the conversion step.

#### Step 2: Move the data to the nas share

1. Move the files on the nas-share.
2. Copy the entire session folder to:
   ```
   /Volumes/Studies/DELTA-LANG/DATA/DELTA-longitudinal/sourcedata/sub-001/ses-01/
   ```

#### Step 3: Now you can run the delta.sif container
1. Follow the Readme file from the container.

#### Step 4: Now you can run the delta.sif container
1. Run QC steps before you start analyzing the data.

---

### 4.2 Master Session Tracker

Maintain `code/DELTA_session_tracker.tsv` tracking completion status per modality:

| sub_id | session | scan_date | T1w | T2w | fMRI | DTI | NM | MRS | PANSS | uploaded | bids_valid | qc_pass |
|--------|---------|-----------|-----|-----|------|-----|----|----|-------|----------|------------|---------|
| 001 | ses-01 | 2026-06-10 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | pending |

---

## 5. Anonymisation and Data Protection

### 5.1 Pseudonymisation Pipeline

1. Real participant names and DOBs exist **only** in the participant registry.
2. Participant registry stored separately from imaging data, access restricted to PI and data manager.
3. DICOM headers contain identifying information — **strip before sharing externally** using:
   ```bash
   dcm2niix -ba y ...  # anonymise during BIDS conversion
   ```
   Or use `pydicom` / `deid` for DICOM-level anonymisation of `sourcedata/` if sharing.
4. MRS vendor files may contain patient name in header — check and strip before external sharing.
5. T1w/T2w images **must be defaced** before any external data sharing:
   ```bash
   pydeface sub-001_ses-01_T1w.nii.gz
   ```

### 5.2 GDPR / Cantonal Data Protection Compliance (Switzerland)

- Imaging data = health data = **sensitive personal data** under FADP (nDSG).
- Data must reside on Swiss-hosted or approved servers.
- Data sharing agreements required for external collaborators.
- Retain for minimum 10 years post-study completion per Swiss research regulations.
- Document all data access in an access log.

---

## 6. Quality Control (QC) Checkpoints

Check the QC Readme.md

## 7. Metadata and Documentation Requirements

### 7.1 Per-Session Metadata (collected at scanner)

- Date and time of scan
- Scanner ID and software version
- Operator initials
- Any protocol deviations (e.g., shortened sequence, participant discomfort)
- Head coil used
- Session notes (e.g., subject fell asleep, excessive motion)

Record these in the **sessions.tsv** file and the Master Session Tracker.

### 7.2 Protocol Version Control

- Store MRI sequence protocol files (`.exar`, `.pdf` exports from scanner) in `code/scanner_protocols/`.
- Name by date: `DELTA_protocol_v1_20260601.pdf`
- If the protocol changes mid-study, document the change in `CHANGES` and update `sessions.tsv` with a `protocol_version` column.

### 7.3 Behavioural Data

- Enter PANSS and other measures into REDCap (or equivalent) **within 24h** of assessment.
- Export REDCap data monthly as `.csv` to `phenotype/` on central storage.
- REDCap project must have participant IDs matching the `sub-<XXXX>` scheme.
- Never store names or DOBs in REDCap project that is synced to imaging storage.

---

## 8. Contingency Procedures

1. Log in Master Session Tracker: modality, reason (participant dropout, scanner error, protocol violation) and mention it in the session.tsv file.
2. Partial sessions (some modalities acquired): still upload what was collected; mark missing modalities in tracker.

---

## 9. Software and Tools

| Task | Tool | Version |
|------|------|---------|
| DICOM → BIDS | dcm2bids + dcm2niix | dcm2bids ≥ 3.0 |
| BIDS validation | bids-validator | ≥ 1.14 |
| fMRI preprocessing | fMRIPrep | ≥ 23.2 |
| Diffusion preprocessing | QSIPrep | ≥ 0.19 |
| MRI QC | MRIQC | ≥ 23.1 |
| DTI QC | eddy_squad | FSL ≥ 6.0.7 |
| MRS processing | Osprey | ≥ 2.4 |
| Checksum | md5sum / shasum | system |
| Transfer | rsync | ≥ 3.2 |
| Defacing | pydeface | ≥ 2.0 |
| Neuroimaging | freesurfer | ≥ 2.0 |

---

## 14. Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-06-07 | — | Initial draft |

