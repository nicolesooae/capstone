import numpy as np
import matplotlib.pyplot as plt
import neurokit2 as nk
import wfdb
from scipy.signal import resample_poly

record = wfdb.rdrecord("s1_sit")
ir = record.p_signal[:, record.sig_name.index("pleth_2")]

# Original 500 Hz processing
clean_500 = nk.ppg_clean(ir, sampling_rate=500)
raw_100 = resample_poly(ir, 1, 5)

# Compare processing order
signals = {
    "500 Hz original": (clean_500, 500),
    "100 Hz raw-first": (
        nk.ppg_clean(raw_100, sampling_rate=100), 100
    ),
    "100 Hz clean-first": (
        resample_poly(clean_500, 1, 5), 100
    )
}

fig, axes = plt.subplots(3, 1, figsize=(13, 9))

for ax, (name, (signal, fs)) in zip(
    axes, signals.items()
):
    _, info = nk.ppg_peaks(
        signal,
        sampling_rate=fs,
        method="elgendi"
    )

    peaks = np.asarray(info["PPG_Peaks"])
    intervals = np.diff(peaks) / fs

    print(f"\n{name}")
    print(f"Samples: {len(signal)}")
    print(f"Peaks: {len(peaks)}")
    print(f"Signal std: {np.std(signal):.6f}")

    if len(intervals):
        print(f"Median IBI: {np.median(intervals):.3f} s")
        print(f"Maximum IBI: {np.max(intervals):.3f} s")

    n = min(len(signal), fs * 15)
    t = np.arange(n) / fs

    ax.plot(t, signal[:n], linewidth=1)
    visible = peaks[peaks < n]
    ax.scatter(
        visible / fs,
        signal[visible],
        color="red",
        s=15
    )
    ax.set_title(name)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("PPG")

plt.tight_layout()
plt.savefig("diagnose_100hz.png", dpi=160)
print("\nSaved diagnose_100hz.png")
