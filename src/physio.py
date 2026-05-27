#!/usr/bin/env python3
"""physio.py – Generate RETROICOR physiological regressors from Siemens .puls/.resp logs.

Replaces prep_compltask_physio_custom.m without requiring MATLAB or the TAPAS
toolbox.  The Siemens log parser is a faithful Python translation of the
actual tapas_physio_siemens_line2table_SanneAdjusted.m source file.

BIDS layout assumed
-------------------
<bids_root>/
  sub-<id>/ses-<id>/func/
    sub-<id>_ses-<id>_task-<task>_bold.nii.gz   ← used to read TR / n_vols
    sub-<id>_ses-<id>_task-<task>_bold.json
  sub-<id>/ses-<id>/physio/
    sub-<id>_ses-<id>_task-<task>_recording-cardiac_physio.puls
    sub-<id>_ses-<id>_task-<task>_recording-respiratory_physio.resp

Outputs (written to <bids_root>/sub-…/ses-…/func/)
----------------------------------------------------
  sub-<id>_ses-<id>_task-<task>_desc-retroicor_regressors.tsv
  sub-<id>_ses-<id>_task-<task>_desc-retroicor_regressors.json

The TSV has one row per fMRI volume and 18 columns:
  cardiac_sin1..3, cardiac_cos1..3      (3rd-order cardiac RETROICOR)
  resp_sin1..4,    resp_cos1..4         (4th-order respiratory RETROICOR)
  card_resp_sin1,  card_resp_cos1       (1st-order interaction)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks
from scipy.interpolate import interp1d


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
SIEMENS_SAMPLING_INTERVAL_S = 0.00118   # 1.18 ms (identical to .m script default)

# Trigger codes used by the Siemens PMU system
TRIGGER_CARDIAC_ON  = 5000  # R-peak / pulse onset
TRIGGER_CARDIAC_OFF = 6000  # R-peak off
TRIGGER_REC_OFF     = 5003  # recording end marker

# Siemens respiration belt artefact codes (from prep_compltask_physio_custom.m)
# These are hardware status codes that survive into the signal column after
# trigger removal and must be masked out before phase estimation.
RESP_BAD_VALS = {98304, 5003, 67108864, 163840, 33554432}
# Note: 6002 (recording start) and 5002 (scan start) are used only for
# header/footer stripping, not kept as trigger markers — matches the .m file.

# RETROICOR orders (identical to TAPAS defaults used in prep_compltask_physio_custom.m)
CARDIAC_ORDER  = 3
RESP_ORDER     = 4
INTERACT_ORDER = 1


# ──────────────────────────────────────────────────────────────────────────────
# Siemens log parser
# Faithful Python translation of:
#   tapas_physio_siemens_line2table_SanneAdjusted.m  (Lars Kasper, TNU Zurich)
# ──────────────────────────────────────────────────────────────────────────────

def _erase_between(text: str, start_marker: str, end_marker: str) -> str:
    """Remove all substrings that lie strictly between start_marker and end_marker.

    Mirrors MATLAB eraseBetween(lineData, '5002', '6002').
    The markers themselves are preserved in the output.
    """
    pattern = re.escape(start_marker) + r'.*?' + re.escape(end_marker)
    return re.sub(pattern, start_marker + end_marker, text, flags=re.DOTALL)


def siemens_line2table(line_data: str, cardiac_modality: str) -> np.ndarray:
    """Parse one data line from a Siemens physiological log file.

    Direct Python port of tapas_physio_siemens_line2table_SanneAdjusted.m.

    Parameters
    ----------
    line_data        : full text content of the .puls or .resp file
    cardiac_modality : 'PPU', 'RESP', or 'ECG'

    Returns
    -------
    data_table : ndarray, shape (nSamples, 3)
        Column 0 : physiological signal amplitude
        Column 1 : second channel (zeros for PPU/RESP, AVF for ECG)
        Column 2 : trigger code at each sample (0 = no trigger,
                   5000 = cardiac-on, 6000 = cardiac-off, 5003 = rec-off)
    """
    modality = cardiac_modality.upper()

    # ── 1. Locate recording-start marker ' 6002 ' and crop header ────────────
    # MATLAB: iTrigger = regexpi(lineData, ' 6002 ');
    #         lineData = lineData((iTrigger(1)+6):end);   % +6 skips ' 6002 '
    m_start = re.search(r' 6002 ', line_data)
    if m_start is None:
        raise ValueError("Recording-start marker ' 6002 ' not found in log file.")
    line_data = line_data[m_start.end():]   # everything after ' 6002 '

    # ── 2. Remove inter-scan idle blocks (between '5002' and '6002') ─────────
    # MATLAB: lineData = eraseBetween(lineData, '5002', '6002');
    #         lineData = erase(lineData, '50026002');
    line_data = _erase_between(line_data, '5002', '6002')
    line_data = line_data.replace('50026002', '')

    # ── 3. Tokenise: extract all integers ────────────────────────────────────
    # MATLAB: data = textscan(lineData, '%d', 'Delimiter',' ', 'MultipleDelimsAsOne',1)
    tokens = re.split(r'\s+', line_data.strip())
    values = []
    for t in tokens:
        if t == '':
            continue
        try:
            values.append(int(t))
        except ValueError:
            pass
    data = np.array(values, dtype=np.int64)

    # ── 4. Locate non-signal trigger positions ────────────────────────────────
    # MATLAB:
    #   cpulse       = find(data{1} == 5000);
    #   cpulse_off   = find(data{1} == 6000);
    #   recording_off= find(data{1} == 5003);
    #   iNonEcgSignals = [cpulse; cpulse_off; recording_off];
    cpulse        = np.where(data == TRIGGER_CARDIAC_ON)[0]
    cpulse_off    = np.where(data == TRIGGER_CARDIAC_OFF)[0]
    recording_off = np.where(data == TRIGGER_REC_OFF)[0]

    i_non_ecg = np.concatenate([cpulse, cpulse_off, recording_off])
    code_non_ecg = np.concatenate([
        np.full(len(cpulse),        TRIGGER_CARDIAC_ON,  dtype=np.int64),
        np.full(len(cpulse_off),    TRIGGER_CARDIAC_OFF, dtype=np.int64),
        np.full(len(recording_off), TRIGGER_REC_OFF,     dtype=np.int64),
    ])

    # ── 5. Build pure-signal stream by removing trigger markers ──────────────
    # MATLAB:
    #   data_stream = data{1}; data_stream(iNonEcgSignals) = [];
    #   iDataStream = 1:numel(data{1}); iDataStream(iNonEcgSignals) = [];
    mask = np.ones(len(data), dtype=bool)
    if len(i_non_ecg) > 0:
        mask[i_non_ecg] = False
    data_stream  = data[mask]
    i_data_stream = np.where(mask)[0]   # original indices of signal samples

    n_samples = len(data_stream)

    # ── 6. Build data_table and place trigger codes ───────────────────────────
    if modality == 'ECG':
        n_rows = int(np.ceil(n_samples / 2))
        data_table  = np.zeros((n_rows, 3), dtype=np.int64)
        i_data_table = np.zeros((n_rows, 3), dtype=np.int64)

        data_table[:, 0]  = data_stream[0::2]
        i_data_table[:, 0] = i_data_stream[0::2]

        if n_samples % 2 == 1:
            data_table[:n_rows-1, 1]  = data_stream[1::2]
            i_data_table[:n_rows-1, 1] = i_data_stream[1::2]
        else:
            data_table[:, 1]  = data_stream[1::2]
            i_data_table[:, 1] = i_data_stream[1::2]

        # Place trigger codes: find row where iData_table col-1 or col-0 == trigger_idx - 1
        for k in range(len(i_non_ecg)):
            trigger_pos = i_non_ecg[k]
            i_row = np.where(i_data_table[:, 1] == trigger_pos - 1)[0]
            if len(i_row) == 0:
                i_row = np.where(i_data_table[:, 0] == trigger_pos - 1)[0]
            if len(i_row) > 0:
                data_table[i_row[0], 2] = code_non_ecg[k]

    elif modality in ('RESP', 'PPU'):
        n_rows = n_samples
        data_table  = np.zeros((n_rows, 3), dtype=np.int64)
        i_data_table = np.zeros((n_rows, 3), dtype=np.int64)

        data_table[:, 0]  = data_stream
        i_data_table[:, 0] = i_data_stream

        # Place trigger codes: find row where iData_table col-0 == trigger_idx - 1
        for k in range(len(i_non_ecg)):
            trigger_pos = i_non_ecg[k]
            i_row = np.where(i_data_table[:, 0] == trigger_pos - 1)[0]
            if len(i_row) > 0:
                data_table[i_row[0], 2] = code_non_ecg[k]
    else:
        raise ValueError(f'Unknown cardiac/respiratory modality: {cardiac_modality}')

    return data_table


def parse_siemens_log(filepath: Path, modality: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse a Siemens .puls or .resp file via siemens_line2table.

    Handles multi-line files by joining lines (the MATLAB function receives
    the whole file as one string — some Siemens systems write one long line,
    others wrap).

    Parameters
    ----------
    filepath : Path
    modality : 'PPU' or 'RESP'

    Returns
    -------
    signal       : 1-D float array — physiological amplitude time series
    trigger_idx  : 1-D int array — sample indices where cardiac-on (5000) was detected
    """
    raw = filepath.read_text(errors='replace')
    # Join to single line (mirrors how MATLAB reads it)
    line_data = ' '.join(raw.splitlines())

    data_table = siemens_line2table(line_data, modality)

    signal      = data_table[:, 0].astype(float)
    # Trigger-on positions in the cleaned signal coordinate
    trigger_idx = np.where(data_table[:, 2] == TRIGGER_CARDIAC_ON)[0]

    return signal, trigger_idx


