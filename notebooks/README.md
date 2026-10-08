# Notebooks

`ED_Admission_Colab.ipynb` — the one notebook, for the lecturer and the team. It clones the private repository with
a token from the Colab secrets, installs the project (`requirements.txt`, then `pip install -e .`), checks it, runs
the system end to end, and — for the team — opens your agent, measures it, and sends your files to a branch of your
own for a pull request. Results (the run history) are kept in your Google Drive.

Code is not written inside the notebook: it lives in the repository, and the notebook only installs and runs it.
