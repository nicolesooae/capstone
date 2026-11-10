"""Reproducible full-dataset PPG/ECG comparison; downloads handled separately.

Uses the official RECORDS and SHA256SUMS manifests and locally cached WFDB files.
Reports unfiltered comparisons and the subset passing the main pipeline's PRV
checks separately. Never interpolates missing samples or removes bad intervals.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import warnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import neurokit2 as nk
import numpy as np
import pandas as pd
import scipy
import wfdb
from wfdb.io.annotation import is_qrs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from halo_ppg import variability_report
from validate_physionet import COMPARISON_METRICS, interval_features

SOURCE = 'https://physionet.org/files/pulse-transit-time-ppg/1.1.0/'


def read_manifest(data):
    records = (data / 'RECORDS').read_text().split()
    if len(records) != len(set(records)) or not records:
        raise ValueError('Empty or duplicate record manifest')
    for record in records:
        parts = record.split('_')
        if (len(parts) != 2 or not parts[0].startswith('s') or
                not parts[0][1:].isdigit() or parts[1] not in ('sit', 'walk', 'run')):
            raise ValueError(f'Unexpected record name: {record}')
    checksums = {}
    for line in (data / 'SHA256SUMS.txt').read_text().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            checksums[name.lstrip('*').removeprefix('./')] = digest
    return records, checksums


def verify_record(data, record, checksums):
    for extension in ('hea', 'dat', 'atr'):
        name = f'{record}.{extension}'
        if name not in checksums:
            raise ValueError(f'Missing published checksum: {name}')
        with (data / name).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != checksums[name]:
            raise ValueError(f'Checksum mismatch: {name}')


def process_finite_segments(raw, fs):
    """Process finite runs independently so missing samples never bridge beats."""
    finite = np.isfinite(raw)
    edges = np.flatnonzero(np.diff(np.r_[False, finite, False]))
    cleaned = np.full(len(raw), np.nan)
    quality = np.full(len(raw), np.nan)
    process_ok = np.zeros(len(raw), dtype=bool)
    peaks, messages = [], []
    for start, stop in zip(edges[::2], edges[1::2]):
        if stop - start < 10 * fs:
            messages.append(f'segment {start}:{stop} shorter than 10 s')
            continue
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                signals, info = nk.ppg_process(raw[start:stop], sampling_rate=fs,
                    method='elgendi', method_quality='templatematch')
            messages.extend(str(item.message) for item in caught)
            cleaned[start:stop] = signals.PPG_Clean.to_numpy()
            quality[start:stop] = signals.PPG_Quality.to_numpy()
            peaks.extend(np.asarray(info['PPG_Peaks'], dtype=int) + start)
            process_ok[start:stop] = True
        except Exception as exc:
            messages.append(f'segment {start}:{stop} failed: {type(exc).__name__}: {exc}')
    return np.asarray(peaks, dtype=int), cleaned, quality, process_ok, sorted(set(messages))


def compare_record(data, name, channel, min_quality):
    record = wfdb.rdrecord(str(data / name), channel_names=[channel])
    ann = wfdb.rdann(str(data / name), 'atr', return_label_elements=['symbol', 'label_store'])
    fs = int(record.fs)
    if fs != record.fs:
        raise ValueError('Noninteger sampling rate is not supported')
    raw = record.p_signal[:, 0]
    ppg_peaks, cleaned, quality, process_ok, messages = process_finite_segments(raw, fs)
    qrs = np.asarray([is_qrs[code] for code in ann.label_store], dtype=bool)
    ecg_peaks = ann.sample[qrs]
    ecg_symbols = np.asarray(ann.symbol)[qrs]
    in_range = (ecg_peaks >= 0) & (ecg_peaks < len(raw))
    ecg_peaks, ecg_symbols = ecg_peaks[in_range], ecg_symbols[in_range]
    # The pipeline rejects nonfinite input. Placeholders permit its window-level
    # checks to run, but every affected comparison window is explicitly rejected.
    gates = variability_report(fs, 60, ppg_peaks, quality,
        np.where(np.isfinite(raw), raw, 0), cleaned, min_quality, 'processed')
    rows = []
    for index in range(len(raw) // (60 * fs)):
        left, right = index * 60 * fs, (index + 1) * 60 * fs
        pp = ppg_peaks[(ppg_peaks >= left) & (ppg_peaks < right)]
        mask = (ecg_peaks >= left) & (ecg_peaks < right)
        ep = ecg_peaks[mask]
        pfeat, efeat = interval_features(pp, fs), interval_features(ep, fs)
        complete = bool(process_ok[left:right].all())
        # Reject windows within one second of missing data/independent processing
        # boundaries; no filtering across gaps or artificial adjacent intervals.
        guard_left, guard_right = max(0, left - fs), min(len(raw), right + fs)
        gap_free = bool(process_ok[guard_left:guard_right].all())
        comparable = complete and gap_free and all(
            np.isfinite(f[m]) for f in (pfeat, efeat) for m in COMPARISON_METRICS)
        reference_ok = bool(len(ep) >= 5 and np.all(ecg_symbols[mask] == 'N'))
        if len(ep) >= 2:
            reference_ok = reference_ok and (ep[0] - left) / fs <= 1.5 and (right - 1 - ep[-1]) / fs <= 1.5
        gate = gates.iloc[index]
        accepted = bool(comparable and reference_ok and gate.accepted)
        if not complete or not gap_free:
            reason = 'missing/unprocessed PPG samples or processing-gap boundary'
        elif not comparable:
            reason = 'insufficient finite PPG/ECG intervals'
        elif not reference_ok:
            reason = 'ECG reference contains non-normal beats or insufficient coverage'
        else:
            reason = gate.reason
        row = dict(record=name, subject=name.split('_')[0], activity=name.split('_')[1],
            channel=channel, fs_hz=fs, window=index + 1, start_s=60 * index,
            end_s=60 * (index + 1), ppg_beats=len(pp), ecg_beats=len(ep),
            comparable=comparable, accepted=accepted, reason=reason,
            quality_median=gate.quality_median, poor_quality_fraction=gate.poor_quality_fraction)
        for metric in COMPARISON_METRICS:
            row['ppg_' + metric] = pfeat[metric] if complete and gap_free else np.nan
            row['ecg_' + metric] = efeat[metric]
            difference = row['ppg_' + metric] - row['ecg_' + metric]
            row['difference_' + metric] = difference
            row['abs_error_' + metric] = abs(difference)
        rows.append(row)
    frame = pd.DataFrame(rows)
    status = dict(record=name, subject=name.split('_')[0], activity=name.split('_')[1],
        status='processed' if process_ok.all() else 'partially_processed',
        fs_hz=fs, duration_s=len(raw) / fs, missing_ppg_samples=int((~np.isfinite(raw)).sum()),
        ppg_peaks=len(ppg_peaks), ecg_peaks=len(ecg_peaks),
        ecg_annotation_symbols=json.dumps(dict(Counter(ann.symbol))),
        total_windows=len(frame), comparable_windows=int(frame.comparable.sum()) if len(frame) else 0,
        accepted_windows=int(frame.accepted.sum()) if len(frame) else 0,
        trailing_seconds=len(raw) / fs % 60, warnings=' | '.join(messages), error='')
    peaks = pd.DataFrame({'ppg_peak_sample': ppg_peaks, 'ppg_peak_time_s': ppg_peaks / fs})
    reference = pd.DataFrame({'ecg_peak_sample': ecg_peaks, 'ecg_peak_time_s': ecg_peaks / fs,
                              'symbol': ecg_symbols})
    return frame, status, peaks, reference


def agreement_summary(windows):
    rows = []
    for activity in ('all', 'sit', 'walk', 'run'):
        group = windows if activity == 'all' else windows[windows.activity == activity]
        for subset, mask in (('unfiltered', group.comparable), ('accepted', group.accepted)):
            selected = group.loc[mask]
            for metric in COMPARISON_METRICS:
                difference = selected['difference_' + metric].dropna()
                if len(difference):
                    bias = float(difference.mean())
                    sd = float(difference.std(ddof=1))
                    per_record = selected.groupby('record')['abs_error_' + metric].mean()
                    row = dict(mae=float(difference.abs().mean()),
                        median_abs_error=float(difference.abs().median()), bias=bias,
                        rmse=float(np.sqrt(np.mean(difference ** 2))),
                        descriptive_loa_low=bias - 1.96 * sd, descriptive_loa_high=bias + 1.96 * sd,
                        mean_record_mae=float(per_record.mean()))
                else:
                    row = dict.fromkeys(('mae', 'median_abs_error', 'bias', 'rmse',
                        'descriptive_loa_low', 'descriptive_loa_high', 'mean_record_mae'), np.nan)
                rows.append(dict(activity=activity, subset=subset, metric=metric,
                    windows=len(difference), records=selected.record.nunique(),
                    subjects=selected.subject.nunique(), **row))
    return pd.DataFrame(rows)


def make_plots(windows, summary, out):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, metric, label in zip(axes, ('hr_bpm', 'sdnn_ms', 'rmssd_ms'),
                                ('Heart rate (bpm)', 'SDNN (ms)', 'RMSSD (ms)')):
        for subset, offset, color in (('unfiltered', -.18, '#d97706'), ('accepted', .18, '#2563eb')):
            data = summary[(summary.metric == metric) & (summary.subset == subset)].set_index('activity')
            heights = [data.loc[a, 'mae'] for a in ('sit', 'walk', 'run')]
            ax.bar(np.arange(3) + offset, heights, .36, label=subset, color=color)
        ax.set_xticks(range(3), ['Sitting', 'Walking', 'Running'])
        ax.set_ylabel('Mean absolute difference: ' + label)
        ax.legend(fontsize=8)
    fig.suptitle('PPG PRV versus ECG — full PhysioNet PTT dataset, 60-second windows')
    fig.tight_layout()
    fig.savefig(out / 'agreement_by_activity.png', dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, metric, label in zip(axes, ('sdnn_ms', 'rmssd_ms'), ('SDNN (ms)', 'RMSSD (ms)')):
        for activity, color in (('sit', '#2563eb'), ('walk', '#d97706'), ('run', '#dc2626')):
            data = windows[windows.accepted & (windows.activity == activity)]
            mean = (data['ppg_' + metric] + data['ecg_' + metric]) / 2
            ax.scatter(mean, data['difference_' + metric], s=14, alpha=.6, label=activity, c=color)
        stats = summary[(summary.activity == 'all') & (summary.subset == 'accepted') & (summary.metric == metric)].iloc[0]
        for field, style in (('bias', '-'), ('descriptive_loa_low', '--'), ('descriptive_loa_high', '--')):
            if np.isfinite(stats[field]):
                ax.axhline(stats[field], color='black', ls=style, lw=1)
        ax.set(xlabel='Mean of PPG and ECG ' + label, ylabel='PPG minus ECG ' + label)
        ax.legend()
    fig.suptitle('Accepted windows: descriptive agreement (repeated windows per subject)')
    fig.tight_layout()
    fig.savefig(out / 'accepted_agreement.png', dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parent / 'data')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent / 'all_recordings_results')
    parser.add_argument('--channel', default='pleth_2', choices=[f'pleth_{i}' for i in range(1, 7)])
    parser.add_argument('--min-quality', type=float, default=.8)
    args = parser.parse_args()
    if not 0 <= args.min_quality <= 1:
        parser.error('min-quality must be between 0 and 1')
    records, checksums = read_manifest(args.data)
    args.out.mkdir(parents=True, exist_ok=True)
    frames, statuses = [], []
    for index, name in enumerate(records, 1):
        print(f'[{index}/{len(records)}] {name}', flush=True)
        try:
            verify_record(args.data, name, checksums)
            frame, status, peaks, reference = compare_record(args.data, name, args.channel, args.min_quality)
            frames.append(frame)
            record_out = args.out / name
            record_out.mkdir(exist_ok=True)
            frame.to_csv(record_out / 'window_comparison.csv', index=False)
            peaks.to_csv(record_out / 'ppg_detected_peaks.csv', index=False)
            reference.to_csv(record_out / 'ecg_reference_peaks.csv', index=False)
            print(f"  {status['comparable_windows']}/{status['total_windows']} comparable; "
                  f"{status['accepted_windows']} accepted", flush=True)
        except Exception as exc:
            status = dict(record=name, subject=name.split('_')[0], activity=name.split('_')[1],
                status='failed', error=f'{type(exc).__name__}: {exc}', total_windows=0,
                comparable_windows=0, accepted_windows=0)
            print(f"  FAILED: {status['error']}", flush=True)
        statuses.append(status)
        pd.DataFrame(statuses).to_csv(args.out / 'record_status.csv', index=False)
    if not frames or not any(len(frame) for frame in frames):
        raise RuntimeError('No comparison windows; see record_status.csv')
    windows = pd.concat(frames, ignore_index=True)
    windows.to_csv(args.out / 'all_window_comparisons.csv', index=False)
    summary = agreement_summary(windows)
    summary.to_csv(args.out / 'agreement_summary.csv', index=False)
    coverage = pd.DataFrame(statuses).groupby('activity').agg(
        records=('record', 'count'), windows=('total_windows', 'sum'),
        comparable=('comparable_windows', 'sum'), accepted=('accepted_windows', 'sum'))
    coverage['accepted_fraction'] = coverage.accepted / coverage.windows
    coverage.to_csv(args.out / 'coverage_by_activity.csv')
    windows.loc[~windows.accepted].groupby(['activity', 'reason']).size().rename('windows').to_csv(
        args.out / 'rejection_reasons.csv')
    make_plots(windows, summary, args.out)
    metadata = dict(source=SOURCE, channel=args.channel, channel_wavelength={
        'pleth_1': 'infrared', 'pleth_2': 'red', 'pleth_3': 'green',
        'pleth_4': 'infrared', 'pleth_5': 'red', 'pleth_6': 'green'}[args.channel],
        sensor='MAX30101', records_expected=len(records), records_failed=sum(s['status'] == 'failed' for s in statuses),
        neurokit2=nk.__version__, numpy=np.__version__, scipy=scipy.__version__, wfdb=wfdb.__version__,
        ppg_method='elgendi', quality_method='templatematch', window_s=60,
        min_quality=args.min_quality, signal_format='processed',
        missing_data='process finite segments independently; exclude missing/unprocessed windows plus 1 s guard',
        interval_correction='none; no deletion/interpolation of intervals',
        acceptance='main variability_report gates plus normal ECG annotations and reference edge coverage',
        unfiltered='all comparable windows, including windows rejected by the quality gates',
        statistics='differences = PPG minus ECG; pNN errors in percentage points; other units in metric names',
        weighting='mae weights windows equally; mean_record_mae weights record means equally',
        limits_of_agreement='descriptive mean +/- 1.96 sample SD; repeated windows are not independent; no confidence intervals',
        checksum_verification='every hea/dat/atr checked against official SHA256SUMS.txt')
    (args.out / 'analysis_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('\nCoverage:\n' + coverage.to_string(), flush=True)
    print('\nAgreement:\n' + summary[summary.metric.isin(['sdnn_ms', 'rmssd_ms'])].to_string(index=False), flush=True)
    print(f'\nResults: {args.out.resolve()}', flush=True)


if __name__ == '__main__':
    main()
