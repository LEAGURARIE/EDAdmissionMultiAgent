# Multi-Agent System for Placing Admitted Emergency Department Patients

A system that runs in a simulated hospital. When a patient is admitted, it reads the medical record and recommends the
safest, most suitable bed in the shortest time, for the shift manager's approval.

**Two agents and a Coordinator**, each seeing what the others do not: the **Patient Agent** reads the record; a
**Department Agent** for each ward knows what the ward can really give; the **Coordinator** sees every ward's report
and both answers, and decides where the patient goes. Ranking and the hard safety rules are code.

## Documents

- The charter: `CHARTER.md`
- The decisions in force: `DECISIONS.md`
- The agents, their tools, and how they are run and measured: `docs/design/agent_interfaces.md`

## The baseline — the Code Coordinator

**What is measured:** 118 placement decisions with clinically approved, frozen expected answers (104 single cases and
7 cases of two patients for one bed), and 3 scripted days with 39 admissions. All the records are synthetic.

**The baseline is the Code Coordinator: today's procedure, written as code, with no agents.** For every admitted
patient it:

1. reads the record's structured fields — vital signs, chief complaint, the ward the physician requested;
2. looks at every ward's report;
3. phones the wards, one after the other, to ask what they can really give — 5 minutes a call;
4. ranks the beds by the rules and decides.

It does **not** read the physician's free text. The system is judged against it.

**The results** (measured 2026-10-10):

| | The question | Code Coordinator | Target |
|---|---|---|---|
| **Safe** | In how many decisions did the patient get an unsafe bed? (a hard rule broken — e.g. a patient who needs observation in a regular bed) | 45 of 118 (38%) | 0 (0%) |
| | Of the 56 patients who need observation or isolation, how many got it or were escalated? | 18 of 56 (32%) | 56 of 56 (100%) |
| | How many times was a bed chosen that the ward cannot really give? (it looks free in the report, but there is no nurse, or it is broken or promised) | 0 of 100 placements (0%) | 0 (0%) |
| **Suitable** | In how many decisions was the answer exactly the expected one? | 45 of 118 (38%) | at least 100 (85%) |
| **Fast** | How many minutes, on average, from the admission decision to a bed? | 26 | fewer than the baseline |

**What the numbers say:**

- **It is not safe.** In 45 of 118 decisions (38%) the patient gets an unsafe bed, because nobody reads the physician's
  free text, where an isolation need or a hidden severity is written. That is the Patient Agent's job.
- **It never takes a bed the ward cannot give** — because it phones the wards. But every call costs 5 minutes, and
  it does not understand a ward's conditions. That is the Department Agent's job: the same answer, without the phone.
- **It is right in 45 of 118 decisions (38%)**, and a patient reaches a bed after 26 minutes on average.

The system has to be safe, right **and** faster than the baseline.

```
.venv\Scripts\python scripts\run_baseline.py                                  the results above
.venv\Scripts\python scripts\run_metrics.py --decider code_coordinator --details    in full
.venv\Scripts\python scripts\run_scenario.py data\scenarios\busy_day.yaml --policy code_coordinator --display
```

## Layout

| Folder | What it holds |
|---|---|
| `src/ed_admission/agents` | The Patient Agent, the Department Agent and the Coordinator — a file each; entry points only, the agents are not built yet |
| `src/ed_admission/prompts` | A prompt file for each agent |
| `src/ed_admission/tools` | The tools the agents use, their permissions, and `decide` — the hard rules in code |
| `src/ed_admission/environment` | The simulated hospital and the simulation engine |
| `src/ed_admission/contracts` | The contracts: the record, the Patient Agent's assessment, the request and answer in the negotiation with the wards |
| `src/ed_admission/orchestration` | The stages of a placement case, the Coordinator's flow in code, and a code example of each agent |
| `src/ed_admission/observability` | The trace of every case |
| `src/ed_admission/baselines` | The baseline: the Code Coordinator — today's procedure, written as code |
| `src/ed_admission/evaluation` | The runner, the building of the expected answers, the metrics and the report |
| `src/ed_admission/database` | The run history: `runs.csv` and `admissions.csv` |
| `config` | Settings: wards and beds, cleaning, hold and approval times, and the clinical rules |
| `data/records` | The 74 synthetic records |
| `data/templates` | 22 ward-state templates |
| `data/answer_key` | The expected answers, for the evaluation code only |
| `data/scenarios` | 3 scripted days |
| `scripts` | Running: the baseline, the metrics, one agent alone, cases, a scripted day, a health check |
| `notebooks` | The Colab notebook |
| `tests` | The tests |
| `docs/design` | Design documents |

**The data is in Hebrew.** The synthetic emergency department records are written in Hebrew with English medical terms, like real notes in an Israeli emergency department. The templates, scenarios and answers keep the same language.

## Working in Colab

Open [`notebooks/ED_Admission_Colab.ipynb`](https://colab.research.google.com/github/LEAGURARIE/EDAdmissionMultiAgent/blob/main/notebooks/ED_Admission_Colab.ipynb)
(Colab asks to connect to GitHub, since the repository is private). Once, before the first run:

1. **A GitHub token** — GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens*;
   repository access: only this repository; permissions: *Contents: Read-only*.
2. **Colab secrets** — the key icon on the left: `GITHUB_TOKEN` and, to run the agents, `ANTHROPIC_API_KEY`;
   turn on *Notebook access*. Never paste a token or key into a cell.

Then run the cells in order: steps 1–5 connect and run the system end to end — the baseline table included;
steps 6–7 measure one agent alone and show the run history. Colab forgets everything when the session ends; the
run history is kept in Google Drive.

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

## How the project is kept

- Each agent has its own code file and its own prompt file.
- Code is not written inside a notebook. The Colab notebook installs the project from the repository and imports from it.
- Secrets never go into the code or the notebook. They are kept in Colab Secrets or in a local `.env` file.
