# Multi-Agent System for Placing Admitted Emergency Department Patients

A system that runs in a simulated hospital. When a patient is admitted, it goes over the medical record and recommends the most suitable ward, for the shift manager's approval.

## Documents

- The charter: `CHARTER.md` (at the project root)
- The agents guide: `docs/reference/Agents_Guide.pdf`
- The decisions in force: `DECISIONS.md`
- What is done and what is left: `PROJECT.md`

## Layout

| Folder | What it holds |
|---|---|
| `src/ed_admission/agents` | A file for each agent |
| `src/ed_admission/prompts` | A prompt file for each agent |
| `src/ed_admission/tools` | The tools the agents use, and their permissions |
| `src/ed_admission/environment` | The simulated hospital and the simulation engine |
| `src/ed_admission/contracts` | The contracts: the record, and the request and answer in the negotiation with the wards |
| `src/ed_admission/orchestration` | The stages of a placement case, and an example wiring in code |
| `src/ed_admission/observability` | The trace of every case |
| `src/ed_admission/baselines` | The three baselines: first free bed, rules only, simulated coordinator |
| `src/ed_admission/evaluation` | The runner, the building of the expected answers, and the metrics |
| `config` | Settings: wards and beds, cleaning, hold and approval times, and the clinical rules |
| `data/records` | The 74 synthetic records |
| `data/templates` | 22 ward-state templates |
| `data/answer_key` | The expected answers, for the evaluation code only |
| `data/scenarios` | Scripted days |
| `scripts` | Running: cases, metrics, a scripted day, a health check |
| `tests` | The tests |
| `docs` | Design and reference documents |

**The data is in Hebrew.** The synthetic emergency department records are written in Hebrew with English medical terms, like real notes in an Israeli emergency department. The templates, scenarios and answers keep the same language.

## Working in Colab

Open [`notebooks/ED_Admission_Colab.ipynb`](https://colab.research.google.com/github/LEAGURARIE/EDAdmissionMultiAgent/blob/main/notebooks/ED_Admission_Colab.ipynb)
(Colab asks to connect to GitHub, since the repository is private). Once, before the first run:

1. **A GitHub token** — GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens*;
   repository access: only this repository; permissions: *Contents: Read and write*.
2. **Colab secrets** — the key icon on the left: `GITHUB_TOKEN` and, to run the agents, `ANTHROPIC_API_KEY`;
   turn on *Notebook access*. Never paste a token or key into a cell.

Then run the cells in order: steps 1–5 connect and run the system end to end; 6–8 open your agent, measure it,
and send your files to a branch of your own for a pull request. Colab forgets everything when the session ends —
save with step 8 before you close.

## Working locally

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
.venv\Scripts\python -m pytest -q
```

The Anthropic key goes in a local `.env` file (copy `.env.example`); it is never uploaded.

Every measured run is added to the run history in `outputs/` — `runs.csv` (one row per run) and `admissions.csv`
(one row per patient) — so improvement can be followed run after run. Results are never uploaded.

## How the team works

- Each team member has their own files.
- Code is not written inside a notebook. The Colab notebook installs the project from the repository and imports from it.
- Handed to the integrator: a code file that meets the contract, the prompt file, and results of a run on the scripted days.
- Secrets never go into the code or the notebook. Keep them in Colab Secrets or in a local `.env` file.
