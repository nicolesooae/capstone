import numpy as np
import neurokit2 as nk
import wfdb

from scipy.signal import (
    butter,
    sosfiltfilt,
    resample_poly,
    medfilt
)

record = wfdb.rdrecord("s1_sit")
raw = record.p_signal[:, record.sig_name.index("pleth_2")]

# Downsample to 100 Hz
fs = 100
raw_100 = resample_poly(raw, 1, 5)

# Remove slowly varying baseline
# 5-second median filter
baseline = medfilt(raw_100, kernel_size=501)
detrended = raw_100 - baseline

# Stable Butterworth bandpass
sos = butter(
    3,
    [0.5, 8],
    btype="bandpass",
    fs=fs,
    output="sos"
)

clean = sosfiltfilt(sos, detrended)

# Detect peaks
_, info = nk.ppg_peaks(
    clean,
    sampling_rate=fs,
    method="elgendi"
)

peaks = np.asarray(info["PPG_Peaks"])
ibi = np.diff(peaks) / fs

print("Detected peaks:", len(peaks))
print("Median IBI:", np.median(ibi))
print("Maximum IBI:", np.max(ibi))
print("Cleaned signal std:", np.std(clean))
