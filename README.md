# Multi-Agent System for Placing Admitted Emergency Department Patients

A system that runs in a simulated hospital. When a patient is admitted, it reads the medical record and recommends the
safest, most suitable bed in the shortest time, for the shift manager's approval.

**A multi-agent system**, each agent seeing what the others do not: the **Patient Agent** reads the record; the
**Department Agent** answers for one ward at a time, reading that ward's report and private notes; the **Judge**
checks a proposed placement in two layers — code first, then a **Judge Agent** for what code cannot check; the
**Coordinator** is code, a graph that activates the agents and routes on their closed answers only. Ranking, the
proposal and the hard safety rules are code. It is compared with a **single agent** that does every agent's job, and
with the baseline: today's procedure, written as code. Both systems are built and measured.

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

| | The question | Baseline, test (36) | Single agent, test (36) — average of 3 identical runs | Target |
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

## The multi-agent system — built and measured

**Why several agents.** Each part of the job needs a different reading, and in a hospital the information is
divided too:

| Agent | What only it sees | Why an agent, not code |
|---|---|---|
| **Patient Agent** | the whole record, the physician's free text included | needs are written in free text — negation, "cannot be ruled out", a hidden severity; code that reads only the structured fields is the baseline, and it is unsafe in 38% of decisions |
| **Department Agent** | one ward's report and its private notes — never the patient's identity | a ward's note can free a bed or close one ("bed 6 is free once the nurse arrives"); the agent answers as the ward would on the phone, without the 5-minute call |
| **Judge Agent** | a case file: the findings with the sentence each quote came from, the wards' answers and notes, the code checks | checks what code cannot — that a need was read right, that the reason the shift manager reads is true to what the wards said |
| **Coordinator** | every agent's closed answer, never the record | it is code on purpose: routing, ranking and the hard rules must be the same every time |

Every handoff between the parts is a checked contract (Pydantic) and is tagged as data, so text in a record cannot
act as an instruction. The Patient Agent and the Department Agent run on Sonnet, the Judge Agent on Haiku; the single
agent on Opus.

**How it was measured** — the same protocol as the single agent (`docs/design/comparison_protocol.md`): each agent
measured alone, then 2 rounds on the whole system on the 22 development cases, then frozen and run 3 times on the
same 29 test cases (36 decisions) and once on each scripted day:

| | The question | Baseline, test (36) | Single agent — average of 3 identical runs | Multi-agent system — average of 3 identical runs | Target |
|---|---|---|---|---|---|
| **Safe** | In how many decisions did the patient get an unsafe bed? | 11 of 36 (31%) | 1 of 36 (3%) | 1 of 36 (3%) | 0 (0%) |
| | Of the patients who need observation or isolation, how many got it or were escalated? | 47% | 100% | 100% | 100% |
| | How many times was a bed chosen that the ward cannot really give? | 0 | 0 | 0 | 0 |
| **Suitable** | In how many decisions was the answer exactly the expected one? | 42% | **76%** (72–78%) | 60% (58–61%) | at least 85% |
| **Grounded** | How many of the claims about the patient are backed by a quote in the record? | — | 91% (88–94%) | **100%** | at least 95% |
| **Fast** | How many minutes, on average, from the admission decision to a bed? (the 3 scripted days) | 26 | 21 | **16** | fewer than the baseline |
| | How long to decide a case, in seconds? | — | **33** | 49 | — |
| **Cost** | How much per case, and per month at 900 admissions? | — | $0.12 · $104 a month | **$0.043 · $38 a month** | — |

Each system ran the same test set 3 times — 3 identical runs: the same code, models and cases — since a model can answer differently each time. Each number is the average of the 3 runs, with the lowest and highest run in brackets; a difference counts only when these ranges do not overlap.

**What the numbers say:**

- **Equally safe.** The same one broken rule in both systems (the contradiction kept on purpose), every need met or
  escalated, no bed the ward cannot give, and no rule broken on any scripted day.