# ──────────────────────────────────────────────────────────────────────────────
# Phase estimation
# ──────────────────────────────────────────────────────────────────────────────

def estimate_cardiac_phase(ppu_signal: np.ndarray,
                            fs: float,
                            trigger_idx: np.ndarray | None = None) -> np.ndarray:
    """Estimate cardiac phase (0–2π) at every sample.

    Uses scanner-detected R-peak trigger positions when available (column 2 of
    data_table with code 5000), falling back to scipy peak detection.
    This mirrors TAPAS which preferentially uses the PPU trigger column.
    """
    n = len(ppu_signal)
    phase = np.zeros(n)

    # Prefer hardware triggers from data_table column 2
    if trigger_idx is not None and len(trigger_idx) >= 2:
        peaks = trigger_idx
    else:
        # Fallback: find peaks in normalised signal
        sig = ppu_signal - ppu_signal.min()
        if sig.max() > 0:
            sig = sig / sig.max()
        min_dist = max(1, int(0.4 * fs))
        peaks, _ = find_peaks(sig, height=0.3, distance=min_dist)

    if len(peaks) < 2:
        raise RuntimeError(
            'Fewer than 2 cardiac peaks detected – check the .puls file.'
        )

    for i in range(len(peaks) - 1):
        start, end = peaks[i], peaks[i + 1]
        phase[start:end] = np.linspace(0, 2 * np.pi, end - start, endpoint=False)

    # Extrapolate beyond last peak using final inter-peak interval
    if peaks[-1] < n - 1:
        last_interval = peaks[-1] - peaks[-2]
        remaining = n - peaks[-1]
        phase[peaks[-1]:] = np.linspace(
            0, 2 * np.pi * remaining / last_interval, remaining, endpoint=False
        )

    return phase


