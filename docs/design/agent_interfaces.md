# Agent interfaces — what the agents are built on

The system is **two agents and a Coordinator**. Each sees what the others do not:

| Agent | Sees | Decides |
|---|---|---|
| **Patient Agent** | the record only | what the patient needs: target ward, level of care, bed type, flags — every finding with a quote |
| **Department Agent**, one per ward | its own ward only: the report and the private notes | what the ward can really give: a bed now, a bed with a condition, or a decline |
| **Coordinator** | every ward's report and the two agents' answers — never the record | where the patient goes |

**No handoff between the agents:** only the Coordinator calls an agent, and every answer returns to it (`tests/test_two_agents_toolbox.py`).

The Coordinator is an agent with tools: it decides the next step, and the code keeps the limits — the ranking, the
hard rules, the limits on requests. Everything below is built and tested (`tests/test_agent_interfaces.py`,
`tests/test_two_agents_toolbox.py`).

**Status, 2026-10-13:** the interfaces and the Coordinator's flow in code (`example_pipeline.py`) are built. The three
agents are entry points only: they are built after each one's code and system prompt is reviewed, and no agent has
been run against a model.

## 1. The decider

The whole system plugs in as a **decider** — the same contract the baseline uses — and is measured by the same code.

```python
def my_decider(engine, event, waiting) -> list[Decision]: ...
```

| | |
|---|---|
| Contract | `src/ed_admission/environment/decision.py` — `Decision`: place · temporary_place · wait · escalate, `target_ward` (every action — where the patient was meant to go), flags, `physician_approval_for_icu`, `handling_minutes` |
| Case mode | one patient (`engine.decide_case`) or two (`engine.decide_cases`, paired cases); nothing is applied |
| Scripted day | the engine calls the decider at every trigger and **measures how long it took** (rounded up to whole minutes, under a second is instant); a placement holds the bed, and that time plus 15 minutes later the shift manager approves |
| Run it | `scripts/run_cases.py --decider NAME [--set first|second|paired]` · `scripts/run_metrics.py --decider NAME` — NAME is `code_coordinator` (the baseline), `example_pipeline`, or any `module:function` |
| The flow in code | `src/ed_admission/orchestration/example_pipeline.py` — no model; uses every tool of the Coordinator |

## 2. The toolbox — permissions in code

`src/ed_admission/tools/toolbox.py`: `Toolbox(role, engine, ward_id=None, negotiation=None, trace=None, patient_agent=None, case=None)`.
A tool the role may not use raises `PermissionDenied`.

| Tool | Patient | Department | Coordinator | Single agent (comparison) |
|---|---|---|---|---|
| `read_patient_record` | ✓ | | | ✓ |
| `compute_news2` | ✓ | | | ✓ |
| `clinical_lists` (observation, isolation, chief complaints) | ✓ | | | ✓ |
| `department_view` (its own report and notes) | | ✓ bound to one ward | | |
| `assess_patient` (runs the Patient Agent) | | | ✓ | |
| `all_department_status` (every report, no notes) | | | ✓ | ✓ |
| `rank_placements` | | | ✓ | ✓ |
| `ask_ward` (negotiation) | | | ✓ | ✓ |
| `hold_bed`, `release_hold`, `escalate_to_human` | | | ✓ | ✓ |
| `decide` (the hard rules, in code) | | | ✓ | ✓ |

**`assess_patient`** runs the Patient Agent with its own toolbox, checks every finding's quote against the record
(`grounded`), hands the findings to the trace, and keeps the assessment. **`decide`** checks the decision against that
kept assessment (`tools/decide.py`) and refuses it, with the reasons, when a hard rule is broken:

| The rule | |
|---|---|
| The need is never lowered | observation → an observation bed or the isolation room · isolation → the isolation room · intensive care → intensive care |
| Nothing above the need | intensive care only for a patient who needs it · a non-severe patient never in an observation bed |
| A severe patient never waits | |
| The target ward stands | a ward the physician requested is never changed; `internal_medicine` may become A or B, nothing else |
| Nothing is dropped | every flag the Patient Agent raised · intensive care without a physician's request is marked for a physician's approval |

The Coordinator cannot change what the Patient Agent found, and cannot decide before the patient is assessed. An
escalation always passes.

## 3. The negotiation — Coordinator ⇄ Department Agent

| | |
|---|---|
| Contract | `src/ed_admission/contracts/negotiation.py` — `WardRequest` (needs only: bed type, severe, observation, isolation, target ward, proposed bed — no identifier) · `WardAnswer` (accept · condition · decline, bed, reason, condition, minutes) |
| Channel | `src/ed_admission/tools/negotiation.py` — `Negotiation(engine, responders)`; a responder is `(DepartmentView, WardRequest) -> WardAnswer` — the Department Agent |
| Limits | 3 requests per ward, 8 per case → `LimitReached` → escalate (`placement_rules.yaml`, `negotiation`) |
| Time | not fixed per request — the engine measures the whole decision in a scripted day |
| Code responder | `truthful_ward(engine)` answers from the ward's true state — for tests and for the Code Coordinator, the baseline |

