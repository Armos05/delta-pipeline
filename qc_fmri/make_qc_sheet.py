"""
Generate fMRI QC scoresheet Excel file.
Run with: conda run -n dipy python make_qc_sheet.py
"""

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles.differential import DifferentialStyle

OUT_PATH = "fMRI_QC_Scoresheet.xlsx"
N_DATA_ROWS = 100   # empty rows for participants

# ── Column definitions ─────────────────────────────────────────────────────
# (header line 1, header line 2, group)
COLS = [
    # Fixed
    ("SubjID",          "",                     "fixed"),
    ("sess_num",        "",                     "fixed"),
    # ── MRIQC BOLD ──────────────────────────────────────────────────────────
    ("A",               "Background",           "bold"),
    ("BA",              "Signal Dropout",       "bold"),
    ("BB",              "Brain Distortions",    "bold"),
    ("C",               "Aliasing Ghost",       "bold"),
    ("D",               "FOV Wraparound",       "bold"),
    ("EA",              "Carpet: Motion",       "bold"),
    ("EB",              "Carpet: Respiration",  "bold"),
    ("EC",              "Carpet: Coil Failure", "bold"),
    ("ED",              "Carpet: Drift",        "bold"),
    ("F",               "ICA Spin-History",     "bold"),
    ("G",               "Hyperint. Slice",      "bold"),
    ("H",               "Vertical Strikes",     "bold"),
    ("I",               "Data Formatting",      "bold"),
    # ── MRIQC T1w ───────────────────────────────────────────────────────────
    ("J",               "Background T1w",       "t1w"),
    ("KA",              "Signal Dropout T1w",   "t1w"),
    ("L",               "Aliasing Ghost T1w",   "t1w"),
    ("M",               "FOV Wraparound T1w",   "t1w"),
    ("O",               "Motion/Gibbs Ring.",   "t1w"),
    ("P",               "Intensity Non-Unif.",  "t1w"),
    ("Q",               "Eye Spillover",        "t1w"),
    # ── fMRIPrep ────────────────────────────────────────────────────────────
    ("R",               "MNI Normalization",    "fmriprep"),
    ("S",               "Brain Mask",           "fmriprep"),
    ("T",               "Residual SDC",         "fmriprep"),
    ("U",               "Brain Segmentation",   "fmriprep"),
    ("V",               "Surface Reconstr.",    "fmriprep"),
    ("W",               "Coreg EPI-T1w",        "fmriprep"),
    ("X",               "CompCor Regions",      "fmriprep"),
    # ── Decision ────────────────────────────────────────────────────────────
    ("BOLD",            "Decision",             "decision"),
    ("T1w",             "Decision",             "decision"),
    ("fMRIPrep",        "Decision",             "decision"),
    ("FINAL",           "DECISION",             "decision"),
    # ── Notes ───────────────────────────────────────────────────────────────
    ("Notes",           "",                     "notes"),
]

# ── Colour palette ─────────────────────────────────────────────────────────
FILLS = {
    "fixed":    PatternFill("solid", fgColor="D9D9D9"),   # grey
    "bold":     PatternFill("solid", fgColor="BDD7EE"),   # blue
    "t1w":      PatternFill("solid", fgColor="C6EFCE"),   # green
    "fmriprep": PatternFill("solid", fgColor="FCE4D6"),   # orange/peach
    "decision": PatternFill("solid", fgColor="E2EFDA"),   # light green
    "notes":    PatternFill("solid", fgColor="FFF2CC"),   # yellow
}

GROUP_LABELS = {
    "bold":     "MRIQC BOLD",
    "t1w":      "MRIQC T1w",
    "fmriprep": "fMRIPrep",
    "decision": "Decision",
}

COND_FILLS = {
    "EXCLUDE": PatternFill("solid", fgColor="FF9999"),   # red
    "FLAG":    PatternFill("solid", fgColor="FFEB9C"),   # amber
    "PASS":    PatternFill("solid", fgColor="C6EFCE"),   # green
    "INCLUDE": PatternFill("solid", fgColor="C6EFCE"),
}

THIN = Side(style="thin", color="AAAAAA")
THICK = Side(style="medium", color="888888")

def thin_border():
    return Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

