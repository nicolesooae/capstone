#!/usr/bin/env python3
"""HALO MAX30102 offline PPG prototype. Research/educational use, not diagnostic."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from halo_metrics import VARIABILITY_FIELDS, interval_variability, hemodynamics_from_args


def demo_data(fs, duration, seed=42):
    """Artificial waveform for software smoke-testing only, NOT a validation dataset."""
    rng = np.random.default_rng(seed)
    n = round(duration * fs)
    t = np.arange(n) / fs
    # synthetic systolic pulse train with mild sinusoidal beat timing variation
    pulse_times = []
    beat = 0.5
    while beat < duration:
        pulse_times.append(beat)
        beat += 60 / (72 + 3 * np.sin(2 * np.pi * 0.07 * beat))
    pulse = np.zeros(n)
    for pt in pulse_times:
        ix = int(round(pt * fs))
        if ix < n:
            pulse[ix] = 1
    from scipy.signal import lfilter
    # Smooth asymmetric pulse template
    k = np.arange(round(0.8 * fs)) / fs
    kernel = np.exp(-0.5 * ((k - 0.13) / 0.055)**2) + 0.20 * np.exp(-0.5 * ((k - 0.34) / 0.10)**2)
    wave = lfilter(kernel, [1], pulse)
    ir = 115000 + 2500 * wave + 170 * np.sin(2 * np.pi * 0.12 * t) + rng.normal(0, 50, n)
    red = 98000 + 1700 * wave + rng.normal(0, 70, n)
    return pd.DataFrame({'time_s': t, 'ir': ir, 'red': red})


def load_data(path, fs, channel, demo=False, duration=300):
    data = demo_data(fs, duration) if demo else pd.read_csv(path)
    data.columns = [str(c).strip().lower() for c in data.columns]
    if channel not in data:
        raise ValueError(f"Missing '{channel}' column. Found: {list(data.columns)}")
    raw = pd.to_numeric(data[channel], errors='coerce').to_numpy(dtype=float)
    if len(raw) < 10 * fs:
        raise ValueError('Need at least 10 seconds of data')
    if 'time_s' in data:
        time = pd.to_numeric(data['time_s'], errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
            raise ValueError('time_s must be finite and strictly increasing')
        intervals = np.diff(time)
        if abs(1 / np.median(intervals) - fs) / fs > .02:
            raise ValueError('Timestamp rate differs by >2% from --fs. Check acquisition settings.')
        if np.any(intervals > 1.5 / fs):
            raise ValueError('Detected timestamp gaps; segment or repair acquisition before analysis.')
        if np.max(np.abs(intervals - 1/fs)) > 0.15/fs:
            raise ValueError('Time sampling too irregular; resample from verified timestamps first.')
    else:
        time = np.arange(len(raw)) / fs
    if not np.isfinite(raw).all():
        raise ValueError('Non-finite or missing samples detected. Do not interpolate without review.')
    return data, raw, time


def evaluate_window(start, stop, fs, peaks, quality, raw, cleaned, min_quality, signal_format):
    sel = peaks[(peaks >= start) & (peaks < stop)]
    q = float(np.nanmedian(quality[start:stop]))
    raw_segment = raw[start:stop]
    # ADC clipping can only be interpreted for genuine unsigned ADC counts.
    # Published forehead data are transformed and may contain negative values.
    clipped = (float(np.mean((raw_segment <= 0) | (raw_segment >= 262143)))
               if signal_format == "adc" else float("nan"))
    flat = np.ptp(raw_segment) < max(1e-8, 1e-6 * max(1.0, np.max(np.abs(raw_segment))))
    row = {'start_s': round(start/fs, 3), 'end_s': round(stop/fs, 3),
           'quality_median': round(q, 3), 'clipped_fraction': round(clipped, 4),
           'detected_peaks': int(len(sel)), 'mean_hr_bpm': np.nan,
           'mean_interval_ms': np.nan,
           **dict.fromkeys(VARIABILITY_FIELDS, np.nan), 'pulse_amplitude_counts': np.nan,
           'accepted': False, 'reason': ''}
    if not np.isfinite(q) or q < min_quality:
        row['reason'] = 'low waveform template quality'
        return row
    if (np.isfinite(clipped) and clipped > 0.01) or flat:
        row['reason'] = 'clipping (ADC) or flat input signal'
        return row
    if len(sel) < 5:
        row['reason'] = 'too few detected beats'
        return row
    intervals_ms = np.diff(sel)/fs*1000
    if np.any((intervals_ms < 333) | (intervals_ms > 1500)):
        row['reason'] = 'implausible/missed pulse intervals; inspect peaks'
        return row
    if np.any(np.abs(intervals_ms - np.median(intervals_ms)) > 0.25*np.median(intervals_ms)):
        row['reason'] = 'possible pulse-detection artifact; inspect peaks'
        return row
    row['accepted'] = True
    row['reason'] = 'passes preliminary automated checks'
    row['mean_interval_ms'] = float(np.mean(intervals_ms))
    row['mean_hr_bpm'] = float(60000/np.mean(intervals_ms))
    row.update(interval_variability(intervals_ms))
    # Exploratory amplitude in filtered-signal units, not necessarily ADC counts.
    # Note: CSV field name is retained for backward compatibility.
    amp=[]
    for p in sel:
        a=max(start, p-int(.5*fs))
        if p > a:
            amp.append(float(cleaned[p] - np.min(cleaned[a:p+1])))
    if amp:
        row['pulse_amplitude_counts']=float(np.median(amp))
    return row


def variability_report(fs, window_s, peaks, quality, raw, cleaned, min_quality, signal_format):
    """Dedicated HRV-surrogate report; never join intervals across rejected windows.

    Only complete windows are used. If the recording cannot fill one window,
    return an explicit unavailable row instead of computing ultra-short metrics.
    Additional coverage checks avoid accepting brief clean fragments surrounded
    by long stretches of missed beats or poor template quality.
    """
    window = round(window_s * fs)
    starts = range(0, len(raw) - window + 1, window) if len(raw) >= window else [0]
    rows = []
    for start in starts:
        stop = min(start + window, len(raw))
        row = evaluate_window(start, stop, fs, peaks, quality, raw, cleaned,
                              min_quality, signal_format)
        sel = peaks[(peaks >= start) & (peaks < stop)]
        row.update({
            'variability_source': 'PPG-derived PRV (HRV surrogate; not ECG HRV)',
            'window_duration_s': (stop - start) / fs,
            'interval_count': max(0, len(sel) - 1),
            'interval_span_s': float((sel[-1] - sel[0]) / fs) if len(sel) > 1 else 0.0,
            'sample_resolution_ms': 1000 / fs,
            'poor_quality_fraction': float(np.mean(
                ~np.isfinite(quality[start:stop]) | (quality[start:stop] < min_quality))),
        })
        reason = None
        if stop - start < window:
            reason = 'recording shorter than requested variability window'
        elif row['accepted']:
            if (sel[0] - start) / fs > 1.5 or (stop - 1 - sel[-1]) / fs > 1.5:
                reason = 'insufficient pulse coverage at variability-window edges'
            elif row['poor_quality_fraction'] > 0.05:
                reason = 'more than 5% poor or missing template quality in variability window'
        if reason:
            row.update(dict.fromkeys(VARIABILITY_FIELDS, np.nan))
            row.update(accepted=False, reason=reason, mean_hr_bpm=np.nan, mean_interval_ms=np.nan,
                       pulse_amplitude_counts=np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def run(args):
    hemodynamics = None if getattr(args, 'skip_hemodynamics', False) else hemodynamics_from_args(args)
    import neurokit2 as nk
    df, raw, time = load_data(args.csv, args.fs, args.channel, args.demo, args.duration)
    signal_format = args.signal_format
    if signal_format == "auto":
        signal_format = "processed" if np.min(raw) < 0 else "adc"
    print(f"Input signal format: {signal_format} (auto is heuristic; verify dataset units)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    signals, info = nk.ppg_process(raw, sampling_rate=args.fs, method='elgendi', method_quality='templatematch')
    signals.insert(0, 'time_s', time)
    for c in ('ir','red'):
        if c in df and c != args.channel:
            signals[f'{c}_raw'] = pd.to_numeric(df[c],errors='coerce').to_numpy()
    peaks = np.asarray(info['PPG_Peaks'], dtype=int)
    quality = signals['PPG_Quality'].to_numpy(dtype=float)
    cleaned = signals['PPG_Clean'].to_numpy(dtype=float)
    window = round(args.window * args.fs)
    rows = [evaluate_window(s, s+window, args.fs, peaks, quality, raw, cleaned, args.min_quality, signal_format)
            for s in range(0, len(raw)-window+1, window)]
    windows=pd.DataFrame(rows)
    if windows.empty:
        raise ValueError('Recording too short for selected --window')
    hrv = variability_report(args.fs, args.hrv_window, peaks, quality, raw, cleaned,
                             args.min_quality, signal_format)
    signals.to_csv(out/'processed_signals.csv',index=False)
    windows.to_csv(out/'window_features.csv',index=False)
    hrv.to_csv(out/'hrv_features.csv', index=False)
    if hemodynamics is not None:
        pd.DataFrame([hemodynamics]).to_csv(out/'hemodynamics.csv', index=False)
    pd.DataFrame({'peak_sample':peaks,'peak_time_s':time[peaks]}).to_csv(out/'detected_peaks.csv',index=False)
    metadata = {
        'input': 'synthetic demo' if args.demo else str(Path(args.csv).resolve()),
        'sampling_rate_hz': args.fs, 'signal_format': signal_format,
        'time_origin_s': float(time[0]),
        'window_time_basis': 'seconds relative to recording start',
        'variability_source': 'PPG-derived PRV; not ECG-validated HRV',
        'variability_window_s': args.hrv_window,
        'variability_accepted_windows': int(hrv.accepted.sum()),
        'variability_total_windows': len(hrv),
        'variability_trailing_seconds_not_analyzed': (len(raw) % round(args.hrv_window * args.fs)) / args.fs,
        'interval_handling': 'whole-window rejection; no ectopic correction or interpolation',
        'variability_min_quality': args.min_quality,
        'variability_max_poor_quality_fraction': 0.05,
        'variability_interval_range_ms': [333, 1500],
        'variability_max_relative_interval_deviation': 0.25,
        'demo': args.demo,
    }
    if hemodynamics is not None:
        metadata.update(svri_units='dyn*s*cm^-5*m^2',
                        svri_formula='80 * (MAP_mmHg - CVP_mmHg) / CI_L_min_m2',
                        svri_source='external measurements; one supplied measurement set per run')
    (out/'analysis_metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    nplot = min(len(raw), round(30*args.fs))
    fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
    axes[0].plot(time[:nplot], raw[:nplot],lw=.8)
    axes[0].set_ylabel('ADC counts' if signal_format == 'adc' else 'Reported PPG units')
    axes[1].plot(time[:nplot], cleaned[:nplot],lw=.8)
    visible=peaks[peaks<nplot]
    axes[1].scatter(time[visible],cleaned[visible],c='red',s=14,label='Detected peaks')
    axes[1].legend(loc='upper right')
    axes[1].set_ylabel('Filtered PPG')
    axes[2].plot(time[:nplot],quality[:nplot],lw=1)
    axes[2].axhline(args.min_quality,color='red',ls=':',label='Exploratory threshold')
    axes[2].set_ylabel('Template quality')
    axes[2].set_xlabel('Time (s)')
    axes[2].legend(loc='upper right')
    fig.tight_layout();fig.savefig(out/'ppg_first_30s.png',dpi=160);plt.close(fig)
    print(f"Samples: {len(raw)}, peaks: {len(peaks)}, windows accepted: {int(windows.accepted.sum())}/{len(windows)}")
    print(f"Variability windows accepted: {int(hrv.accepted.sum())}/{len(hrv)} ({args.hrv_window} s each)")
    if hemodynamics is not None:
        print(f"SVRi: {hemodynamics['svri_status']}")
        if np.isfinite(hemodynamics['svri_dyn_s_cm5_m2']):
            print(f"SVRi = {hemodynamics['svri_dyn_s_cm5_m2']:.2f} dyn*s*cm^-5*m^2")
    print(f"Files written to: {out.resolve()}")
    print('Caution: quality threshold is exploratory; PRV estimates are not ECG-validated HRV.')
    if args.demo:
        print('DEMO MODE: synthetic data only, not evidence of sensor performance.')


def main():
    p=argparse.ArgumentParser(description='HALO MAX30102 PPG prototype (NeuroKit2)')
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv',help='CSV containing ir, optionally red and time_s')
    source.add_argument('--demo',action='store_true',help='Generate fake data for smoke test')
    p.add_argument('--fs',type=int,default=100,help='Actual acquisition sample rate, Hz (default 100)')
    p.add_argument('--channel',choices=['ir','red'],default='ir')
    p.add_argument('--signal-format', choices=['auto','adc','processed'], default='auto',
                   help='ADC counts or transformed PPG values; auto detects negative values')
    p.add_argument('--duration',type=int,default=300,help='Synthetic demo seconds')
    p.add_argument('--window',type=int,default=60,help='Nonoverlapping analysis-window length, seconds')
    p.add_argument('--hrv-window', type=int, default=60,
                   help='Separate PRV/HRV-surrogate window, seconds (minimum 60; use 300 for 5 minutes)')
    p.add_argument('--map-mmhg', type=float, help='Externally measured mean arterial pressure, mmHg')
    p.add_argument('--cvp-mmhg', type=float, help='Externally measured central venous pressure, mmHg')
    p.add_argument('--cardiac-index', type=float, help='Externally measured cardiac index, L/min/m^2')
    p.add_argument('--cardiac-output', type=float, help='Externally measured cardiac output, L/min (requires --bsa)')
    p.add_argument('--bsa', type=float, help='Body surface area, m^2 (with --cardiac-output)')
    p.add_argument('--skip-hemodynamics', action='store_true', help='Omit SVRi outputs (used by the live recorder)')
    p.add_argument('--min-quality',type=float,default=0.8,help='Experimental median template-quality cutoff (0-1)')
    p.add_argument('--out',default='halo_ppg_results')
    args=p.parse_args()
    if args.fs <= 0 or args.window < 10 or args.hrv_window < 60 or not (0 <= args.min_quality <= 1):
        p.error('Require fs>0, window>=10, hrv-window>=60, 0<=min-quality<=1')
    try:
        if args.skip_hemodynamics and any(getattr(args, field) is not None for field in
                ('map_mmhg', 'cvp_mmhg', 'cardiac_index', 'cardiac_output', 'bsa')):
            raise ValueError('Do not combine --skip-hemodynamics with hemodynamic measurements')
        hemodynamics_from_args(args)
    except ValueError as exc:
        p.error(str(exc))
    run(args)

if __name__ == '__main__':
    main()