## 4. The placement case — steps in order

`src/ed_admission/orchestration/placement_case.py`. The Coordinator's tools move the case themselves, so a step cannot
be skipped; a tool called too early is refused (`StepOutOfOrder`).

```
triggered → patient_assessed → availability_collected → ranked → [negotiating] → rules_checked → decided
            assess_patient     all_department_status    rank_     ask_ward        decide
                                                        placements
an escalation may end the case at any step · after 3 refused decisions only an escalation is left
```

## 5. The trace — complete and grounded

`src/ed_admission/observability/tracing.py`: one `Trace` per case. The toolbox records the tool calls, the Patient
Agent's call (model, prompt version, tokens, cost, time) and its claims with their quotes; the placement case records
the steps. When a case is decided, hand its trace to the measurement: `tracing.collect(trace)`.
`scripts/run_metrics.py` takes them and prints complete traces, groundedness and the cost per case; a decider that
collects none (the baseline) has no trace metrics.

| Metric | Code | Target |
|---|---|---|
| Complete trace | `metrics.complete_trace_rate(traces)` | 100% |
| Groundedness — the quote really appears in the record | `metrics.groundedness(traces)` | ≥ 95% |
| Minutes saved for each patient, against the baseline | `metrics.minutes_saved(baseline_engine, system_engine)` | more than 0, with safety |

## 6. How to run and measure

**A. One agent alone**, against the part of the expected answers that is its job. The agent gets only its own
toolbox (or, for the Department Agent, its ward's view and the request) and never sees the answers.

```
.venv/Scripts/python scripts/run_agent_metrics.py --agent patient                      (the code example: the number to beat)
.venv/Scripts/python scripts/run_agent_metrics.py --agent patient --function ed_admission.agents.patient_agent:run --limit 5
```

| Agent | Its `run` | What is measured |
|---|---|---|
| Patient | `run(toolbox, patient_id) -> PatientAssessment` | on the 74 records: target ward · binding request · severe · intensive care · observation · isolation · flags `prompt_injection`, `missing_vital_sign`, `severity_judgment_note` |
| Department | `run(view, request) -> WardAnswer` — the negotiation responder | on 314 requests: a bed now or not, as the ward really is · an accepted bed is really free · apart: `the note decides`, the requests where the report alone gives the wrong answer |

`--cases P-007 P-043` or `--limit 5` try a few first. The code examples: `src/ed_admission/orchestration/example_agents.py`.

**B. The whole decision.**

```
.venv/Scripts/python scripts/run_metrics.py --decider ed_admission.agents.coordinator_agent:run
.venv/Scripts/python scripts/run_baseline.py                                           (the baseline: the Code Coordinator)
```

What the scripts print — the goal in one table, in percentages, against the targets: **safe** (decisions with a hard rule
broken, needs met, a bed the ward cannot give), **suitable** (match), **fast** (minutes to a bed), and **tracked** (complete traces,
groundedness) once the decider keeps traces. `run_metrics.py`: the decider next to the baseline (the Code
Coordinator); `run_agent_metrics.py`: the agent next to its code example; `run_baseline.py`: the baseline
alone. Add `--details` for everything else: by rule, challenge, trap and template, each day, minutes saved,
cost and tokens, and every wrong case.

**The run history** — every run of `run_metrics.py` and `run_agent_metrics.py` adds to two files, so improvement is
seen run after run: `runs.csv` (one row per run — did it improve?) and `admissions.csv` (one row per patient — which
patient was fixed, which was broken). Add `--label "what changed"`. The version is computed from the code, prompts,
configuration and data: a rerun at the same version replaces its rows, a change adds new ones. Locally in
`outputs/`; in Colab in Google Drive (the notebook sets `ED_OUTPUTS`). `src/ed_admission/database/run_log.py`.

## 7. What an agent must never do

**The expected answers are in the repository; the agents never see them.** They are there so each agent can be
measured and its mistakes read. Nothing of them may reach an agent: not through code, and not through its prompt. A
prompt is written from the agent's role and `DECISIONS.md` — never from the answers, never with an evaluation
record, case id or template as an example, and never tuned to one case.

- Read `data/answer_key/` — a test fails if any code outside `src/ed_admission/evaluation/` refers to it.
- Read the templates or scenarios, or a ward's `note_effects` — the agents get the notes' text only, through the
  Department Agent's view.
- Send `temperature` to a Claude model; handle secrets (`.env` is filled by the user).

## 8. Where each agent lives

| Agent | Code | Entry point | Prompt |
|---|---|---|---|
| Patient | `agents/patient_agent.py` | `run(toolbox, patient_id)` | `prompts/patient_agent.md` |
| Department (one per ward) | `agents/department_agent.py` | `run(view, request)` | `prompts/department_agent.md` |
| Coordinator | `agents/coordinator_agent.py` | `run(engine, event, waiting)` | `prompts/coordinator_agent.md` |

The task of each agent, and the decisions behind it: the docstring of its file and `DECISIONS.md`.
