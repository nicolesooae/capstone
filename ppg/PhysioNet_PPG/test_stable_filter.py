import numpy as np
import neurokit2 as nk
import wfdb

from scipy.signal import butter, sosfiltfilt, resample_poly

record = wfdb.rdrecord("s1_sit")
raw = record.p_signal[:, record.sig_name.index("pleth_2")]

raw_100 = resample_poly(raw, 1, 5)

# Numerically stable 0.5 to 8 Hz Butterworth bandpass
sos = butter(
    3,
    [0.5, 8],
    btype="bandpass",
    fs=100,
    output="sos"
)

clean_100 = sosfiltfilt(sos, raw_100)

_, info = nk.ppg_peaks(
    clean_100,
    sampling_rate=100,
    method="elgendi"
)

peaks = np.asarray(info["PPG_Peaks"])
ibi = np.diff(peaks) / 100

print("Detected peaks:", len(peaks))
print("Median IBI:", np.median(ibi))
print("Maximum IBI:", np.max(ibi))
print("Cleaned signal std:", np.std(clean_100))
