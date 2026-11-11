# EEG

EEG workload pipeline for the capstone. There are three parts, plus an archive:

| Folder | What it is | Reuse it when… |
| --- | --- | --- |
| [`pipeline/`](pipeline/) | **The preprocessing pipeline** (`preproc.py`): filter, find bad channels, re-reference, remove EOG with ICA, split into windows, compute band-power features. One `SETTINGS` dict controls every stage. | you need to clean EEG and extract features from any dataset. Every other folder imports this. |
| [`feasibility_eegbci/`](feasibility_eegbci/) | **MNE feasibility test**: runs the pipeline on the public EEGBCI eyes-open vs eyes-closed baselines to check that it picks up the known alpha increase. | you change `preproc.py` and want to confirm it still measures real brain rhythms (regression check). |
| [`validation_cyton_brainflow/`](validation_cyton_brainflow/) | **OpenBCI Cyton / BrainFlow validation**: loads BrainFlow CSV recordings from our own board, adapts the pipeline for a 2-channel frontal montage (blink masking in place of ICA), and plots spectral features and condition comparisons. | you have a new recording from the board. |
| [`archive/`](archive/) | First MNE learning notebook from September. Not used by anything. | (reference only) |

```
eeg/
├── requirements.txt
├── pipeline/
│   └── preproc.py                  ← shared pipeline
├── feasibility_eegbci/
│   ├── 01_eegbci_sanity.ipynb      ← feasibility test
│   ├── eeg_data/                   ← EEGBCI EDF files (auto-downloaded by MNE)
│   └── results/                    ← features, logs, QC + result plots
├── validation_cyton_brainflow/
│   ├── brainflow_spectral.py       ← CLI: BrainFlow CSV → features + plots
│   ├── recordings/                 ← raw BrainFlow CSVs from the Cyton
│   └── results/<recording name>/   ← one output folder per recording
└── archive/
    └── mnepreproc.ipynb
```

## Setup

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`brainflow` is optional. Without it, `brainflow_spectral.py` falls back to built-in board defaults.

## How the parts fit together

```
             pipeline/preproc.py
            (standardize → filter → bad channels → reference → ICA → windows → features)
                 ▲                                   ▲
   imports all stages                       imports individual stages,
                 │                          skips ones a 2-ch montage can't support
feasibility_eegbci/01_eegbci_sanity.ipynb   validation_cyton_brainflow/brainflow_spectral.py
   (64-ch public data, known effect)            (our board, our recordings)
```

Both consumers add `../pipeline` to `sys.path` and `import preproc`, so keep the folder layout as-is or update those two lines if you move things.
