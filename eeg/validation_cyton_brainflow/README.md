# Cyton board validation (BrainFlow recordings)

**Question:** can we get usable spectral features out of our own OpenBCI Cyton recordings, recorded with BrainFlow, using the shared pipeline?

`brainflow_spectral.py` is a command-line tool that loads a BrainFlow CSV and runs it through [`pipeline/preproc.py`](../pipeline/). It then saves features, a summary, a log and plots. It can also compare two labelled time segments (e.g. easy vs hard task).

## What's different from the main pipeline

Our montage is small and frontal (currently Fp1/Fp2), so some pipeline stages can't run. The script skips or replaces them automatically:

| Stage | Main pipeline | Here, with < 8 channels |
| --- | --- | --- |
| Resample | 160 Hz | native rate (250 Hz for the Cyton) |
| Bad channels | flat + variance outlier | flat only. With 2 channels, the variance z-score would flag real signal |
| Reference | average | as recorded (Cyton SRB / earlobe) |
| EOG removal | ICA | **blink masking** (below). ICA is still available with `--ica` if there are ≥ 8 channels including Fp1/Fp2 |
| Windows | 4 s, PTP auto-reject | 4 s windows built from clean 1 s segments |

**Blink masking** (`--blinks auto`, used whenever Fp1/Fp2 are present and ICA didn't run). Blinks at Fp1/Fp2 are 100–500 µV and mostly 1–8 Hz, which sits right on top of theta. They also happen every 1–3 s, so almost every 4 s window contains one. Dropping whole windows would therefore lose most of the data. Instead the script:
1. detects blinks with an absolute threshold of 100 µV on the 1–10 Hz filtered mean of Fp1/Fp2,
2. marks each blink from 0.25 s before the peak to 0.45 s after as `BAD_blink`,
3. cuts the data into 1 s segments (0.5 s step) and drops any that overlap a blink or exceed 100 µV peak-to-peak,
4. averages the PSDs of the clean segments inside each 4 s window. A window needs ≥ 1.5 s of clean data, otherwise it is skipped.

The fixed 100 µV limit matters. On the 2026-10-29 test, the data-driven `"auto"` limit left theta tracking blink count (r = 0.73). Fixed limits from 75 to 150 µV all gave stable theta that was uncorrelated with blinks. Tunables are in the `BLINK` dict at the top of the script.

## How to run

```sh
cd eeg/validation_cyton_brainflow

# basic: Cyton, all 8 channels
python brainflow_spectral.py recordings/<file>.csv --board 0 \
    --ch-names Fp1 Fp2 C3 C4 P7 P8 O1 O2

# only some columns wired up: give columns + sample rate explicitly
python brainflow_spectral.py recordings/<file>.csv --sfreq 250 --eeg-cols 2 7 \
    --ch-names Fp1 Fp2

# label two task blocks (recording seconds) and compare them
python brainflow_spectral.py recordings/<file>.csv --sfreq 250 --eeg-cols 2 7 \
    --ch-names Fp1 Fp2 --segments easy:2-70 hard:80-157
```

Useful flags (`--help` lists all of them):

| Flag | Meaning |
| --- | --- |
| `--board` | BrainFlow board id: 0 Cyton (default), 1 Ganglion, 2 Cyton+Daisy, -1 synthetic |
| `--sfreq`, `--eeg-cols` | override the board defaults. Columns are 0-based indices into the CSV |
| `--ch-names` | 10-20 names in column order. Real names enable montage positions |
| `--crop TMIN TMAX` | keep only this time range, e.g. to drop settling time at the start |
| `--notch` | line noise frequency, 60 (default) or 50; 0 turns it off |
| `--blinks auto/on/off`, `--ptp` | blink masking, and the per-segment peak-to-peak limit in µV |
| `--segments NAME:START-END …` | label conditions. The first two are compared; windows that straddle a boundary belong to neither |
| `--out` | output dir (default `results/<csv name>/`) |

**BrainFlow CSV format:** `DataFilter.write_file()` writes tab-separated rows with no header, one row per sample. The columns are package number, EEG (µV), accelerometer, …, timestamp and marker. The loader handles headers, comma separators, all-zero start-up packets, NaN dropouts and a truncated last line (from copying a file while it's still being written). Nonzero values in the marker column become annotations.

## Outputs (`results/<recording name>/`)

| File | Contents |
| --- | --- |
| `features.csv` | Same columns as the main pipeline, plus `clean_frac` and `blink_rate_per_min` per window |
| `summary.csv` | Mean relative and log band power per channel |
| `log.json` | Channels, bad channels, reference used, blink count/rate, clean-time fraction, windows kept/skipped |
| `psd.png` | Welch PSD per channel and the channel mean, with bands shaded |
| `band_power_by_channel.png`, `band_power_over_time.png` | Relative power per channel, and over time with segment shading |
| `condition_comparison.csv`, `conditions.png` | only with `--segments`: median per condition, % change, Mann-Whitney p on non-overlapping windows, within-block theta trend |
| `workload_ratio.png`, `topomap.png` | only when Fz+Pz are present, or ≥ 4 positioned channels |

## Recordings so far (2026-10-29, Fp1/Fp2, 250 Hz)

| Recording | Length | Blinks/min | Clean time | Windows kept | Notes |
| --- | --- | --- | --- | --- | --- |
| `…16-11-58_2` | 162 s | 49.6 | 23% | 29 / 80 | Very blink-heavy; no conditions labelled |
| `…16-11-58_3` | 155 s | 18.6 | 62% | 66 / 76 | `easy` ≈ 2–70 s, `hard` ≈ 80–157 s |

In `_3`, frontal theta was higher in the hard block: +29% at Fp1 (p = 0.15) and +49% at Fp2 (p = 0.08), with 15 non-overlapping windows per block. Neither is significant. The hard block also had more blinks and less clean data, so the eye artifact is a possible confound. Check `theta_trend_rho_*` in `condition_comparison.csv` for drift within each block.

> The exact commands for these runs weren't saved. The segment times above come from the feature tables. Columns 2 and 7 are the only Cyton EEG columns with signal in these files (3 and 4 are railed), but confirm which one is Fp1 against the board setup. **Save the command in this table with each new recording.**
