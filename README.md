# Multi-Agent System for Placing Admitted Emergency Department Patients

A system that runs in a simulated hospital. When a patient is admitted, it reads the medical record and recommends the
safest, most suitable bed in the shortest time, for the shift manager's approval.

**A multi-agent system**, each agent seeing what the others do not: the **Patient Agent** reads the record; the
**Department Agent** looks for a bed one ward at a time, reading that ward's report and private notes; the **Judge**
checks a proposed placement against a rubric; the **Coordinator** activates them and decides. Ranking and the hard
safety rules are code. It is compared with a **single agent** that does every agent's job — built and measured — and
with the baseline: today's procedure, written as code.

## Documents

- The charter: `CHARTER.md`
- The decisions in force: `DECISIONS.md`
- Every data file and every field in it: `data/data_dictionary.md`
- The agents, their tools, and how they are run and measured: `docs/design/agent_interfaces.md`

## The baseline — the Code Coordinator

**What is measured:** 118 placement decisions with clinically approved, frozen expected answers (104 single cases and
7 cases of two patients for one bed), and 4 scripted days: three with 39 admissions, for the time to a bed, and a
fourth with three moments when two patients compete for one bed. All the records are synthetic.

**The baseline is the Code Coordinator: today's procedure, written as code, with no agents.** For every admitted
patient it:

1. reads the record's structured fields — vital signs, chief complaint, the ward the physician requested;
2. looks at every ward's report;
3. phones the wards, one after the other, to ask what they can really give — 5 minutes a call;
4. ranks the beds by the rules and decides.

It does **not** read the physician's free text. The system is judged against it.

**The results** (measured 2026-10-10; re-measured 2026-10-15 — the same, except the match: when the record names
neither internal medicine A nor B, the decision is now scored by the occupancy rule in `DECISIONS.md`):

| | The question | Code Coordinator | Target |
|---|---|---|---|
| **Safe** | In how many decisions did the patient get an unsafe bed? (a hard rule broken — e.g. a patient who needs observation in a regular bed) | 45 of 118 (38%) | 0 (0%) |
| | Of the 56 patients who need observation or isolation, how many got it or were escalated? | 18 of 56 (32%) | 56 of 56 (100%) |
| | How many times was a bed chosen that the ward cannot really give? (it looks free in the report, but there is no nurse, or it is broken or promised) | 0 of 100 placements (0%) | 0 (0%) |
| **Suitable** | In how many decisions was the answer exactly the expected one? | 56 of 118 (47%) | at least 100 (85%) |
| **Fast** | How many minutes, on average, from the admission decision to a bed? | 26 | fewer than the baseline |

**What the numbers say:**

- **It is not safe.** In 45 of 118 decisions (38%) the patient gets an unsafe bed, because nobody reads the physician's
  free text, where an isolation need or a hidden severity is written. That is the Patient Agent's job.
- **It never takes a bed the ward cannot give** — because it phones the wards. But every call costs 5 minutes, and
  it does not understand a ward's conditions. That is the Department Agent's job: the same answer, without the phone.
- **It is right in 56 of 118 decisions (47%)**, and a patient reaches a bed after 26 minutes on average.

The system has to be safe, right **and** faster than the baseline.

**The scripted days run like a real shift.** Every admission opens a case; a bed that frees while patients wait opens
a case for all of them, and the decider decides who gets it — from the records, never from a hint. Patients who wait
together are handed together. The clock goes on while a case is open, so a slow decider makes the next patients
wait too; one case at a time by default (`config/hospital.yaml`). The report adds a line for this: the longest wait
for a case, and the conflicts — two open cases that chose the same bed.

**Day 4 — competing for a bed** checks the rule for two patients and one bed at three moments: the same state, the
earlier decision first · the bed type needed before a last resort · severe before non-severe. Who should get the bed
is computed at the moment from the patients' true needs. The baseline reaches **none of the three**: it does not
read isolation in the free text, so it puts the isolation patients in regular beds and the competition never forms.

## The single agent — built and measured

One agent with every agent's tools: it reads the record, reads every ward's report and notes, ranks the options and
decides. It is the strong opponent of the comparison — the same task, the same rules and the same cases as the
multi-agent system (`docs/design/comparison_protocol.md`, written before any paid run). Improved in 2 rounds on 22
development cases, then measured 3 times on 29 test cases it had never seen (36 decisions). It is compared with the
baseline on those same 36 decisions only — the baseline on all 118 is in the table above. The same questions:

