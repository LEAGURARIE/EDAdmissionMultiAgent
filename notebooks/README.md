# Notebooks

`ED_Admission_Colab.ipynb` — the one notebook. It clones the private repository with a token from the Colab secrets,
installs the project (`requirements.txt`, then `pip install -e .`), checks it, runs the system end to end — the baseline included —
and measures one agent alone. Results (the run history) are kept in Google Drive.

Code is not written inside the notebook: it lives in the repository, and the notebook only installs and runs it.