def clean_resp_signal(signal: np.ndarray) -> np.ndarray:
    """Remove Siemens belt artefact codes and interpolate gaps.

    The original MATLAB (prep_compltask_physio_custom.m) removes rows where
    data_table_resp(:,1) matches any of RESP_BAD_VALS.  Removing rows shortens
    the array, which is acceptable in MATLAB where TAPAS resamples the signal
    internally.  In our Python pipeline we need a uniform 847 Hz grid for the
    Hilbert transform, so instead we:
      1. Flag all artefact samples with NaN.
      2. Linear-interpolate across flagged gaps (typically 1-3 samples).
      3. Clip any remaining extreme values to ±5σ as a safety net.
    """
    sig = signal.astype(float)
    mask = np.isin(sig, list(RESP_BAD_VALS))
    if mask.any():
        idx = np.arange(len(sig))
        sig[mask] = np.interp(idx[mask], idx[~mask], sig[~mask])

    # Clip outliers beyond ±5σ (e.g. unrecognised artefact codes)
    mu, sd = sig.mean(), sig.std()
    if sd > 0:
        sig = np.clip(sig, mu - 5 * sd, mu + 5 * sd)

    return sig


def estimate_resp_phase(resp_signal: np.ndarray) -> np.ndarray:
    """Estimate respiratory phase via Hilbert analytic signal (Glover 2000).

    Uses second-order sections (SOS) form for the Butterworth bandpass filter.
    The standard ba-form butter() is numerically unstable at the very low
    normalised cut-off frequencies produced by the 847 Hz Siemens sampling
    rate (normalised 0.1 Hz ≈ 0.000236, 0.5 Hz ≈ 0.00118), causing filtfilt
    to return all-NaN output.  scipy recommends output='sos' + sosfiltfilt
    for exactly this scenario.
    """
    from scipy.signal import butter, sosfiltfilt, hilbert

    fs_approx = 1.0 / SIEMENS_SAMPLING_INTERVAL_S   # ≈ 847 Hz
    nyq = fs_approx / 2.0

    # SOS form: numerically stable even for very low normalised cut-offs
    sos = butter(3, [0.1 / nyq, 0.5 / nyq], btype='band', output='sos')
    filtered = sosfiltfilt(sos, resp_signal)

    # Safety check: fall back to raw signal if filtering still failed
    if not np.all(np.isfinite(filtered)):
        raise RuntimeError(
            'Respiratory bandpass filter produced non-finite values. '
            'Check that the .resp file contains valid signal data.'
        )

    analytic = hilbert(filtered)
    phase = np.angle(analytic) + np.pi   # shift from [-π, π] → [0, 2π]
    return phase


