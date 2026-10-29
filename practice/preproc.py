"""
EEG preprocessing + workload feature pipeline (checking capstone feasibility).

One function per stage, all driven by a single SETTINGS dict so the same
pipeline runs on EEGBCI (practice), a workload dataset (e.g. n-back), and
our own device recordings later in capstone pipeline.

Assumption for this phase: full signal acquisition, no dropouts.

Usage (from a notebook):
    import preproc
    raw = preproc.load_eegbci(subject=1, run=1)
    clean, feats, log = preproc.run_pipeline(raw, subject=1, condition="eyes_open")


    

Using the baseline sets for EEGBCI. This dataset is used for motor imagery but the first
two sets are eyes-open and eyes-closed baseline which can be used for validation of the
pipeline.

EEGBCI uses the standard 1010 placement and 160hz sampling
https://physionet.org/content/eegmmidb/1.0.0/

"""

from __future__ import annotations

import copy
import numpy as np
import pandas as pd
import mne

mne.set_log_level("WARNING")

# ---------------------------------------------------------------------------
# Settings: change these, not the functions
# ---------------------------------------------------------------------------
SETTINGS = {
    # Step 2: load / standardize
    "montage": "standard_1020",
    "resample_hz": 160.0,      
    # Step 3: filter
    "l_freq": 1.0,
    "h_freq": 40.0,
    "notch_hz": 60.0, #for safer config reuse - rn its redundant cus of bandpass
    
    
    # Step 4: bad channels
    "bad_z_thresh": 5.0,           # robust z-score on channel log-variance
    # Variance alone can't tell noise from real signal: blinks make Fp1/Fp2
    # big, eyes-closed alpha makes O1/O2 big. Keep EOG proxies out of the
    # variance check so ICA still has them (they're still flagged if flat).

    #good channel eeg always has v wiggling around its mean. so non-zero std is expected.
    #if it has 0 std or close to 0 that means we have a flat channel (electrode not picking up anything)
    "flat_uv": 1.0, #1uV
    # Step 5: reference
    "reference": "average",        # "average" or list of channels e.g. ["A1","A2"]
    # Step 6: artifacts
    "use_ica": True,               # needs many channels; turn off for reduced montage
    "ica_n_components": 0.99,      # fraction of variance or int
    "eog_proxy": ["Fp1", "Fp2"],   # frontal channels used as stand-in EOG
    "reject_ptp_uv": "auto",       # "auto" (data-driven), a µV number, or None
    "reject_auto_k": 5.0,          # "auto": reject if p-p > median + k * robust SD
    # Step 7: windows + features
    "win_s": 4.0,
    "step_s": 2.0,
    "bands": {"theta": (4.0, 8.0), "alpha": (8.0, 13.0), "beta": (13.0, 30.0)},
    "total_band": (1.0, 40.0),     # denominator for relative power
    "ratio_num": ("theta", "Fz"),  # workload index = frontal theta / parietal alpha
    "ratio_den": ("alpha", "Pz"),
    # Step 11: reduced montage (None = all channels)
    "channels": None,              # e.g. ["Fz", "Pz"] to mimic the device
    "random_state": 42,
}


def get_settings(**overrides) -> dict:
    """Copy of SETTINGS with overrides, e.g. get_settings(win_s=2, use_ica=False)."""
    s = copy.deepcopy(SETTINGS)
    for k, v in overrides.items():
        if k not in s:
            raise KeyError(f"Unknown setting: {k}")
        s[k] = v
    return s


# ---------------------------------------------------------------------------
# Step 2: load + standardize
# ---------------------------------------------------------------------------
def load_eegbci(subject: int, run: int, path: str = "./eeg_data/") -> mne.io.Raw:
    """Load one EEGBCI run. R01 = eyes open baseline, R02 = eyes closed baseline."""
    from mne.datasets import eegbci

    fname = eegbci.load_data(subject, [run], path=path, update_path=False)[0]
    raw = mne.io.read_raw_edf(fname, preload=True)
    eegbci.standardize(raw) #fix channel names, capitals etc
    return raw


def standardize(raw: mne.io.Raw, s: dict) -> mne.io.Raw:
    """Montage, resample, optional channel subset. Works on a copy."""
    raw = raw.copy()
    raw.set_montage(s["montage"], on_missing="warn")
    if s["resample_hz"] and raw.info["sfreq"] != s["resample_hz"]:
        raw.resample(s["resample_hz"])
    return raw


