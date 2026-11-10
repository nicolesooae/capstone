"""HALO: Compare finger PPG against annotated ECG from PhysioNet PTT dataset.

Source mapping: pleth_1/4 are infrared, pleth_2/5 red, pleth_3/6 green.
The source sensors are MAX30101, not MAX30102.

Install: pip install numpy pandas scipy matplotlib neurokit2 wfdb
Run (from PhysioNet_PPG folder): python validate_physionet.py
Uses s1_sit.dat/.hea/.atr from Mehrgardt et al., PhysioNet v1.1.0.
"""
from pathlib import Path
import argparse
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import wfdb
import neurokit2 as nk

# Use the same formulas as the main pipeline, even when launched in this folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from halo_metrics import interval_variability

COMPARISON_METRICS = ['hr_bpm', 'sdnn_ms', 'rmssd_ms', 'sdsd_ms', 'pnn20_pct', 'pnn50_pct']


def interval_features(peak_samples, fs):
    """Compute uncorrected pulse/RR metrics in milliseconds.

    These are only interpretable after assessing ectopic/artifact intervals.
    """
    intervals = np.diff(np.asarray(peak_samples, dtype=float)) * 1000.0 / fs
    if len(intervals) < 3 or not np.isfinite(intervals).all() or np.any(intervals <= 0):
        return {"beats": len(peak_samples), "mean_interval_ms": np.nan,
                **dict.fromkeys(COMPARISON_METRICS, np.nan)}
    variability = interval_variability(intervals)
    return {
        "beats": len(peak_samples),
        "hr_bpm": 60000.0 / np.mean(intervals),
        "mean_interval_ms": float(np.mean(intervals)),
        **{key.removeprefix('prv_'): value for key, value in variability.items()},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--record', default='s1_sit', help='WFDB record basename')
    p.add_argument('--channel', default='pleth_2',
                   help='PPG channel: pleth_1/4 infrared, pleth_2/5 red, pleth_3/6 green')
    p.add_argument('--out', default='physionet_validation_results')
    args = p.parse_args()
    target = Path(args.record)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    record = wfdb.rdrecord(str(target))
    annotations = wfdb.rdann(str(target), 'atr')
    fs = float(record.fs)
    if args.channel not in record.sig_name:
        raise ValueError(f'{args.channel} not found; choose from {record.sig_name}')
    ppg = np.array(record.p_signal[:, record.sig_name.index(args.channel)], dtype=float)
    invalid = ~np.isfinite(ppg)
    if invalid.all():
        raise ValueError('PPG contains no finite samples')
    if invalid.any():
        x = np.arange(len(ppg))
        ppg[invalid] = np.interp(x[invalid], x[~invalid], ppg[~invalid])
    ecg_peaks = np.asarray(annotations.sample, dtype=int)
    ecg_peaks = ecg_peaks[(ecg_peaks >= 0) & (ecg_peaks < len(ppg))]
    print(f'Recording: {args.record}, {len(ppg)/fs:.1f} s, fs={fs:g} Hz')
    print(f'PPG channel: {args.channel}; ECG annotations: {len(ecg_peaks)}')

    # Established NeuroKit Elgendi cleaning and detection. This does not independently
    # establish accurate PPG beats; visual review is still required.
    sig, info = nk.ppg_process(ppg, sampling_rate=int(fs), method='elgendi')
    ppg_peaks = np.asarray(info['PPG_Peaks'], dtype=int)
    print(f'PPG detected peaks: {len(ppg_peaks)}')

    # Compare separately derived metrics over matching, fixed 60-second windows.
    # Timing of ECG electrical R-peaks and optical pulse peaks is different,
    # so we compare INTERVAL-derived metrics, not absolute peak timestamps.
    window_s = 60
    rows = []
    for i in range(int(len(ppg)/fs // window_s)):
        left = int(i * window_s * fs)
        right = int((i+1) * window_s * fs)
        pp = ppg_peaks[(ppg_peaks >= left) & (ppg_peaks < right)]
        ep = ecg_peaks[(ecg_peaks >= left) & (ecg_peaks < right)]
        pfeat = interval_features(pp, fs)
        efeat = interval_features(ep, fs)
        row = {'window': i+1, 'start_s': i*window_s,
               'end_s': (i+1)*window_s, 'ppg_beats': len(pp),
               'ecg_beats': len(ep)}
        for label in COMPARISON_METRICS:
            row['ppg_'+label] = pfeat[label]
            row['ecg_'+label] = efeat[label]
            row['difference_'+label] = pfeat[label]-efeat[label]
            row['abs_error_'+label] = abs(row['difference_'+label])
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError('Recording shorter than 60 seconds')
    df.to_csv(out/'window_comparison.csv', index=False)

    print('\nMean absolute errors across 60-second windows:')
    for metric in COMPARISON_METRICS:
        print(f'  {metric}: {df["abs_error_"+metric].mean():.2f}')
    print('Note: SDNN/RMSSD here include all detected intervals without ectopic or artifact correction.')
    print('These are exploratory discrepancies, not clinically validated accuracy values.')

    # Provide detailed peak locations for review, not a forced one-to-one pairing.
    pd.DataFrame({'ppg_peak_sample': ppg_peaks, 'ppg_peak_time_s': ppg_peaks/fs}).to_csv(
        out/'ppg_detected_peaks.csv', index=False)
    pd.DataFrame({'ecg_peak_sample': ecg_peaks, 'ecg_peak_time_s': ecg_peaks/fs}).to_csv(
        out/'ecg_reference_peaks.csv', index=False)

    # First 15 seconds for manual visual review.
    stop = min(len(ppg), int(15*fs))
    tt = np.arange(stop)/fs
    fig, ax = plt.subplots(figsize=(13, 4))
    ax.plot(tt, sig['PPG_Clean'].to_numpy()[:stop], label='Clean PPG', lw=1)
    visible = ppg_peaks[ppg_peaks < stop]
    ax.scatter(visible/fs, sig['PPG_Clean'].to_numpy()[visible], s=22,
               color='red', label='Detected PPG peaks', zorder=3)
    ax.set(xlabel='Time (s)', ylabel='Filtered PPG (arbitrary units)',
           title=f'{args.record}: {args.channel} PPG peaks, first 15 seconds')
    ax.legend(); fig.tight_layout()
    fig.savefig(out/'ppg_peaks_first_15s.png', dpi=160)
    plt.close(fig)

    fig, axs = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    for ax, metric, label in zip(axs, ['hr_bpm','sdnn_ms','rmssd_ms'],
                                 ['Heart rate (bpm)','SDNN (ms)','RMSSD (ms)']):
        ax.plot(df['window'], df['ecg_'+metric], 'o-', label='ECG reference')
        ax.plot(df['window'], df['ppg_'+metric], 's--', label='PPG estimate')
        ax.set_ylabel(label); ax.grid(alpha=0.3); ax.legend()
    axs[-1].set_xlabel('60-second window number')
    fig.suptitle('HALO exploratory PPG vs ECG interval features')
    fig.tight_layout()
    fig.savefig(out/'ppg_vs_ecg_features.png', dpi=160)
    plt.close(fig)
    print(f'\nSaved output to: {out.resolve()}')

if __name__ == '__main__':
    main()