# ──────────────────────────────────────────────────────────────────────────────
# RETROICOR regressor construction  (Glover et al., 2000)
# ──────────────────────────────────────────────────────────────────────────────

def _downsample_phase_to_volumes(phase: np.ndarray,
                                  fs: float,
                                  tr: float,
                                  n_vols: int) -> np.ndarray:
    """Sample physiological phase at each fMRI volume acquisition time.

    Uses onset_slice = 1 (nominal, like TAPAS sqpar.onset_slice = 1):
    volume k is sampled at t = k * TR.
    """
    n_physio  = len(phase)
    t_physio  = np.arange(n_physio) / fs
    t_volumes = np.clip(np.arange(n_vols) * tr, t_physio[0], t_physio[-1])
    interp    = interp1d(t_physio, phase, kind='linear', fill_value='extrapolate')
    return interp(t_volumes)


def build_retroicor_regressors(cardiac_phase: np.ndarray,
                                resp_phase:    np.ndarray,
                                fs:       float,
                                tr:       float,
                                n_vols:   int,
                                cardiac_order:  int = CARDIAC_ORDER,
                                resp_order:     int = RESP_ORDER,
                                interact_order: int = INTERACT_ORDER,
                                ) -> tuple[np.ndarray, list[str]]:
    """Build RETROICOR design matrix (18 columns by default).

    Returns
    -------
    X       : (n_vols, n_regressors) float array
    columns : list of column name strings
    """
    phi_c = _downsample_phase_to_volumes(cardiac_phase, fs, tr, n_vols)
    phi_r = _downsample_phase_to_volumes(resp_phase,    fs, tr, n_vols)

    regressors, names = [], []

    for m in range(1, cardiac_order + 1):
        regressors += [np.sin(m * phi_c), np.cos(m * phi_c)]
        names      += [f'cardiac_sin{m}', f'cardiac_cos{m}']

    for n in range(1, resp_order + 1):
        regressors += [np.sin(n * phi_r), np.cos(n * phi_r)]
        names      += [f'resp_sin{n}', f'resp_cos{n}']

    for k in range(1, interact_order + 1):
        regressors += [np.sin(phi_c + phi_r), np.cos(phi_c + phi_r)]
        names      += [f'card_resp_sin{k}', f'card_resp_cos{k}']

    return np.column_stack(regressors), names   # (n_vols, 18)


# ──────────────────────────────────────────────────────────────────────────────
# BIDS helpers
# ──────────────────────────────────────────────────────────────────────────────

def find_physio_files(bids_root: Path,
                      subject: str, session: str, task: str,
                      ) -> tuple[Path | None, Path | None]:
    """Locate Siemens .puls / .resp files under <subject>/<session>/physio/."""
    physio_ses = bids_root / subject / session / 'physio'
    prefix = f'{subject}_{session}_task-{task}'

    puls = next(physio_ses.glob(f'{prefix}*cardiac*physio.puls'), None)
    resp = next(physio_ses.glob(f'{prefix}*respiratory*physio.resp'), None)

    # Fallback: bare *.puls / *.resp without BIDS recording entity
    if puls is None:
        puls = next(physio_ses.glob(f'{prefix}*.puls'), None)
    if resp is None:
        resp = next(physio_ses.glob(f'{prefix}*.resp'), None)

    return puls, resp


