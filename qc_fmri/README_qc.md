# fMRI Quality Control Protocol

You will run these steps after running mriqc and fmriprep.

Step-by-step QC guidelines for fMRI data processed with **MRIQC** and **fMRIPrep**, based on Provins et al. (2023).

## How to use

1. Open [`fMRI_QC_Decision_Tree.md`](fMRI_QC_Decision_Tree.md) as your reference while inspecting reports.
2. Record PASS / FLAG / EXCLUDE for each criterion in [`fMRI_QC_Scoresheet.xlsx`](fMRI_QC_Scoresheet.xlsx).
3. Finally, you can run calculate_tsnr.py while following the instructions in the main Readme.md file (This will give you a sense of how the signal is in the different parts of the brain)

## Files

| File | Description |
|------|-------------|
| `fMRI_QC_Decision_Tree.md` | Full decision tree with criterion descriptions, what each artifact indicates, and reference images |
| `fMRI_QC_Scoresheet.xlsx` | Per-subject QC spreadsheet with dropdowns and conditional formatting |
| `make_qc_sheet.py` | Script to regenerate the Excel file |
| `fnimg-01-1073734.pdf` | Provins et al. 2023 — primary source for criteria A–X |
| `fnins-17-1076824.pdf` | Lepping et al. 2023 — visual inspection in rs-fMRI |
| `fnins-17-1100544.pdf` | Teves et al. 2023 — fMRI QC framework |
| `Data Sheet 1.pdf` | Supplementary figures S1–S13 illustrating each criterion |
