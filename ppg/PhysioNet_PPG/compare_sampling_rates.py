import numpy as np
import pandas as pd
import neurokit2 as nk
import wfdb

from scipy.signal import resample_poly
from pathlib import Path

# Load original recording and ECG reference
record = wfdb.rdrecord("s1_sit")
annotations = wfdb.rdann("s1_sit", "atr")

fs_original = int(record.fs)
ir = record.p_signal[:, record.sig_name.index("pleth_2")]
ecg_peaks_s = annotations.sample / fs_original

# Calculate features from peak timestamps within each window
def features(times, start, end):
    t = times[(times >= start) & (times < end)]
    if len(t) < 3:
        return None

    intervals = np.diff(t) * 1000

    return {
        "HR": 60000 / np.mean(intervals),
        "SDNN": np.std(intervals, ddof=1),
        "RMSSD": np.sqrt(np.mean(np.diff(intervals)**2))
    }

results = []

for fs in [500, 100]:
    # Proper anti-aliasing downsampling
    if fs == fs_original:
        signal = ir.copy()
    else:
        signal = resample_poly(ir, fs, fs_original)

    # Same NeuroKit method for both sampling rates
    if fs == 100:
        from scipy.signal import medfilt, butter, sosfiltfilt

        baseline = medfilt(signal, kernel_size=501)
        detrended = signal - baseline

        sos = butter(
            3, [0.5, 8],
            btype="bandpass",
            fs=fs,
            output="sos"
        )
        cleaned = sosfiltfilt(sos, detrended)
    else:
        cleaned = nk.ppg_clean(
            signal,
            sampling_rate=fs,
            method="elgendi"
        )

    _, info = nk.ppg_peaks(
        cleaned,
        sampling_rate=fs,
        method="elgendi"
    )

    ppg_peaks_s = np.asarray(info["PPG_Peaks"]) / fs

    # Identical 60-second windows
    for start in np.arange(0, 480, 60):
        end = start + 60

        ecg = features(ecg_peaks_s, start, end)
        ppg = features(ppg_peaks_s, start, end)

        if ecg is None or ppg is None:
            continue

        row = {
            "sampling_rate": fs,
            "start_s": start,
            "ecg_hr": ecg["HR"],
            "ppg_hr": ppg["HR"],
            "ecg_sdnn": ecg["SDNN"],
            "ppg_sdnn": ppg["SDNN"],
            "ecg_rmssd": ecg["RMSSD"],
            "ppg_rmssd": ppg["RMSSD"],
        }

        for metric in ["hr", "sdnn", "rmssd"]:
            row[f"{metric}_absolute_error"] = abs(
                row[f"ppg_{metric}"] - row[f"ecg_{metric}"]
            )

        results.append(row)

    print(f"{fs} Hz: {len(ppg_peaks_s)} PPG peaks")

df = pd.DataFrame(results)

output = Path("sampling_rate_comparison.csv")
df.to_csv(output, index=False)

print("\nMean absolute error against ECG:")
print(
    df.groupby("sampling_rate")[
        ["hr_absolute_error",
         "sdnn_absolute_error",
         "rmssd_absolute_error"]
    ].mean().round(3)
)

print(f"\nSaved: {output.resolve()}")
