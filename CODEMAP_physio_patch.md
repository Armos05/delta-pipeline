# CODEMAP — physio.py

Input : Siemens `.puls` (cardiac PPU) and `.resp` (respiratory) log files
        found under `<bids>/sub-XX/ses-XX/physio/`.
Output: RETROICOR TSV + JSON sidecar in `<bids>/sub-XX/ses-XX/func/`.
Orchestrator: `src/physio.py` → called via `src/cli.py physio`.

---

## Step-by-step flow

### 0. Entry point

```
cli.py main
  → cmd_physio(args)
  → physio.run_physio(args)
```

### 1. Subject / session / task discovery (`run_physio`)

- Subjects: `--subjects` CLI arg **or** all `sub-*` folders under BIDS root.
- Sessions: `--sessions` CLI arg **or** all `ses-*` folders per subject.
- Tasks: `--tasks` CLI arg (default: `sentencecompletion`).

### 2. Skip check

Output TSV already present **and** `--force` not set → `SKIP`.

### 3. Physio file location (`find_physio_files`)

Looks in `<bids>/<subject>/<session>/physio/` for:
- `*recording-cardiac*physio.puls`
- `*recording-respiratory*physio.resp`
Falls back to bare `*.puls` / `*.resp` if the BIDS recording entity is absent.

### 4. TR / n_vols resolution (`read_bold_metadata`)

Priority: CLI arg → BOLD JSON sidecar `RepetitionTime` → NIfTI header.

### 5. Log parsing — `parse_siemens_log` → `siemens_line2table`

**`siemens_line2table`** is a faithful Python translation of
`tapas_physio_siemens_line2table_SanneAdjusted.m` (Lars Kasper, TNU Zurich).

Step-by-step (mirrors the MATLAB exactly):

| # | MATLAB | Python |
|---|--------|--------|
| a | `regexpi(lineData, ' 6002 ')` — find recording-start | `re.search(r' 6002 ', line_data)` |
| b | `lineData = lineData((iTrigger(1)+6):end)` — strip header | `line_data = line_data[m_start.end():]` |
| c | `eraseBetween(lineData, '5002', '6002')` — remove idle blocks | `_erase_between()` using `re.sub` |
| d | `erase(lineData, '50026002')` — remove adjacent pair | `str.replace('50026002', '')` |
| e | `textscan(lineData, '%d', …)` — tokenise | `re.split` + `int()` cast |
| f | `find(data{1} == 5000/6000/5003)` — locate triggers | `np.where(data == …)` |
| g | `iNonEcgSignals = [cpulse; cpulse_off; recording_off]` | `np.concatenate([cpulse, cpulse_off, recording_off])` |
| h | `data_stream(iNonEcgSignals) = []` — remove triggers from signal | boolean mask, `data[mask]` |
| i | Trigger placement at `iDataStream == iNonEcgSignals(k)-1` | `np.where(i_data_table[:,0] == trigger_pos - 1)` |
| j | Returns `data_table(:,1)` as signal, `data_table(:,3)` as trigger codes | `data_table[:,0]`, `data_table[:,2]` |

**Note on trigger placement**: a 5000 marker immediately following a 6000 (no
intervening signal sample) cannot be placed in the table — identical silent
drop behaviour as in TAPAS. This never occurs in real Siemens logs, which
always have baseline PPU samples between beats.

`parse_siemens_log` joins multi-line file content into one string (MATLAB
`fileread` behaviour), then extracts `signal = data_table[:,0]` and
`trigger_idx = np.where(data_table[:,2] == 5000)`.

### 6. Cardiac phase (`estimate_cardiac_phase`)

- **Prefers hardware triggers** from `data_table[:,2]` (5000 codes) — exact
  R-peak times as detected by the Siemens PMU. Mirrors TAPAS priority.
- Falls back to `scipy.signal.find_peaks` on the normalised PPU signal when
  fewer than 2 hardware triggers are available.
- Linearly interpolates phase 0 → 2π between consecutive R-peaks.

### 7. Respiratory phase (`estimate_resp_phase`)

- Band-pass 0.1–0.5 Hz (Butterworth, order 3).
- Hilbert analytic signal → instantaneous phase shifted to [0, 2π].

### 8. RETROICOR regressors (`build_retroicor_regressors`)

Downsamples both phase time-series to fMRI volume times (one per TR,
`onset_slice = 1` as in TAPAS `sqpar`), then computes:

| Group | Orders | Columns |
|-------|--------|---------|
| Cardiac | 1–3 | 6 |
| Respiratory | 1–4 | 8 |
| Interaction | 1 | 2 |
| **Total** | | **18** |

### 9. Output (`write_outputs`)

- `*_desc-retroicor_regressors.tsv` — tab-separated, header row = column names.
- `*_desc-retroicor_regressors.json` — BIDS sidecar (TR, n_vols, sources, columns).

### 10. State update

`mark_physio_done(state, subject, session)` sets `physio_done=True` and
`physio_at=<ISO timestamp>` in `deltastate.json`.

---

## Key code locations

| What | File | Function |
|------|------|----------|
| CLI routing | `src/cli.py` | `cmd_physio` |
| Orchestration | `src/physio.py` | `run_physio` |
| **Siemens log parser** | `src/physio.py` | `siemens_line2table` ← port of `tapas_physio_siemens_line2table_SanneAdjusted.m` |
| File read + extract | `src/physio.py` | `parse_siemens_log` |
| Cardiac phase | `src/physio.py` | `estimate_cardiac_phase` |
| Respiratory phase | `src/physio.py` | `estimate_resp_phase` |
| RETROICOR build | `src/physio.py` | `build_retroicor_regressors` |
| File discovery | `src/physio.py` | `find_physio_files` |
| BOLD metadata | `src/physio.py` | `read_bold_metadata` |
| Write TSV + JSON | `src/physio.py` | `write_outputs` |
| State tracking | `src/state.py` | `mark_physio_done`, `needs_physio` |
