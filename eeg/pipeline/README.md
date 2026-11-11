# Preprocessing pipeline (`preproc.py`)

The main EEG preprocessing and feature-extraction pipeline. It is a single module with one function per stage. All stages read from one `SETTINGS` dict, so the same code runs on EEGBCI, a workload dataset (e.g. n-back), and our own device recordings.

Assumption for now: the signal is fully acquired, with no dropouts.

## Stages

| Step | Function | What it does | Key settings |
| --- | --- | --- | --- |
| 2 | `load_eegbci` | Load one EEGBCI run (R01 eyes open, R02 eyes closed) and standardize channel names | `path` arg |
| 2 | `standardize` | Apply the montage, resample, work on a copy | `montage`, `resample_hz` |
| 3 | `filter_raw` | Notch filter, then band-pass filter | `notch_hz`, `l_freq`, `h_freq` |
| 4 | `find_bad_channels` / `fix_bad_channels` | Flag flat channels (std < `flat_uv`) and channels whose log-variance is an outlier (robust z). `eog_proxy` channels are excluded from the variance check so ICA still has them. Interpolate the flagged channels. | `flat_uv`, `bad_z_thresh` |
| 5 | `rereference` | Average reference, or a list of channels | `reference` |
| 6 | `remove_eog_ica` | FastICA on a 1 Hz high-passed copy; drop components that correlate with Fp1/Fp2 | `use_ica`, `ica_n_components`, `eog_proxy` |
| 7 | `make_windows` | Fixed-length overlapping windows; drop windows with outlying peak-to-peak (`"auto"` = median + k·robust SD per channel, or a fixed µV value) | `win_s`, `step_s`, `reject_ptp_uv`, `reject_auto_k` |
| 7 | `band_power_features` | Welch PSD per window gives absolute, log and relative theta/alpha/beta power, plus the workload ratio (frontal theta / parietal alpha) | `bands`, `total_band`, `ratio_num`, `ratio_den` |
| 8 | `normalize_to_baseline` | z-score each subject's features against that subject's baseline condition, per channel (adds `<col>_z` columns) | — |
| 11 | (in `run_pipeline`) | Optionally keep only the device's channel subset | `channels` |

`run_pipeline(raw, s, subject, condition)` runs steps 2–7 in order and returns `(clean_raw, features_df, log_dict)`. It never modifies the input `raw`.

## Usage

```python
import sys
sys.path.insert(0, "path/to/eeg/pipeline")
import preproc

s = preproc.get_settings(win_s=2, use_ica=False)    # copy of SETTINGS with overrides
raw = preproc.load_eegbci(subject=1, run=1, path="path/to/eeg_data/")   # or any mne.io.Raw
clean, feats, log = preproc.run_pipeline(raw, s, subject=1, condition="eyes_open")

# after concatenating several conditions:
feats = preproc.normalize_to_baseline(feats, baseline_condition="eyes_open")
```

To use your own data, pass any `mne.io.Raw` (in volts, with 10-20 channel names) to `run_pipeline`. `load_eegbci` is only a convenience loader.

**Rules for changing settings:** change `SETTINGS` or pass overrides through `get_settings(...)`, not the functions. `get_settings` raises on an unknown key, so typos fail loudly.

## Output columns (`features_df`)

One row per window × channel:
`subject, condition, window, t_start_s, channel, {theta,alpha,beta}_abs, {…}_rel, log_{…}_abs, workload_ratio`

`t_start_s` is the time in the recording, so dropped windows leave gaps instead of shifting later windows. `workload_ratio` is the same for every channel in a window, and is NaN if `Fz` or `Pz` is missing.

## Caveats

- **Few channels.** An average reference and ICA both need decent head coverage. For a reduced montage, set `reference` to the device's reference and `use_ica=False`. [`validation_cyton_brainflow/`](../validation_cyton_brainflow/) shows how to do this, with blink masking in place of ICA.
- **Bad-channel detection** uses variance over the whole recording. That is fine for short EEGBCI runs but less robust for long recordings with transient artifacts (marked `CHANGE LATER` in the code).
- **The notch filter** is redundant with the 40 Hz low-pass at the moment. It is kept so the config still works if `h_freq` is raised.

## After changing this file

Re-run [`feasibility_eegbci/01_eegbci_sanity.ipynb`](../feasibility_eegbci/) and check the pass criteria still hold.