def thick_right():
    return Border(left=THIN, right=THICK, top=THIN, bottom=THIN)


def col_letter(idx):  # 1-based
    return get_column_letter(idx)


# ── Build workbook ─────────────────────────────────────────────────────────
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "QC Ratings"

# Freeze the first two columns and first three rows
ws.freeze_panes = "C4"

# ── Row 1: group category headers ─────────────────────────────────────────
# collect column index ranges per group
group_ranges = {}
for ci, (_, _, grp) in enumerate(COLS, start=1):
    if grp not in ("fixed", "notes"):
        group_ranges.setdefault(grp, [ci, ci])
        group_ranges[grp][1] = ci

# Write group label in first column of each group, then merge
for grp, (start, end) in group_ranges.items():
    ws.cell(row=1, column=start).value = GROUP_LABELS[grp]
    ws.cell(row=1, column=start).font = Font(bold=True, size=11, color="FFFFFF")
    ws.cell(row=1, column=start).alignment = Alignment(horizontal="center", vertical="center")
    fill_color = {
        "bold":     "2E75B6",
        "t1w":      "375623",
        "fmriprep": "C55A11",
        "decision": "538135",
    }[grp]
    for ci in range(start, end + 1):
        ws.cell(row=1, column=ci).fill = PatternFill("solid", fgColor=fill_color)
    if start != end:
        ws.merge_cells(
            start_row=1, start_column=start,
            end_row=1, end_column=end
        )

# Fixed header cells row 1
for label, col_idx in [("Subject Info", 1)]:
    ws.cell(row=1, column=col_idx).value = "Subject Info"
    ws.cell(row=1, column=col_idx).font = Font(bold=True, size=11, color="FFFFFF")
    ws.cell(row=1, column=col_idx).fill = PatternFill("solid", fgColor="595959")
    ws.cell(row=1, column=col_idx).alignment = Alignment(horizontal="center", vertical="center")
ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)

notes_ci = len(COLS)
ws.cell(row=1, column=notes_ci).value = "Notes"
ws.cell(row=1, column=notes_ci).font = Font(bold=True, size=11, color="FFFFFF")
ws.cell(row=1, column=notes_ci).fill = PatternFill("solid", fgColor="7F6000")
ws.cell(row=1, column=notes_ci).alignment = Alignment(horizontal="center", vertical="center")

ws.row_dimensions[1].height = 22

# ── Row 2: criterion code ──────────────────────────────────────────────────
for ci, (code, _, grp) in enumerate(COLS, start=1):
    cell = ws.cell(row=2, column=ci)
    cell.value = code
    cell.font = Font(bold=True, size=9)
    cell.fill = FILLS[grp]
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = thin_border()

ws.row_dimensions[2].height = 18

# ── Row 3: criterion description ──────────────────────────────────────────
for ci, (_, desc, grp) in enumerate(COLS, start=1):
    cell = ws.cell(row=3, column=ci)
    cell.value = desc
    cell.font = Font(italic=True, size=8, color="444444")
    cell.fill = FILLS[grp]
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = thin_border()

ws.row_dimensions[3].height = 30

# ── Column widths ─────────────────────────────────────────────────────────
for ci, (code, _, grp) in enumerate(COLS, start=1):
    cl = col_letter(ci)
    if grp == "fixed" and code == "SubjID":
        ws.column_dimensions[cl].width = 14
    elif grp == "fixed":
        ws.column_dimensions[cl].width = 10
    elif grp == "notes":
        ws.column_dimensions[cl].width = 40
    elif grp == "decision":
        ws.column_dimensions[cl].width = 11
    else:
        ws.column_dimensions[cl].width = 10

# ── Column grouping (Excel outline) ───────────────────────────────────────
# Assign outline levels so each group can be collapsed
outline_groups = {
    "bold":     1,
    "t1w":      1,
    "fmriprep": 1,
}
for ci, (_, _, grp) in enumerate(COLS, start=1):
    if grp in outline_groups:
        ws.column_dimensions[col_letter(ci)].outline_level = outline_groups[grp]

