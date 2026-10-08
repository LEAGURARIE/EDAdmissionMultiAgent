# Multi-Agent System for Placing Admitted Emergency Department Patients — Project Plan

> What is done and what is left to do. Updated: 2026-10-07.

## The goal

Help the emergency department's shift manager place every patient who was admitted **in the safest, most suitable bed, in the shortest time** — also when the hospital is busy.

**What the system does:**
- **Understands the patient** — reads the record, including the physician's free text, and identifies the target ward, the severity, and the need for observation, isolation or intensive care.
- **Finds a bed** — checks availability in the wards and asks them; when the target ward has no room, offers alternatives by the proximity ladder, temporary placement included — without compromising on safety.
- **Explains and records** — every recommendation comes with a reason, a quote from the record and a full trace.
- **Leaves the decision to a person** — the shift manager approves, rejects or changes; when there is no safe option, the system escalates to them.

The system does not replace the shift manager — it helps them, mostly in busy hours.

**How we will know it improves things:** we compare it with the "simulated coordinator", that is, today's procedure, on three things: safe, suitable and fast.

---

| Document | Role |
|---|---|
| `CHARTER.md` | The charter submitted to the course |
| `DECISIONS.md` | The decisions in force |
| `docs/design/agent_interfaces.md` | The team guide: what the agents are built on |
| `docs/design/ward_state_templates.md` | The templates, the case plan, the added records and the paired cases |
| `docs/design/case_file_example.md` | An example pair, taken from the real files |
| `docs/design/sources.md` | The sources, S1 to S9, checked against the original |

---

## What is done

### Environment — complete

| What | Where |
|---|---|
| A simulated hospital: 5 wards, regular beds, observation beds and an isolation room in every ward | `config/hospital.yaml` |
| A simulation engine: simulated clock, events, cleaning 45 minutes, hold 30 minutes, shift manager's approval 15 minutes | `environment/simulation_engine.py` |
| Ward report: free beds, cleaning, discharges expected today and whether they are likely within the hour, day, shift and weekend | `environment/ward_report.py` · `environment/shifts.py` |
| Private ward notes, and what they mean for the beds. Not shown in the report | `environment/ward_truth.py` |
| 22 ward-state templates, all with private notes | `data/templates/` |
| 3 scripted days: a busy weekday, evening and night, a weekend. Notes by the hour, beds not updated, crossing midnight | `data/scenarios/` |
| Case mode: one patient, or two patients for one bed | `environment/simulation_engine.py` |
| The decision contract: place, temporary placement, wait, escalate. Flags from a closed list, and a ladder level computed in code | `environment/decision.py` |
| The decider's time is measured for real on a scripted day, and added before the approval | `environment/simulation_engine.py` |
| Reading every config file from one place | `src/ed_admission/config.py` |

### Data and answers — complete and frozen

| What | Where |
|---|---|
| Clinical lists, approved: ladders, observation, isolation, NEWS2, chief complaints, placement rules | `config/clinical_rules/` |
| Signs in the structured fields for the baselines, checked against the sources | `config/clinical_rules/structured_signs.yaml` |
| The record contract · NEWS2 calculation, checked against the source | `contracts/patient_record.py` · `tools/news2.py` |
| **74 records, approved:** the first 60, 12 for the Severity judgment situations, and 2 for MRSA | `data/records/` |
| **104 pairs and 7 paired cases, approved and frozen.** Every pair tests both reading and coordination | `data/answer_key/` |
| Balanced answers: 27 placed in the target ward, 47 temporary placements, 17 waits, 13 escalations | `data/answer_key/` |

### Metrics and baselines — built

| What | Where |
|---|---|
| **The metrics:** match, violations of hard rules, needs met, unsuitable ward, a bed the ward cannot give, flags, match by the reports | `evaluation/metrics.py` |
| **Minutes saved per patient,** against the "simulated coordinator", on the three days | `evaluation/metrics.py` |
| **Ranking in code,** on the reports only | `tools/rank_placements.py` |
| **3 baselines:** "first free bed", "rules only", "simulated coordinator" | `src/ed_admission/baselines/` |
| Running and measuring | `scripts/run_cases.py` · `scripts/run_metrics.py` · `scripts/run_scenario.py` |

