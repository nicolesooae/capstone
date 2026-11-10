# Included reference data

The original project public reference datasets and saved analysis results are
included. These are reference recordings, not measurements from the Gravity SEN0518.

- PhysioNet Pulse Transit Time PPG Dataset v1.1.0, Mehrgardt et al. (2022):
  https://physionet.org/content/pulse-transit-time-ppg/1.1.0/
  DOI: https://doi.org/10.13026/jpan-6n92
  The published license is in `PhysioNet_PPG/data/LICENSE.txt`; the published
  RECORDS and SHA256SUMS.txt manifests accompany the cached WFDB files.
- Multisite MAX30102 recordings used in the forehead experiments:
  https://github.com/sanvsquezsz/PPG-based-BGL-assessment
  Preparation provenance is in `HALO_Real_PPG_Dataset/download_and_prepare.py`.

See README.md for analysis methods and limitations, and USE_GRAVITY.md for
recording processed HR and SpO2 with the actual SEN0518 hardware. Its documented
interface does not provide the individual pulse intervals needed for PRV.
