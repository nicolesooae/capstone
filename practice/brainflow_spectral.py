"""
Load a BrainFlow EEG recording (CSV), run it through the preproc.py pipeline,
and plot / save spectral features.

BrainFlow's DataFilter.write_file() saves one row per sample and one column per
board channel (package num, EEG, accel, ..., timestamp, marker), tab-separated,
no header, EEG in µV. Which columns are EEG and the sampling rate depend on the
board, so pass --board (or --sfreq + --eeg-cols for anything else).

Usage:
    python brainflow_spectral.py recording.csv --board 0 \
        --ch-names Fp1 Fp2 C3 C4 P7 P8 O1 O2
    python brainflow_spectral.py recording.csv --sfreq 250 --eeg-cols 1 2 3 4 \
        --ch-names Fz Cz Pz Oz --notch 60

Outputs go to results/<csv name>/: features.csv, summary.csv, log, and PNGs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import mne
import numpy as np
import pandas as pd

import preproc

# Board defaults if brainflow isn't installed: (sfreq, EEG column indices).
# With brainflow installed, BoardShim is the source of truth.
BOARD_FALLBACK = {
    -1: (250.0, list(range(1, 17))),  # synthetic
    0: (250.0, list(range(1, 9))),    # OpenBCI Cyton
    1: (200.0, list(range(1, 5))),    # OpenBCI Ganglion
    2: (125.0, list(range(1, 17))),   # OpenBCI Cyton + Daisy
}

# Blink masking (see blink_masked_features)
BLINK = {
    "pre_s": 0.25,       # mark this long before the blink peak...
    "post_s": 0.45,      # ...and this long after (blink + recovery)
    "seg_s": 1.0,        # clean segment length -> 1 Hz PSD bins
    "seg_step_s": 0.5,
    "min_clean_s": 1.5,  # windows with less clean data than this are skipped
    # Fixed p-p limit per 1 s segment. The data-driven "auto" limit fails on
    # Fp channels: most of the recording has eye activity, so the median it is
    # built on is itself artifact. On the 2026-10-29 Fp1/Fp2 test, auto left
    # theta at ~106 µV²/Hz (tracking blink count, r=0.73); 75-150 µV all gave a
    # stable ~2 µV²/Hz uncorrelated with blinks.
    "ptp_uv": 100.0,
    # Absolute blink threshold (1-10 Hz filtered). MNE's default is relative
    # to the signal range, so it "finds" blinks in blink-free data.
    "blink_uv": 100.0,
}

# Validated categorical palette (slots 1-3), one colour per band, fixed order.
BAND_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
MEAN_COLOR = "#2a78d6"
INK, MUTED, GRID = "#222222", "#6b6b6b", "#e4e4e0"

plt.rcParams.update({
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "axes.titlecolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.titleweight": "bold", "axes.axisbelow": True, "figure.dpi": 110,
})


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def board_info(board_id: int) -> tuple[float, list[int], int | None]:
    """(sfreq, eeg column indices, marker column or None) for a BrainFlow board."""
    try:
        from brainflow.board_shim import BoardShim
        marker = None
        try:
            marker = BoardShim.get_marker_channel(board_id)
        except Exception:
            pass
        return (float(BoardShim.get_sampling_rate(board_id)),
                list(BoardShim.get_eeg_channels(board_id)), marker)
    except ImportError:
        if board_id not in BOARD_FALLBACK:
            raise SystemExit(
                f"brainflow not installed and board {board_id} has no fallback; "
                "pass --sfreq and --eeg-cols, or `pip install brainflow`."
            )
        sf, cols = BOARD_FALLBACK[board_id]
        return sf, cols, None


def read_table(path: str | Path) -> pd.DataFrame:
    """Read a BrainFlow CSV (tab or comma, with or without a header row)."""
    df = pd.read_csv(path, sep=None, engine="python", header=None)
    first = pd.to_numeric(df.iloc[0], errors="coerce")
    if first.isna().any():  # first row is a header
        df.columns = df.iloc[0].astype(str).str.strip()
        df = df.iloc[1:].reset_index(drop=True)
    return df.apply(pd.to_numeric, errors="coerce")


def load_brainflow_csv(
    path: str | Path,
    board_id: int | None = 0,
    sfreq: float | None = None,
    eeg_cols: list | None = None,
    ch_names: list[str] | None = None,
    marker_col: int | str | None = None,
) -> mne.io.Raw:
    """BrainFlow CSV -> mne Raw (in volts). Nonzero marker values become annotations."""
    df = read_table(path)

    if board_id is not None:
        b_sf, b_cols, b_marker = board_info(board_id)
        sfreq = sfreq or b_sf
        eeg_cols = eeg_cols or b_cols
        marker_col = marker_col if marker_col is not None else b_marker
    if sfreq is None or not eeg_cols:
        raise SystemExit("Need --board, or both --sfreq and --eeg-cols.")

    def col(c):  # index by position, or by name if the file had a header
        return df[c] if c in df.columns else df.iloc[:, int(c)]

    # a recording copied while still being written ends on a truncated line
    while len(df) and df.iloc[-1].isna().any():
        df = df.iloc[:-1]

    data = np.vstack([col(c).to_numpy(float) for c in eeg_cols])
    # all-zero EEG rows are empty packets (seen at stream start), not signal
    data[:, (data == 0).all(axis=0)] = np.nan
    # BrainFlow can leave the odd NaN on dropped packets; fill by interpolation
    if np.isnan(data).any():
        data = pd.DataFrame(data.T).interpolate(limit_direction="both").to_numpy().T

    ch_names = ch_names or [f"EEG{i + 1}" for i in range(len(eeg_cols))]
    if len(ch_names) != len(eeg_cols):
        raise SystemExit(f"{len(ch_names)} channel names for {len(eeg_cols)} EEG columns.")

    info = mne.create_info(ch_names, sfreq, ch_types="eeg")
    raw = mne.io.RawArray(data * 1e-6, info)  # µV -> V

    if marker_col is not None:
        try:
            m = col(marker_col).fillna(0).to_numpy()
            idx = np.flatnonzero(m)
            if len(idx):
                raw.set_annotations(mne.Annotations(
                    idx / sfreq, 0.0, [str(int(v)) if v == int(v) else str(v) for v in m[idx]]
                ))
        except (IndexError, KeyError):
            pass
    return raw


# ---------------------------------------------------------------------------
# Preprocess: same stages as preproc.run_pipeline, but each one is skipped
# when a small consumer montage can't support it.
# ---------------------------------------------------------------------------
def preprocess(raw: mne.io.Raw, s: dict, condition=None, blinks: str = "auto"):
    log = {"n_channels": len(raw.ch_names), "sfreq": raw.info["sfreq"],
           "duration_s": round(raw.times[-1], 2)}

    montage = mne.channels.make_standard_montage(s["montage"])
    has_pos = all(c in montage.ch_names for c in raw.ch_names)
    if not has_pos:
        s = {**s, "montage": None}  # generic names: no positions, no interpolation
    log["has_positions"] = has_pos

    x = preproc.standardize(raw, s) if has_pos else raw.copy()
    if s["resample_hz"] and x.info["sfreq"] != s["resample_hz"]:
        x.resample(s["resample_hz"])
    if s["notch_hz"] and s["notch_hz"] >= x.info["sfreq"] / 2:
        s = {**s, "notch_hz": None}
    x = preproc.filter_raw(x, s)

    bads = preproc.find_bad_channels(x, s)
    if len(x.ch_names) < 8:
        # robust z over a handful of channels flags real signal (e.g. strong
        # frontal theta) as noise, so only trust the flat-channel check here
        std_uv = dict(zip(x.ch_names, x.get_data().std(axis=1) * 1e6))
        bads = [b for b in bads if std_uv[b] < s["flat_uv"]]
    log["bad_channels"] = bads
    if bads:
        if has_pos and len(bads) < len(x.ch_names) // 2:
            x = preproc.fix_bad_channels(x, bads)
        else:
            x = x.copy().drop_channels(bads)
            log["bad_channels_dropped"] = True

    # Average reference needs decent head coverage; otherwise keep the board's
    # own reference (e.g. Cyton SRB / earlobe).
    if s["reference"] == "average" and len(x.ch_names) < 8:
        log["reference"] = "as recorded (too few channels for average)"
    else:
        x = preproc.rereference(x, s)
        log["reference"] = s["reference"]

    proxies = [c for c in s["eog_proxy"] if c in x.ch_names]
    ran_ica = s["use_ica"] and len(x.ch_names) >= 8 and proxies
    if ran_ica:
        x, excluded = preproc.remove_eog_ica(x, s)
        log["ica_excluded"] = excluded
    else:
        log["ica_excluded"] = "skipped"

    if blinks == "on" or (blinks == "auto" and proxies and not ran_ica):
        x, blink_t = annotate_blinks(x, proxies)
        log["blinks"] = len(blink_t)
        log["blink_rate_per_min"] = round(len(blink_t) / (x.times[-1] / 60), 1)
        epochs, feats, wlog = blink_masked_features(x, s, blink_t, condition)
        log.update(wlog)
        return x, epochs, feats, log

    epochs, n_dropped = preproc.make_windows(x, s)
    log["windows_kept"] = len(epochs)
    log["windows_dropped"] = n_dropped

    feats = preproc.band_power_features(epochs, s, subject=None, condition=condition)
    return x, epochs, feats, log


# ---------------------------------------------------------------------------
# Blink masking: for frontal-only montages where ICA isn't possible.
# Blinks at Fp1/Fp2 are 100-500 µV and mostly 1-8 Hz, i.e. right on top of
# theta, and come every 1-3 s, so a 4 s window almost always contains one.
# Instead of dropping whole windows we mark each blink, cut the data into
# short segments, keep only clean ones, and average their spectra per window.
# ---------------------------------------------------------------------------
def annotate_blinks(x: mne.io.Raw, proxies: list[str]):
    """Detect blinks on the mean of the frontal channels; add BAD_blink spans.
    Returns (raw, blink peak times in s, recording time)."""
    sf = x.info["sfreq"]
    eog = mne.io.RawArray(x.get_data(picks=proxies).mean(0, keepdims=True),
                          mne.create_info(["EOG"], sf, "eog"), verbose=False)
    ev = mne.preprocessing.find_eog_events(eog, ch_name="EOG", l_freq=1, h_freq=10,
                                           thresh=BLINK["blink_uv"] * 1e-6, verbose=False)
    # annotations and preproc's t_start_s use recording time (includes any crop)
    peaks = ev[:, 0] / sf + x.first_time
    x = x.copy()
    pre, post = BLINK["pre_s"], BLINK["post_s"]
    # append in place: raw.annotations stores recording time, whereas
    # set_annotations() would read new onsets as relative to the data start
    start = np.clip(peaks - pre, x.first_time, None)
    end = np.clip(peaks + post, None, x.first_time + x.times[-1])
    x.annotations.append(start, end - start, ["BAD_blink"] * len(peaks))
    return x, peaks


def blink_masked_features(x: mne.io.Raw, s: dict, blink_t: np.ndarray, condition=None):
    """Same columns as preproc.band_power_features, plus clean_frac and
    blink_rate_per_min, computed from clean short segments inside each window."""
    sf = x.info["sfreq"]
    seg, step = BLINK["seg_s"], BLINK["seg_step_s"]
    # make_windows drops segments overlapping BAD_* and does the PTP check,
    # which also catches muscle bursts and eye movements the detector missed
    ep, n_dropped = preproc.make_windows(
        x, {**s, "win_s": seg, "step_s": step, "reject_ptp_uv": BLINK["ptp_uv"]})
    psd = ep.compute_psd(method="welch", fmin=s["total_band"][0], fmax=s["total_band"][1],
                         n_fft=int(sf * seg), n_per_seg=int(sf * seg), verbose=False)
    p, f = psd.get_data(return_freqs=True)  # (segments, channels, freqs), 1 Hz bins
    df_f = f[1] - f[0]
    seg_t = ep.events[:, 0] / sf

    per_win = int(round((s["win_s"] - seg) / step)) + 1
    min_segs = int(np.ceil((BLINK["min_clean_s"] - seg) / step)) + 1
    starts = x.first_time + np.arange(0, x.times[-1] - s["win_s"] + 1e-9, s["step_s"])

    ch = ep.ch_names
    (nb, nc), (db, dc) = s["ratio_num"], s["ratio_den"]
    rows, n_skipped = [], 0
    for w0 in starts:
        inside = (seg_t >= w0 - 1e-9) & (seg_t + seg <= w0 + s["win_s"] + 1e-9)
        if inside.sum() < min_segs:
            n_skipped += 1
            continue
        pw = p[inside].mean(0)  # (channels, freqs)
        total = pw.sum(-1) * df_f
        bp = {b: pw[:, (f >= lo) & (f < hi)].sum(-1) * df_f for b, (lo, hi) in s["bands"].items()}
        ratio = (bp[nb][ch.index(nc)] / bp[db][ch.index(dc)]
                 if nc in ch and dc in ch else np.nan)
        n_blinks = ((blink_t >= w0) & (blink_t < w0 + s["win_s"])).sum()
        for c, cname in enumerate(ch):
            row = {"subject": None, "condition": condition, "window": len(rows) // len(ch),
                   "t_start_s": w0, "channel": cname}
            for b in bp:
                row[f"{b}_abs"] = bp[b][c]
                row[f"{b}_rel"] = bp[b][c] / total[c]
                row[f"log_{b}_abs"] = np.log10(bp[b][c])
            row["workload_ratio"] = ratio
            row["clean_frac"] = inside.sum() / per_win
            row["blink_rate_per_min"] = n_blinks * 60 / s["win_s"]
            rows.append(row)

    n_possible = int((x.times[-1] - seg) // step) + 1
    wlog = {"clean_segments": len(ep), "segments_dropped_ptp": n_dropped,
            "clean_time_frac": round(len(ep) / n_possible, 2),
            "windows_kept": len(starts) - n_skipped, "windows_too_contaminated": n_skipped}
    return ep, pd.DataFrame(rows), wlog


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------
def shade_bands(ax, s):
    for (name, (lo, hi)), c in zip(s["bands"].items(), BAND_COLORS):
        ax.axvspan(lo, hi, color=c, alpha=0.08, lw=0)
        ax.text((lo + hi) / 2, 1.0, name, transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=9, color=MUTED)


def plot_psd(epochs, s, title):
    """Welch PSD per channel (thin grey) + channel mean, averaged over windows."""
    sf = epochs.info["sfreq"]
    n = min(int(sf * 2), len(epochs.times))  # blink-masked segments are only 1 s
    psd = epochs.compute_psd(method="welch", fmin=s["total_band"][0], fmax=s["total_band"][1],
                             n_fft=n, n_per_seg=n)
    p, f = psd.get_data(return_freqs=True)
    p_db = 10 * np.log10(p.mean(0) * 1e12)  # mean over windows, µV²/Hz -> dB

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for ch_p in p_db:
        ax.plot(f, ch_p, color="#b9b9b3", lw=1)
    ax.plot(f, p_db.mean(0), color=MEAN_COLOR, lw=2, label="channel mean")
    # label the strongest channel so outliers are identifiable
    hi = int(np.argmax(p_db.mean(1)))
    ax.annotate(epochs.ch_names[hi], (f[-1], p_db[hi, -1]), xytext=(4, 0),
                textcoords="offset points", fontsize=8, color=MUTED, va="center")
    shade_bands(ax, s)
    ax.set(xlabel="Frequency (Hz)", ylabel="Power (dB re 1 µV²/Hz)", xlim=(f[0], f[-1]))
    ax.set_title(title, pad=18)
    ax.legend(frameon=False, loc="upper right", labelcolor=INK)
    fig.tight_layout()
    return fig


def plot_band_bars(feats, s):
    """Mean relative band power per channel, grouped bars."""
    bands = list(s["bands"])
    m = feats.groupby("channel", sort=False)[[f"{b}_rel" for b in bands]].mean()
    x = np.arange(len(m))
    w = 0.8 / len(bands)

    fig, ax = plt.subplots(figsize=(max(6, 0.7 * len(m) + 3), 4.5))
    for i, (b, c) in enumerate(zip(bands, BAND_COLORS)):
        ax.bar(x + (i - (len(bands) - 1) / 2) * w, m[f"{b}_rel"] * 100, w * 0.92,
               color=c, label=b, edgecolor="white", linewidth=1)
    ax.set_xticks(x, m.index)
    ax.set(ylabel="Relative power (% of 1-40 Hz)")
    ax.grid(axis="x", visible=False)
    ax.set_title("Relative band power by channel")
    ax.legend(frameon=False, ncols=len(bands), loc="upper left", bbox_to_anchor=(0, 1.0))
    ax.set_ylim(0, ax.get_ylim()[1] * 1.12)
    fig.tight_layout()
    return fig


def plot_band_time(feats, s, annotations=None, segments=None):
    """Relative band power over time (mean across channels), one line per band."""
    bands = list(s["bands"])
    m = feats.groupby("t_start_s")[[f"{b}_rel" for b in bands]].mean()
    # put skipped windows back as NaN so the lines break instead of bridging gaps
    grid = np.round(np.arange(m.index.min(), m.index.max() + 1e-9, s["step_s"]), 6)
    m = m.set_axis(np.round(m.index, 6)).reindex(grid)
    t = pd.Series(m.index + s["win_s"] / 2, index=m.index)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for b, c in zip(bands, BAND_COLORS):
        ax.plot(t, m[f"{b}_rel"] * 100, color=c, lw=2, marker="o", ms=3, label=b)
    if annotations is not None:
        for onset, desc in zip(annotations.onset, annotations.description):
            if desc.startswith("BAD"):
                continue
            ax.axvline(onset, color=MUTED, lw=0.8, ls="--")
            ax.text(onset, 1.0, desc, transform=ax.get_xaxis_transform(),
                    fontsize=8, color=MUTED, ha="center", va="bottom")
    for name, a, b in segments or []:
        ax.axvspan(a, b, color=GRID, alpha=0.5, lw=0, zorder=0)
        ax.text((a + b) / 2, 1.0, name, transform=ax.get_xaxis_transform(),
                fontsize=9, color=INK, ha="center", va="bottom")
    ax.set(xlabel="Time (s, window centre)", ylabel="Relative power (%)")
    ax.set_title("Band power over time (mean across channels)", pad=14)
    ax.legend(frameon=False, loc="upper left", ncols=len(bands))
    fig.tight_layout()
    return fig


def plot_ratio_time(feats, s):
    nb, nc = s["ratio_num"]
    db, dc = s["ratio_den"]
    r = feats.groupby("window").agg(t=("t_start_s", "first"), ratio=("workload_ratio", "first"))
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(r["t"] + s["win_s"] / 2, r["ratio"], color=MEAN_COLOR, lw=2, marker="o", ms=3)
    ax.set(xlabel="Time (s, window centre)", ylabel="ratio")
    ax.set_title(f"Workload index: {nc} {nb} / {dc} {db}")
    fig.tight_layout()
    return fig


def label_segments(feats, segments, win_s):
    """condition = segment name for windows lying fully inside it, else None
    (windows straddling a switch belong to neither condition)."""
    feats = feats.copy()
    feats["condition"] = None
    for name, a, b in segments:
        inside = (feats["t_start_s"] >= a - 1e-9) & (feats["t_start_s"] + win_s <= b + 1e-9)
        feats.loc[inside, "condition"] = name
    return feats


COMPARE_COLS = ["log_theta_abs", "log_alpha_abs", "log_beta_abs",
                "theta_rel", "alpha_rel", "beta_rel", "blink_rate_per_min", "clean_frac"]


def compare_conditions(feats, s, names):
    """Condition A vs B per channel and feature. Stats use non-overlapping
    windows only (every win_s/step_s-th), since 50% overlap double counts data.
    Also reports theta's trend over time within each condition: if theta
    drifts within a block, a between-block difference may just be drift."""
    from scipy.stats import mannwhitneyu, spearmanr

    a_name, b_name = names
    t0 = feats["t_start_s"].min()
    k = int(round(s["win_s"] / s["step_s"]))
    nonover = (np.round((feats["t_start_s"] - t0) / s["step_s"]) % k) == 0
    rows = []
    for ch, g in feats[nonover].groupby("channel", sort=False):
        A, B = g[g.condition == a_name], g[g.condition == b_name]
        for col in [c for c in COMPARE_COLS if c in g]:
            ma, mb = A[col].median(), B[col].median()
            row = {"channel": ch, "feature": col, f"{a_name}_median": ma,
                   f"{b_name}_median": mb, f"n_{a_name}": len(A), f"n_{b_name}": len(B)}
            # power features: express the change as % of linear power
            row["change"] = (f"{(10 ** (mb - ma) - 1) * 100:+.0f}%" if col.startswith("log_")
                             else f"{mb - ma:+.3g}")
            row["p_mannwhitney"] = (mannwhitneyu(A[col], B[col]).pvalue
                                    if len(A) >= 3 and len(B) >= 3 else np.nan)
            if col == "log_theta_abs":
                for nm, G in [(a_name, A), (b_name, B)]:
                    row[f"theta_trend_rho_{nm}"] = (spearmanr(G["t_start_s"], G[col])[0]
                                                    if len(G) >= 4 else np.nan)
            rows.append(row)
    return pd.DataFrame(rows)


def plot_conditions(feats, names):
    """Per-window values by condition: theta (the measure), beta (forehead
    muscle check) and blink rate (eye check). Dots = windows, bar = median."""
    panels = [("log_theta_abs", "log10 theta power (V²)"),
              ("log_beta_abs", "log10 beta power (V²), muscle check"),
              ("blink_rate_per_min", "blinks / min, eye check")]
    panels = [p for p in panels if p[0] in feats]
    chans = list(dict.fromkeys(feats["channel"]))
    rng = np.random.default_rng(0)
    fig, axs = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 4.2))
    for ax, (col, label) in zip(np.atleast_1d(axs), panels):
        for i, ch in enumerate(chans):
            for j, (nm, c) in enumerate(zip(names, BAND_COLORS)):
                v = feats.loc[(feats.channel == ch) & (feats.condition == nm), col]
                x = i + (j - 0.5) * 0.4
                ax.scatter(x + rng.uniform(-0.08, 0.08, len(v)), v, s=14, color=c,
                           alpha=0.6, lw=0, label=nm if i == 0 else None)
                ax.hlines(v.median(), x - 0.15, x + 0.15, color=INK, lw=2)
        ax.set_xticks(range(len(chans)), chans)
        ax.grid(axis="x", visible=False)
        ax.set_title(label, fontsize=10)
    np.atleast_1d(axs)[0].legend(frameon=False, loc="best")
    fig.suptitle(f"{names[0]} vs {names[1]} (bars = median)", fontweight="bold")
    fig.tight_layout()
    return fig


def plot_topomaps(epochs, s):
    """Mean log band power on the scalp (needs real 10-20 positions, >= 4 channels)."""
    sf = epochs.info["sfreq"]
    psd = epochs.compute_psd(method="welch", fmin=s["total_band"][0], fmax=s["total_band"][1],
                             n_fft=int(sf * 2), n_per_seg=int(sf * 2))
    fig = psd.plot_topomap(bands={k: v for k, v in s["bands"].items()},
                           normalize=True, cmap="Blues", show=False)
    fig.suptitle("Relative band power on the scalp")
    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--board", type=int, default=0,
                    help="BrainFlow board id (0 Cyton, 1 Ganglion, 2 Cyton+Daisy, -1 synthetic)")
    ap.add_argument("--sfreq", type=float, help="override sampling rate (Hz)")
    ap.add_argument("--eeg-cols", nargs="+",
                    help="EEG column indices (0-based) or header names; overrides --board")
    ap.add_argument("--ch-names", nargs="+", help="10-20 names in column order, e.g. Fz Cz Pz Oz")
    ap.add_argument("--marker-col", help="column with event markers (nonzero = event)")
    ap.add_argument("--crop", nargs=2, type=float, metavar=("TMIN", "TMAX"),
                    help="keep only this time range in seconds (e.g. drop settling at start)")
    ap.add_argument("--notch", type=float, default=60.0, help="line noise Hz (50 or 60; 0 = off)")
    ap.add_argument("--ica", action="store_true", help="run EOG ICA (needs >= 8 ch incl. Fp1/Fp2)")
    ap.add_argument("--blinks", choices=["auto", "on", "off"], default="auto",
                    help="mask blinks and use clean segments only "
                         "(auto: when Fp1/Fp2 are present and ICA didn't run)")
    ap.add_argument("--ptp", type=float, default=BLINK["ptp_uv"],
                    help="blink mode: drop 1 s segments with peak-to-peak above this µV")
    ap.add_argument("--condition", help="label stored in the feature table")
    ap.add_argument("--segments", nargs="+", metavar="NAME:START-END",
                    help="label time ranges (recording s) as conditions, e.g. "
                         "easy:2-70 hard:80-157; windows outside or straddling are unlabelled. "
                         "The first two are compared.")
    ap.add_argument("--out", help="output dir (default results/<csv name>)")
    ap.add_argument("--show", action="store_true", help="open figure windows")
    args = ap.parse_args()

    eeg_cols = None
    if args.eeg_cols:
        eeg_cols = [int(c) if c.lstrip("-").isdigit() else c for c in args.eeg_cols]
    marker_col = None
    if args.marker_col is not None:
        marker_col = int(args.marker_col) if args.marker_col.isdigit() else args.marker_col

    raw = load_brainflow_csv(
        args.csv, board_id=None if eeg_cols and args.sfreq else args.board,
        sfreq=args.sfreq, eeg_cols=eeg_cols, ch_names=args.ch_names, marker_col=marker_col,
    )
    if args.crop:
        raw.crop(*args.crop)

    # Device data: keep native rate, user's notch, ICA off unless asked
    BLINK["ptp_uv"] = args.ptp
    s = preproc.get_settings(resample_hz=None, notch_hz=args.notch or None, use_ica=args.ica)
    clean, epochs, feats, log = preprocess(raw, s, condition=args.condition,
                                           blinks=args.blinks)

    out = Path(args.out or Path(__file__).parent / "results" / Path(args.csv).stem)
    out.mkdir(parents=True, exist_ok=True)
    segments = []
    for seg in args.segments or []:
        name, rng_ = seg.split(":")
        a, b = map(float, rng_.split("-"))
        segments.append((name, a, b))
    comparison = None
    if segments:
        feats = label_segments(feats, segments, s["win_s"])
        if len(segments) >= 2:
            names = [segments[0][0], segments[1][0]]
            comparison = compare_conditions(feats, s, names)
            comparison.to_csv(out / "condition_comparison.csv", index=False)
    feats.to_csv(out / "features.csv", index=False)

    rel = [f"{b}_rel" for b in s["bands"]]
    logs = [f"log_{b}_abs" for b in s["bands"]]
    summary = feats.groupby("channel", sort=False)[rel + logs].mean()
    summary.to_csv(out / "summary.csv")
    (out / "log.json").write_text(json.dumps(log, indent=2, default=str))

    figs = {
        "psd.png": plot_psd(epochs, s, f"Power spectrum: {Path(args.csv).name}"),
        "band_power_by_channel.png": plot_band_bars(feats, s),
        "band_power_over_time.png": plot_band_time(
            feats, s, clean.annotations if len(clean.annotations) else None, segments),
    }
    if comparison is not None:
        figs["conditions.png"] = plot_conditions(feats, names)
    if feats["workload_ratio"].notna().any():
        figs["workload_ratio.png"] = plot_ratio_time(feats, s)
    if log["has_positions"] and len(epochs.ch_names) >= 4:
        figs["topomap.png"] = plot_topomaps(epochs, s)
    for name, fig in figs.items():
        fig.savefig(out / name, bbox_inches="tight")

    print(json.dumps(log, indent=2, default=str))
    print("\nMean per channel:")
    print((summary[rel] * 100).round(1).rename(columns=lambda c: c.replace("_rel", " %")))
    if comparison is not None:
        print(f"\n{names[0]} vs {names[1]} (non-overlapping windows):")
        show = comparison.drop(columns=[c for c in comparison if c.startswith("theta_trend")])
        print(show.to_string(index=False, float_format=lambda v: f"{v:.3g}"))
        th = comparison[comparison.feature == "log_theta_abs"]
        print("\ntheta trend within block (Spearman rho vs time):")
        print(th[["channel"] + [c for c in th if c.startswith("theta_trend")]]
              .to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"\nSaved to {out}")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