def read_bold_metadata(bids_root: Path,
                       subject: str, session: str, task: str,
                       ) -> tuple[float | None, int | None]:
    """Read TR and number of volumes from BOLD JSON sidecar and/or NIfTI header."""
    func_dir = bids_root / subject / session / 'func'
    tr, n_vols = None, None

    bold_json = next(func_dir.glob(f'{subject}_{session}_task-{task}_bold.json'), None)
    if bold_json and bold_json.exists():
        meta = json.loads(bold_json.read_text())
        tr = meta.get('RepetitionTime')

    bold_nii = next(func_dir.glob(f'{subject}_{session}_task-{task}_bold.nii*'), None)
    if bold_nii:
        try:
            import nibabel as nib
            img = nib.load(str(bold_nii))
            if img.ndim == 4:
                n_vols = img.shape[3]
        except Exception:
            pass

    return tr, n_vols


def write_outputs(regressors: np.ndarray,
                  col_names:  list[str],
                  bids_root:  Path,
                  subject:    str,
                  session:    str,
                  task:       str,
                  tr:         float,
                  n_vols:     int) -> Path:
    """Write regressor TSV + BIDS JSON sidecar to func/ directory."""
    func_dir = bids_root / subject / session / 'func'
    func_dir.mkdir(parents=True, exist_ok=True)

    prefix   = f'{subject}_{session}_task-{task}_desc-retroicor_regressors'
    tsv_path  = func_dir / f'{prefix}.tsv'
    json_path = func_dir / f'{prefix}.json'

    np.savetxt(str(tsv_path), regressors,
               delimiter='\t', header='\t'.join(col_names),
               comments='', fmt='%.8f')

    sidecar = {
        'Description': 'RETROICOR physiological noise regressors',
        'Sources': [
            f'{subject}/{session}/physio/'
            f'{subject}_{session}_task-{task}_recording-cardiac_physio.puls',
            f'{subject}_{session}/physio/'
            f'{subject}_{session}_task-{task}_recording-respiratory_physio.resp',
        ],
        'SamplingFrequency': round(1.0 / SIEMENS_SAMPLING_INTERVAL_S),
        'RepetitionTime':    tr,
        'NumberOfVolumes':   n_vols,
        'CardiacOrder':      CARDIAC_ORDER,
        'RespOrder':         RESP_ORDER,
        'InteractionOrder':  INTERACT_ORDER,
        'Columns':           col_names,
    }
    json_path.write_text(json.dumps(sidecar, indent=2))

    return tsv_path


# ──────────────────────────────────────────────────────────────────────────────
# Core per-run processing
# ──────────────────────────────────────────────────────────────────────────────

def process_run(bids_root: Path,
                subject:   str,
                session:   str,
                task:      str,
                tr:        float | None = None,
                n_vols:    int   | None = None) -> Path:
    """Process one fMRI run.  Returns path to the output TSV."""

    print(f'  [{subject}/{session}/task-{task}] Locating physio files…')
    puls_file, resp_file = find_physio_files(bids_root, subject, session, task)

    if puls_file is None or resp_file is None:
        physio_dir = bids_root / subject / session / 'physio'
        raise FileNotFoundError(
            f'Physio files not found for {subject}/{session}/task-{task}.\n'
            f'Expected in: {physio_dir}\n'
            f'  *recording-cardiac*physio.puls\n'
            f'  *recording-respiratory*physio.resp'
        )

    bids_tr, bids_n_vols = read_bold_metadata(bids_root, subject, session, task)
    tr     = tr     or bids_tr
    n_vols = n_vols or bids_n_vols

    if tr is None:
        raise ValueError(
            f'TR not found for {subject}/{session}/task-{task}. '
            'Provide --tr or ensure the BOLD JSON sidecar contains RepetitionTime.'
        )
    if n_vols is None:
        raise ValueError(
            f'Number of volumes not found for {subject}/{session}/task-{task}. '
            'Provide --n-vols or ensure the BOLD NIfTI file is present.'
        )

    fs = 1.0 / SIEMENS_SAMPLING_INTERVAL_S   # ≈ 847 Hz

    print(f'  [{subject}/{session}/task-{task}] Parsing cardiac log: {puls_file.name}')
    ppu_signal, ppu_triggers = parse_siemens_log(puls_file, 'PPU')

    print(f'  [{subject}/{session}/task-{task}] Parsing respiratory log: {resp_file.name}')
    resp_signal, _ = parse_siemens_log(resp_file, 'RESP')

    print(f'  [{subject}/{session}/task-{task}] Estimating cardiac phase '
          f'({len(ppu_triggers)} scanner triggers detected)…')
    cardiac_phase = estimate_cardiac_phase(ppu_signal, fs,
                                           trigger_idx=ppu_triggers if len(ppu_triggers) >= 2 else None)

    print(f'  [{subject}/{session}/task-{task}] Estimating respiratory phase…')
    resp_phase = estimate_resp_phase(clean_resp_signal(resp_signal))

    print(f'  [{subject}/{session}/task-{task}] Building RETROICOR regressors '
          f'(TR={tr}s, n_vols={n_vols})…')
    X, col_names = build_retroicor_regressors(cardiac_phase, resp_phase,
                                               fs, tr, n_vols)

    out_tsv = write_outputs(X, col_names, bids_root, subject, session, task,
                             tr, n_vols)
    print(f'  [{subject}/{session}/task-{task}] ✓  {out_tsv}')
    return out_tsv


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline entry-point (called from cli.py)
# ──────────────────────────────────────────────────────────────────────────────

