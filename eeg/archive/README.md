# Archive

`mnepreproc.ipynb` is the first-pass MNE learning notebook from September 2026. It loads EEGBCI subject 1, plots the raw signal and PSD, and tries basic filtering. It was superseded by [`pipeline/preproc.py`](../pipeline/), and nothing depends on it.

It expects to be run from a folder that contains `eeg_data/`, so it won't run from here as-is. To re-run it, point `custom_path` at `../feasibility_eegbci/eeg_data/`.
