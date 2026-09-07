




some important data structures and tools im discovering:


mne.io.Raw
core data struct used to represent cont, multi-channel eeg data. houses 2d numpy array of raw data samples and its metadata structure (mne.Info) --> channel names, types, sampling rates, hardware configs, etc

mne.io.read_raw_xyz
where xyz can be edf, brainvision vhdr, eeglab set, fif - our used funtion will depend on which format our hardware saves our recordings too (prob edf or fif)

