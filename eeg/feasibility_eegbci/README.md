# Feasibility test: EEGBCI eyes open vs eyes closed

**Question:** does the pipeline measure real brain rhythms rather than artifacts of our own processing?

**Test:** closing your eyes produces a large, well-established increase in alpha power (8–13 Hz), strongest over occipital sites. We run [`pipeline/preproc.py`](../pipeline/) on the public [EEGBCI / PhysioNet eegmmidb](https://physionet.org/content/eegmmidb/1.0.0/) baseline runs (R01 eyes open, R02 eyes closed; 64 channels, 160 Hz, 10-10 placement). If the pipeline can't see this effect, it can't be trusted for anything subtler.

## Pass criteria (fixed before running, also recorded in Form B2)

1. Relative alpha at Oz is higher with eyes closed than eyes open in ≥ 90% of subjects.
2. Paired effect size (Cohen's d_z) on log alpha at Oz is ≥ 0.8.
3. Cleaning keeps ≥ 80% of windows on average, i.e. rejection isn't throwing the data away.

## Latest result (subjects 1–4)

| Measure | Result | Target | Pass |
| --- | --- | --- | --- |
| Subjects with alpha increase | 100% | ≥ 90% | ✅ |
| Cohen's d_z (log alpha) | 4.25 | ≥ 0.8 | ✅ |
| Windows kept | 79.3% | ≥ 80% | ❌ (just under) |

n = 4 is too small for a significance test (the notebook only runs Wilcoxon when n ≥ 6). Increase `SUBJECTS` before reporting these numbers.

## How to run

```sh
cd eeg/feasibility_eegbci
jupyter notebook 01_eegbci_sanity.ipynb      # run all cells
```

The notebook must be run from this folder. It adds `../pipeline` to `sys.path`, reads data from `./eeg_data/`, and writes to `./results/`. Any subjects not already in `eeg_data/` are downloaded from PhysioNet automatically.

Settings are at the top of the notebook:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUBJECTS` | `range(1, 5)` | EEGBCI has 109 subjects; use `range(1, 11)` or more for a real test |
| `CHANNEL` | `"Oz"` | channel where the effect is measured |
| `QC_SUBJECT` | `1` | subject shown in the QC plots |
| `s = preproc.get_settings(...)` | defaults | pipeline overrides, e.g. `win_s=2` |

The notebook only calls `preproc.py`; all processing logic lives there.

## Outputs (`results/`)

| File | Contents |
| --- | --- |
| `features.csv` | Feature table for every subject × condition × window × channel |
| `pipeline_log.csv` | Per run: bad channels, ICA components removed, windows kept/dropped |
| `qc_overlay.png` | Fp1 and Oz before and after cleaning (blinks removed, alpha preserved) |
| `qc_psd.png` | Oz spectrum, eyes open vs closed. Expect an 8–13 Hz peak with eyes closed |
| `result_within_subject.png` | Per-window alpha, stability over time, and a scalp map of the change (expect occipital) |
| `result_paired.png` | One line per subject, eyes open → eyes closed |
| `summary.csv` | Pass/fail table above |

## Data (`eeg_data/`)

This folder uses MNE's EEGBCI cache layout (`MNE-eegbci-data/files/eegmmidb/1.0.0/S00X/S00XR0Y.edf`). Subjects 1–4, runs 1–2 are checked in. Don't rename the subfolders, because `mne.datasets.eegbci.load_data` expects this layout.