**The results:**

| Decider | Match | Violations | Needs met | Time to bed on the days |
|---|---|---|---|---|
| "First free bed" | 0% | 65 | 4% | 15 to 139 minutes, and patients are left waiting |
| "Rules only" | 2% | 48 | 27% | 15 minutes, without asking the wards |
| **"Simulated coordinator", that is, today** | **38%** | **45** | **32%** | **21 on a weekday · 31 at night · 27 at the weekend** |
| The charter's target | 85% | 0 | 100% | Less than today |

### Interfaces for the agents — built

| What | Where |
|---|---|
| Each agent's tools, and the permissions enforced in code. The single agent included | `tools/toolbox.py` |
| The negotiation: the request, the answer, the limits | `contracts/negotiation.py` · `tools/negotiation.py` |
| The stages of a case, in an order enforced in code, and up to 2 Safety Critic rounds | `orchestration/placement_case.py` |
| A trace for every case, complete trace and groundedness | `observability/tracing.py` · `evaluation/metrics.py` |
| An example wiring in code, that runs on the whole evaluation | `orchestration/example_pipeline.py` |
| **Measuring each agent alone,** against the part of the expected answers that is its job, with a code example to beat for every agent | `evaluation/agent_metrics.py` · `scripts/run_agent_metrics.py` · `orchestration/example_agents.py` · `contracts/agent_outputs.py` |
| Every run script takes a decider by name or as `module:function`, so each owner runs their own agent without changing the scripts | `evaluation/deciders.py` |
| **The measurement output, side by side with today:** every metric for the decider and the simulated coordinator, with the difference; by challenge, trap and template; the days; complete traces, groundedness, time and cost per case from the traces the decider hands (`tracing.collect`) | `scripts/run_metrics.py` · `evaluation/metrics.py` · `observability/tracing.py` |
| **Fixed entry points for connecting the agents:** `run` in every agent file, `run_back` for back to Supervision; the Coordinator's `run` is the whole system. Signatures guarded by a test | `src/ed_admission/agents/` · `tests/test_agent_entry_points.py` |

### Agents — roles defined, logic not yet

| What | Where |
|---|---|
| 6 types. Each with a task, tools, links and an owner | `src/ed_admission/agents/` |
| Owners: Lea Coordinator · Ron Clinical Problem and Safety Critic · Galit Severity and Supervision · Amir Department | `src/ed_admission/agents/` |

### Charter and repository

- `CHARTER.md` submitted on 4.10. The lecturer was invited to the repository.
- On GitHub only `CHARTER.md` and `docs/sources.md`. Everything else is local. In the next upload `docs/sources.md` moves to `docs/design/sources.md`, with 9 sources. Approved.

### Tests

893 tests pass:

```
.venv/Scripts/python -m pytest -q
```

---

## What is left to do

### Checkpoint 1 — 18.10

| # | What | Status |
|---|---|---|
| 1 | The pipeline runs end to end with a baseline, without agents | ✓ |
| 2 | **README** with the baselines table and run instructions | — |
| 3 | **Colab notebook** as a run link · `requirements.txt` | Built: `notebooks/ED_Admission_Colab.ipynb` — one notebook for the lecturer and the team. **To test in Colab after the upload** |
| 4 | **A 5-line update** in the repository: what works, metric against target, what we decided and why, what is stuck, 3 next steps | — |
| 5 | **Update `CHARTER.md`** after the lecturer's feedback, in one go. Check it stays at 2 pages | — |
| 6 | **Upload to the repository,** only after every file was reviewed and approved | — |

**Changes to the charter:**