# ── Data validation dropdowns ─────────────────────────────────────────────
# Criterion columns: PASS / FLAG / EXCLUDE
crit_dv = DataValidation(
    type="list",
    formula1='"PASS,FLAG,EXCLUDE"',
    allow_blank=True,
    showDropDown=False,
    showErrorMessage=True,
    error="Enter PASS, FLAG, or EXCLUDE",
    errorTitle="Invalid entry",
)
ws.add_data_validation(crit_dv)

# Decision columns: INCLUDE / FLAG / EXCLUDE
dec_dv = DataValidation(
    type="list",
    formula1='"INCLUDE,FLAG,EXCLUDE"',
    allow_blank=True,
    showDropDown=False,
    showErrorMessage=True,
    error="Enter INCLUDE, FLAG, or EXCLUDE",
    errorTitle="Invalid entry",
)
ws.add_data_validation(dec_dv)

# Identify column index ranges
crit_cols = [ci for ci, (_, _, grp) in enumerate(COLS, start=1)
             if grp in ("bold", "t1w", "fmriprep")]
dec_cols  = [ci for ci, (_, _, grp) in enumerate(COLS, start=1)
             if grp == "decision"]

first_data_row = 4
last_data_row  = first_data_row + N_DATA_ROWS - 1

for ci in crit_cols:
    cl = col_letter(ci)
    crit_dv.add(f"{cl}{first_data_row}:{cl}{last_data_row}")

for ci in dec_cols:
    cl = col_letter(ci)
    dec_dv.add(f"{cl}{first_data_row}:{cl}{last_data_row}")

# ── Conditional formatting ────────────────────────────────────────────────
all_qc_cols = crit_cols + dec_cols

for ci in all_qc_cols:
    cl = col_letter(ci)
    rng = f"{cl}{first_data_row}:{cl}{last_data_row}"

    ws.conditional_formatting.add(
        rng,
        CellIsRule(
            operator="equal", formula=['"EXCLUDE"'],
            fill=COND_FILLS["EXCLUDE"],
            font=Font(bold=True, color="9C0006"),
        ),
    )
    ws.conditional_formatting.add(
        rng,
        CellIsRule(
            operator="equal", formula=['"FLAG"'],
            fill=COND_FILLS["FLAG"],
            font=Font(bold=False, color="9C6500"),
        ),
    )
    ws.conditional_formatting.add(
        rng,
        CellIsRule(
            operator="equal", formula=['"PASS"'],
            fill=COND_FILLS["PASS"],
            font=Font(color="276221"),
        ),
    )
    ws.conditional_formatting.add(
        rng,
        CellIsRule(
            operator="equal", formula=['"INCLUDE"'],
            fill=COND_FILLS["INCLUDE"],
            font=Font(bold=True, color="276221"),
        ),
    )

# ── Data rows: alternating row shading + borders ──────────────────────────
for row in range(first_data_row, last_data_row + 1):
    row_fill = PatternFill("solid", fgColor="F2F2F2") if row % 2 == 0 else None
    for ci, (_, _, grp) in enumerate(COLS, start=1):
        cell = ws.cell(row=row, column=ci)
        # light alternating background only for non-QC columns
        if grp in ("fixed", "notes") and row_fill:
            cell.fill = row_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
        # thick border on last column of each group
        is_last_in_group = (
            ci == len(COLS) or COLS[ci][2] != grp  # next col different group
        )
        cell.border = thick_right() if is_last_in_group else thin_border()

# ── Add a second sheet: Criterion Legend ──────────────────────────────────
legend = wb.create_sheet("Criterion Legend")