# ---------------------------------------------------------------------------
# Step 3: filter
# ---------------------------------------------------------------------------
def filter_raw(raw: mne.io.Raw, s: dict) -> mne.io.Raw:
    raw = raw.copy()
    if s["notch_hz"]:
        raw.notch_filter(s["notch_hz"])
    raw.filter(s["l_freq"], s["h_freq"])
    return raw


# ---------------------------------------------------------------------------
# Step 4: bad channels
# ---------------------------------------------------------------------------
def find_bad_channels(raw: mne.io.Raw, s: dict) -> list[str]:
    """Flag flat channels and channels with outlying variance (robust z-score)."""
    data = raw.get_data(picks="eeg") * 1e6  # V -> µV

    #find indices of channels that are eeg from raw.info then iterate through to find raw ch name
    names = [raw.ch_names[i] for i in mne.pick_types(raw.info, eeg=True)]

    ##CHANGE LATER: for now we are flagging channels based on variance across entire time span
    #this is less desireable when recording gets long with many transient artifacts
    #make more robust for capstone use later, for now its fine (eegbci is short window samples)
    std = data.std(axis=1) #collapse columns - take std over all channels time samples (one num per ch)
    flat = std < s["flat_uv"]

    #finding noisy channels
    logv = np.log(np.maximum(std, 1e-6) ** 2)
    med = np.median(logv)
    mad = np.median(np.abs(logv - med)) * 1.4826 or 1e-9
    noisy = np.abs((logv - med) / mad) > s["bad_z_thresh"]

    #exclude protected channels
    protected = set(s["eog_proxy"])
    return [n for n, f, z in zip(names, flat, noisy)
            if f or (z and n not in protected)]


def fix_bad_channels(raw: mne.io.Raw, bads: list[str]) -> mne.io.Raw:
    raw = raw.copy()
    raw.info["bads"] = bads
    if bads:
        raw.interpolate_bads(reset_bads=True)
    return raw


# ---------------------------------------------------------------------------
# Step 5: reference
# ---------------------------------------------------------------------------
def rereference(raw: mne.io.Raw, s: dict) -> mne.io.Raw:
    """NOTE: average reference is not valid with only a few channels;
    for a reduced montage set s['reference'] to the device's reference."""

    #this is just ensuring across different datasets we can provide the same reference
    #average reduces the bias of any single reference sit and works with dense montages
    return raw.copy().set_eeg_reference(s["reference"])


# ---------------------------------------------------------------------------
# Step 6: artifacts
# ---------------------------------------------------------------------------
def remove_eog_ica(raw: mne.io.Raw, s: dict) -> tuple[mne.io.Raw, list[int]]:
    """Fit ICA on a 1 Hz high-passed copy, drop components correlated with
    the frontal EOG proxy channels, apply to the data."""
    fit_raw = raw.copy().filter(l_freq=1.0, h_freq=None)
    ica = mne.preprocessing.ICA(
        n_components=s["ica_n_components"],
        method="fastica",
        random_state=s["random_state"],
        max_iter="auto",
    )
    ica.fit(fit_raw)
    proxies = [c for c in s["eog_proxy"] if c in raw.ch_names]
    exclude: list[int] = []
    if proxies:
        idx, _ = ica.find_bads_eog(fit_raw, ch_name=proxies)
        exclude = sorted(set(idx))
    ica.exclude = exclude
    return ica.apply(raw.copy()), exclude


# ---------------------------------------------------------------------------
# Step 7: windows + features
# ---------------------------------------------------------------------------
def make_windows(raw: mne.io.Raw, s: dict) -> tuple[mne.Epochs, int]:
    """Fixed-length windows; drop any whose peak-to-peak exceeds the threshold."""
    epochs = mne.make_fixed_length_epochs(
        raw, duration=s["win_s"], overlap=s["win_s"] - s["step_s"], preload=True
    )
    n_before = len(epochs)
    thr = s["reject_ptp_uv"]
    if thr == "auto":
        # Data-driven: drop a window if any channel's peak-to-peak is far above
        # that channel's typical value (median + k * robust SD). Amplitude scale
        # varies a lot between amplifiers/datasets, so a fixed µV cutoff can
        # throw away everything (EEGBCI sits around 100-200 µV p-p).
        d = epochs.get_data(picks="eeg")
        ptp = d.max(-1) - d.min(-1)                       # (windows, channels)
        med = np.median(ptp, axis=0)
        mad = np.median(np.abs(ptp - med), axis=0) * 1.4826
        bad = (ptp > med + s["reject_auto_k"] * mad).any(axis=1)
        epochs.drop(np.where(bad)[0], reason="PTP_AUTO")
    elif thr:
        epochs.drop_bad(reject={"eeg": thr * 1e-6})
    if len(epochs) == 0:
        raise RuntimeError(
            "All windows rejected. Check amplitude scale, or loosen "
            "'reject_ptp_uv' / 'reject_auto_k'."
        )
    return epochs, n_before - len(epochs)