def run_physio(args) -> None:
    """Orchestration function called by cli.py cmd_physio(args)."""
    from state import load_state, save_state, mark_physio_done

    bids_root  = Path(args.bids).resolve()
    state_file = Path(args.state)
    state      = load_state(state_file)

    subjects = (args.subjects or
                sorted(d.name for d in bids_root.iterdir()
                       if d.is_dir() and d.name.startswith('sub-')))

    tasks   = args.tasks or ['sentencecompletion']
    force   = args.force
    dry_run = args.dry_run

    done_count = skipped_count = 0
    failed = []

    for subject in subjects:
        sub_dir = bids_root / subject
        if not sub_dir.exists():
            print(f'WARN  {subject} not found in {bids_root}')
            continue

        sessions = (args.sessions or
                    sorted(d.name for d in sub_dir.iterdir()
                           if d.is_dir() and d.name.startswith('ses-'))
                    or ['ses-01'])

        for session in sessions:
            for task in tasks:
                run_key = f'{subject}/{session}/task-{task}'

                func_dir = bids_root / subject / session / 'func'
                out_tsv  = (func_dir /
                            f'{subject}_{session}_task-{task}_desc-retroicor_regressors.tsv')

                if not force and out_tsv.exists():
                    print(f'SKIP  {run_key} (already exists – use --force to redo)')
                    skipped_count += 1
                    continue

                if dry_run:
                    print(f'DRY   {run_key} → {out_tsv}')
                    continue

                print(f'START {run_key}')
                try:
                    process_run(bids_root, subject, session, task,
                                tr=args.tr, n_vols=args.n_vols)
                    mark_physio_done(state, subject, session)
                    save_state(state, state_file)
                    done_count += 1
                except Exception as exc:
                    print(f'FAIL  {run_key}: {exc}')
                    failed.append(run_key)

    print(f'\n{done_count} done, {skipped_count} skipped, {len(failed)} failed')
    if failed:
        print('Failed:', ', '.join(failed))
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Standalone CLI
# ──────────────────────────────────────────────────────────────────────────────

def _build_standalone_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Generate RETROICOR physiological regressors from Siemens logs.'
    )
    p.add_argument('--bids',    required=True,  help='BIDS root directory')
    p.add_argument('--subject', required=True,  help='Subject ID (e.g. sub-01)')
    p.add_argument('--session', default='ses-01', help='Session ID')
    p.add_argument('--task',    required=True,  help='Task label (e.g. sentencecompletion)')
    p.add_argument('--tr',      type=float, default=None)
    p.add_argument('--n-vols',  type=int,   default=None, dest='n_vols')
    return p


if __name__ == '__main__':
    parser = _build_standalone_parser()
    args   = parser.parse_args()
    process_run(
        bids_root=Path(args.bids).resolve(),
        subject=args.subject,
        session=args.session,
        task=args.task,
        tr=args.tr,
        n_vols=args.n_vols,
    )