legend_data = [
    ("Code", "Stage",        "Name",                             "Hard Exclude?"),
    ("A",    "MRIQC BOLD",   "Background artifacts spilling onto brain",         "YES"),
    ("BA",   "MRIQC BOLD",   "BOLD signal dropout (SDC)",                        "YES"),
    ("BB",   "MRIQC BOLD",   "Brain distortions (SDC)",                          "YES if severe"),
    ("C",    "MRIQC BOLD",   "Aliasing ghost at brain intensity",                "YES"),
    ("D",    "MRIQC BOLD",   "FOV wrap-around overlapping cortex",               "YES"),
    ("EA",   "MRIQC BOLD",   "Carpet crown: motion peaks",                       "No — FLAG"),
    ("EB",   "MRIQC BOLD",   "Carpet crown: respiration",                        "No — FLAG"),
    ("EC",   "MRIQC BOLD",   "Carpet crown: coil failure",                       "YES"),
    ("ED",   "MRIQC BOLD",   "Carpet crown: drift of unknown source",            "YES"),
    ("F",    "MRIQC BOLD",   "ICA spin-history stripes",                         "No — FLAG"),
    ("G",    "MRIQC BOLD",   "Hyperintensity of single slices",                  "No — FLAG"),
    ("H",    "MRIQC BOLD",   "Vertical strikes in sagittal SD map",              "No — FLAG"),
    ("I",    "MRIQC BOLD",   "Data formatting issues (flipped axes, orientation)","YES"),
    ("J",    "MRIQC T1w",    "Background artifacts T1w",                         "No — FLAG"),
    ("KA",   "MRIQC T1w",    "Signal dropout T1w",                               "YES if severe"),
    ("L",    "MRIQC T1w",    "Aliasing ghost T1w",                               "No — FLAG"),
    ("M",    "MRIQC T1w",    "FOV wrap-around overlapping brain",                "YES"),
    ("O",    "MRIQC T1w",    "Motion-related and Gibbs ringing",                 "No — FLAG"),
    ("P",    "MRIQC T1w",    "Extreme intensity non-uniformity",                 "No — FLAG"),
    ("Q",    "MRIQC T1w",    "Eye spillover into frontal lobe",                  "No — FLAG"),
    ("R",    "fMRIPrep",     "Failure in MNI normalization",                     "YES"),
    ("S",    "fMRIPrep",     "Inaccurate brain mask (major holes/inclusions)",   "YES"),
    ("T",    "fMRIPrep",     "Residual susceptibility distortion",               "Conditional"),
    ("U",    "fMRIPrep",     "Brain tissue segmentation error",                  "No — FLAG"),
    ("V",    "fMRIPrep",     "Surface reconstruction problem",                   "No — FLAG"),
    ("W",    "fMRIPrep",     "EPI-T1w coregistration failure",                   "YES"),
    ("X",    "fMRIPrep",     "CompCor regions covering neural sources",          "Conditional"),
]

hdr_fill   = PatternFill("solid", fgColor="595959")
bold_fill  = PatternFill("solid", fgColor="BDD7EE")
t1w_fill   = PatternFill("solid", fgColor="C6EFCE")
prep_fill  = PatternFill("solid", fgColor="FCE4D6")
yes_fill   = PatternFill("solid", fgColor="FF9999")
flag_fill  = PatternFill("solid", fgColor="FFEB9C")
cond_fill  = PatternFill("solid", fgColor="FFC7CE")

for ri, row_data in enumerate(legend_data, start=1):
    for ci, val in enumerate(row_data, start=1):
        cell = legend.cell(row=ri, column=ci)
        cell.value = val
        cell.border = thin_border()
        cell.alignment = Alignment(vertical="center", wrap_text=(ci == 3))

        if ri == 1:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = hdr_fill
        else:
            stage = row_data[1]
            if stage == "MRIQC BOLD":
                cell.fill = bold_fill
            elif stage == "MRIQC T1w":
                cell.fill = t1w_fill
            else:
                cell.fill = prep_fill

            # colour the last column by exclude status
            if ci == 4:
                v = val
                if v == "YES":
                    cell.fill = yes_fill
                    cell.font = Font(bold=True, color="9C0006")
                elif "FLAG" in v:
                    cell.fill = flag_fill
                    cell.font = Font(color="9C6500")
                else:
                    cell.fill = cond_fill
                    cell.font = Font(color="843C0C")

legend.column_dimensions["A"].width = 8
legend.column_dimensions["B"].width = 14
legend.column_dimensions["C"].width = 50
legend.column_dimensions["D"].width = 18
legend.row_dimensions[1].height = 20
for ri in range(2, len(legend_data) + 1):
    legend.row_dimensions[ri].height = 16

# ── Save ──────────────────────────────────────────────────────────────────
wb.save(OUT_PATH)
print(f"Saved: {OUT_PATH}")
print(f"  QC Ratings sheet: {N_DATA_ROWS} participant rows, {len(COLS)} columns")
print(f"  Criterion Legend sheet: {len(legend_data)-1} criteria listed")