| Field | Today | Change to |
|---|---|---|
| 2 — Metrics | Quality against "first free bed" and "rules only". Time: 20% faster than "first free bed" | **All metrics against the "simulated coordinator", that is, today:** safe, suitable and fast, plus the absolute targets. **Time:** minutes saved per patient, from the admission decision to a bed |
| 2 — Baselines | "First free bed" and "rules only" | Plus the "simulated coordinator". "First free bed" stays the safety floor, and "rules only" is the requirement to work without agents |
| 3 — Data | 60 cases and 3 days | 74 records, 22 templates, 104 pairs and 7 paired cases. Every pair tests reading and coordination. 3 days: a busy weekday, evening and night, a weekend |
| 4 — Architecture, 5 — Agent's chair, 7 — Hats | 7 agent types, including an Isolation Agent, which Amir owns | 6 types. Isolation moves to the Supervision Agent. Update Amir's responsibility |

**Draft for line 3 of the update, "what we decided and why":**
- The "simulated coordinator" is today's procedure as code, and **all metrics are compared with it.**
- Every pair requires both reading the patient and asking the wards, so the evaluation tests what the agents are meant to add.
- The system's time is measured for real, not estimated.

### Checkpoint 2 — 30.10

| # | What | Status |
|---|---|---|
| 1 | **Building the agents,** each by its owner, on the interfaces that were built. The prompts in separate files | — |
| 2 | **Measurement:** all metrics against the "simulated coordinator", including minutes saved per patient, complete trace and groundedness | The code is ready |
| 3 | **Comparison with a single agent,** with the same tools as the system | **Open: who builds it** |
| 4 | **A run without the Safety Critic,** to check its contribution | — |
| 5 | **A one-minute demo** | — |
| 6 | **Technical tools:** how the agents run, the proposal Tool Runner · interface, the proposal Streamlit | Open |
| 7 | **Memory:** to discuss. The proposal raised: off during evaluation, so every case starts clean | Open |
| 8 | **The Anthropic key** in `.env` | **Filled by the user** |
| 9 | **Admissions log: one CSV file that accumulates from all runs,** `outputs/admissions.csv`. A row per patient per run: request time, admission time, target ward, actual ward, the reason and a link to the trace, the shift manager's approval, escalation. **Build after the system works.** Needs a target-ward field in every decision | — |

### Open, no date

| What | Note |
|---|---|
| A numeric target for minutes saved | Set after the system's first run |
| An engineering target for the system's response time, for example 2 minutes per case | Not decided. Not in the charter |
| Merging Severity and Supervision into one agent | Decided: not for now |

---

## State for the next session

**Where we stopped, 2026-10-07:** the environment, the data, the answers, the metrics, the three baselines and the agent interfaces are **complete and tested.** 893 tests pass. **The team can start building the agents** following `docs/design/agent_interfaces.md`.

**Git review in progress:** the documents were translated to English and approved. Still to approve: 22 templates, 4 scenario files, `notebooks/README.md`, the 191 record and answer files, and the per-agent measurement built today (6 new files: `contracts/agent_outputs.py`, `evaluation/agent_metrics.py`, `evaluation/deciders.py`, `orchestration/example_agents.py`, `scripts/run_agent_metrics.py`, `tests/test_agent_metrics.py`, `tests/test_agent_entry_points.py`; the six agent files with their entry points; the Colab notebook, `requirements.txt`, `notebooks/README.md`, `README.md`, `scripts/healthcheck.py`, `tests/test_colab_notebook.py`; the side-by-side measurement output (`scripts/run_metrics.py`, `evaluation/metrics.py`, `observability/tracing.py`, `orchestration/example_pipeline.py`, `tests/test_metrics.py`, `tests/test_agent_interfaces.py`); changed: the three run scripts, `docs/design/agent_interfaces.md`, `DECISIONS.md`, `PROJECT.md`, `Agents_Guide.pdf`). Then the final file list and the commit message; nothing uploaded yet.

**Next:** Checkpoint 1, on 18.10: README, Colab notebook, `requirements.txt`, the 5-line update, and the charter update. Then upload to the repository, after every file is approved.