| | The question | Baseline, test (36) | Single agent, test (36), mean and range of 3 | Target |
|---|---|---|---|---|
| **Safe** | In how many decisions did the patient get an unsafe bed? | 11 of 36 (31%) | 1 of 36 (3%) | 0 (0%) |
| | Of the patients who need observation or isolation, how many got it or were escalated? | 47% | 100% | 100% |
| | How many times was a bed chosen that the ward cannot really give? | 0 | 0 | 0 |
| **Suitable** | In how many decisions was the answer exactly the expected one? | 42% | 76% (72–78%) | at least 85% |
| **Fast** | How many minutes, on average, from the admission decision to a bed? (the 3 scripted days) | 26 | 21 | fewer than the baseline |
| **Cost** | How much per case, and per month at 900 admissions? | — | $0.12 per case, $104 a month ($95–110) | — |

- **Fast, day by day** (minutes to a bed): busy day — baseline 21.2, single agent 31 · evening and night — 31.2 and
  16 · weekend — 26.9 and 16. On the busy day the single agent is slower than the baseline.
- **Stable:** the same decision in all 3 runs in 94% of the decisions; 33 seconds to decide a case, on average.
- **The scripted days**, once: no hard rule broken, and all 3 moments of competing for a bed decided right.
- **The one broken rule** is the same case each time: a known contradiction in the rules about confusion that is not
  new, kept on purpose for both systems (`DECISIONS.md`).

```
.venv\Scripts\python scripts\run_baseline.py                                  the results above
.venv\Scripts\python scripts\run_metrics.py --decider code_coordinator --details    in full
.venv\Scripts\python scripts\run_scenario.py data\scenarios\busy_day.yaml --policy code_coordinator --display
.venv\Scripts\python scripts\run_metrics.py --decider single_agent --set test --repeat 3 --paid   the single agent (paid)
.venv\Scripts\python scripts\compare_systems.py                              quality, latency and cost, side by side
```

A decider that calls a model runs only with `--paid`, after its estimated cost is approved.

## Layout

| Folder | What it holds |
|---|---|
| `src/ed_admission/agents` | The single agent — its loop, tools, budget and the monthly price check; the multi-agent system's agents are added as each is built |
| `src/ed_admission/prompts` | A prompt file for each agent: the single agent's is written; the others as they are built |
| `src/ed_admission/tools` | The tools the agents use, their permissions, and `decide` — the hard rules in code |
| `src/ed_admission/environment` | The simulated hospital and the simulation engine |
| `src/ed_admission/contracts` | The contracts: the record, the Patient Agent's assessment, the request and answer in the negotiation with the wards |
| `src/ed_admission/orchestration` | The stages of a placement case, the Coordinator's flow in code, and a code example of each agent |
| `src/ed_admission/observability` | The trace of every case |
| `src/ed_admission/baselines` | The baseline: the Code Coordinator — today's procedure, written as code |
| `src/ed_admission/evaluation` | The runner, the building of the expected answers, the metrics, the development and test sets, and the comparison report |
| `src/ed_admission/database` | The run history: `runs.csv`, `admissions.csv` and `costs.csv` |
| `config` | Settings: wards and beds, cleaning, hold and approval times, the clinical rules, the single agent's model, budget and prices |
| `data/records` | The 74 synthetic records |
| `data/templates` | 22 ward-state templates |
| `data/answer_key` | The expected answers, for the evaluation code only |
| `data/scenarios` | 4 scripted days |
| `scripts` | Running: the baseline, the metrics, one agent alone, cases, a scripted day, the comparison report, the price check, a health check |
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

Every measured run is added to the run history in `outputs/` — `runs.csv` (one row per run), `admissions.csv`
(one row per patient) and `costs.csv` (one row per case, for a decider that calls a model) — so improvement and cost
can be followed run after run. `scripts/compare_systems.py` sets the single agent against the multi-agent system — quality, latency and cost, per case and per run. Results are never uploaded.

## How the project is kept

- Each agent has its own code file and its own prompt file.
- Code is not written inside a notebook. The Colab notebook installs the project from the repository and imports from it.
- Secrets never go into the code or the notebook. They are kept in Colab Secrets or in a local `.env` file.