- **The single agent is more suitable** — 76% against 60%, beyond the spread. Neither reaches the 85% target.
- **The multi-agent system is better grounded, cheaper and faster to a bed:** every claim backed by the record,
  2.7 times cheaper, and 16 minutes to a bed on every scripted day — on the busy day 16 against the single agent's 31.
  It takes longer to decide one case (49 seconds against 33), and the scripted days still show the shorter time to
  a bed.
- **Stable:** the same decision in all 3 runs in 97% of the decisions (single agent 94%).

**Where the gap in suitability comes from.** Most of the multi-agent system's misses are one kind: the target ward
says in its free-text reason that a bed frees soon ("in about an hour"), but not in its closed fields, so the code
goes on to a temporary bed elsewhere instead of a short wait. The single agent reads the ward's note itself and
waits. Two checks, kept out of the comparison, tested the cause:

- **Stronger models** — every agent on Opus: the same on the test set (61% against 60%, the ranges overlap) at 3
  times the cost. The gap is not the models.
- **The Judge Agent looks for a better option** — a general check that the wards' words show no better option than
  the proposal: on the development set it fixed no decision (68% before and after), so it was removed.

The gap comes from one design choice: the parts hand over closed fields only, and code decides from them. What a
ward knows and says only in free text does not reach the decision. The same choice is what keeps every claim
grounded, every step traceable, and the routing safe from text in a record.

```
.venv\Scripts\python scripts\run_baseline.py                                  the results above
.venv\Scripts\python scripts\run_metrics.py --decider code_coordinator --details    in full
.venv\Scripts\python scripts\run_scenario.py data\scenarios\busy_day.yaml --policy code_coordinator --display
.venv\Scripts\python scripts\run_metrics.py --decider single_agent --set test --repeat 3 --paid   the single agent (paid)
.venv\Scripts\python scripts\run_metrics.py --decider multi_agent --set test --repeat 3 --paid    the multi-agent system (paid)
.venv\Scripts\python scripts\compare_systems.py --admissions-per-month 900   quality, latency and cost, side by side
.venv\Scripts\python scripts\build_demo_replay.py                          the demo: a measured case, step by step
```

A decider that calls a model runs only with `--paid`, after its estimated cost is approved.

## Layout

| Folder | What it holds |
|---|---|
| `src/ed_admission/agents` | The single agent and the multi-agent system's three agents — their tools, the shared model call, the budget and the monthly price check |
| `src/ed_admission/prompts` | A prompt file for each agent: the single agent, the Patient Agent, the Department Agent and the Judge Agent |
| `src/ed_admission/tools` | The tools the agents use, their permissions, and `decide` — the hard rules in code |
| `src/ed_admission/environment` | The simulated hospital and the simulation engine |
| `src/ed_admission/contracts` | The contracts: the record, the Patient Agent's assessment, the request and answer in the negotiation with the wards |
| `src/ed_admission/orchestration` | The Coordinator: the graph (LangGraph), the handoff checks, the proposal and the Judge's code checks |
| `src/ed_admission/observability` | The trace of every case |
| `src/ed_admission/baselines` | The baseline: the Code Coordinator — today's procedure, written as code |
| `src/ed_admission/evaluation` | The runner, the building of the expected answers, the metrics, the development and test sets, and the comparison report |
| `src/ed_admission/database` | The run history: `runs.csv`, `admissions.csv` and `costs.csv` |
| `config` | Settings: wards and beds, cleaning, hold and approval times, the clinical rules, the agents' models, budget and prices, the Judge's rubric, and the evaluation's targets and repetitions |
| `data/records` | The 74 synthetic records |
| `data/templates` | 22 ward-state templates |
| `data/answer_key` | The expected answers, for the evaluation code only |
| `data/scenarios` | 4 scripted days |
| `scripts` | Running: the baseline, the metrics, one agent alone, cases, a scripted day, the comparison report, the demo replay, the price check, a health check |
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