def band_power_features(
    epochs: mne.Epochs, s: dict, subject=None, condition=None
) -> pd.DataFrame:
    """One row per window x channel: log absolute and relative band power,
    plus the workload ratio (one value per window, repeated on each row)."""
    sf = epochs.info["sfreq"]
    psd = epochs.compute_psd(
        method="welch", fmin=s["total_band"][0], fmax=s["total_band"][1],
        n_fft=int(sf * 2), n_per_seg=int(sf * 2),  # 2 s segments -> 0.5 Hz bins
    )
    p, f = psd.get_data(return_freqs=True)  # (windows, channels, freqs), V^2/Hz
    df_f = f[1] - f[0]
    total = p.sum(-1) * df_f

    out = {}
    for name, (lo, hi) in s["bands"].items():
        bp = p[..., (f >= lo) & (f < hi)].sum(-1) * df_f
        out[f"{name}_abs"] = bp
        out[f"{name}_rel"] = bp / total

    ch = epochs.ch_names
    # window start times in seconds, relative to the recording
    t0 = epochs.events[:, 0] / sf  # dropped windows leave gaps, so time stays honest

    (nb, nc), (db, dc) = s["ratio_num"], s["ratio_den"]
    ratio = np.full(len(epochs), np.nan)
    if nc in ch and dc in ch:
        ratio = out[f"{nb}_abs"][:, ch.index(nc)] / out[f"{db}_abs"][:, ch.index(dc)]

    rows = []
    for w in range(len(epochs)):
        for c, cname in enumerate(ch):
            row = {"subject": subject, "condition": condition, "window": w,
                   "t_start_s": t0[w], "channel": cname}
            for k, v in out.items():
                row[k] = v[w, c]
                if k.endswith("_abs"):
                    row[f"log_{k}"] = np.log10(v[w, c])
            row["workload_ratio"] = ratio[w]
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 8: within-subject normalization
# ---------------------------------------------------------------------------
def normalize_to_baseline(
    feats: pd.DataFrame, baseline_condition: str, cols: list[str] | None = None
) -> pd.DataFrame:
    """z-score each subject's features against their own baseline windows,
    per channel. Adds columns '<col>_z'."""
    cols = cols or [c for c in feats.columns if c.startswith("log_") or c.endswith("_rel")]
    cols = cols + (["workload_ratio"] if "workload_ratio" in feats.columns else [])
    feats = feats.reset_index(drop=True)  # concat'd tables repeat index labels
    for (subj, chan), g in feats.groupby(["subject", "channel"]):
        base = g[g["condition"] == baseline_condition]
        if base.empty:
            continue
        for c in cols:
            mu, sd = base[c].mean(), base[c].std() or np.nan
            feats.loc[g.index, f"{c}_z"] = (g[c] - mu) / sd
    return feats


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
def run_pipeline(raw: mne.io.Raw, s: dict | None = None, subject=None, condition=None):
    """Steps 2-7. Returns (cleaned raw, feature table, log dict).
    The input raw is never modified."""
    s = s or get_settings()
    log = {"subject": subject, "condition": condition}

    x = standardize(raw, s)
    x = filter_raw(x, s)

    bads = find_bad_channels(x, s)
    x = fix_bad_channels(x, bads)
    log["bad_channels"] = bads

    x = rereference(x, s)

    if s["use_ica"]:
        x, excluded = remove_eog_ica(x, s)
        log["ica_excluded"] = excluded

    if s["channels"]:
        x = x.copy().pick(s["channels"])  # Step 11: mimic device channel set

    epochs, n_dropped = make_windows(x, s)
    log["windows_kept"] = len(epochs)
    log["windows_dropped"] = n_dropped

    feats = band_power_features(epochs, s, subject, condition)
    return x, feats, log