# Agent interfaces — what the agents are built on

**Where this stands (2026-11-14):** both systems are built and measured. **The multi-agent system:** a Patient Agent,
a Department Agent run once per ward, and a Judge in two layers — code checks, then a Judge Agent — with the
Coordinator as code: the LangGraph graph, which runs every agent and routes on their closed answers only
(`DECISIONS.md`). **The single agent** does every agent's job in one agent, for the comparison.

Each agent of the multi-agent system sees what the others do not:

| Agent | Sees | Answers |
|---|---|---|
| **Patient Agent** | the record only | what the patient needs: target ward, level of care, flags — every finding with a quote |
| **Department Agent**, one per ward | its own ward only: the report and the private notes; the patient's needs, never the identity | what the ward can really give: a bed now, a bed with a condition, or a decline |
| **Judge Agent** | a case file: the findings with the sentence each quote came from, the answers and notes of the wards that answered, the proposal and the code checks — never the whole record | whether the needs were read right and the reason is true to the wards |
| **Coordinator** — code, `orchestration/graph.py` | every agent's closed answer, never the record | the next step; the proposal and the hard rules are code |

**No handoff between the agents:** the graph runs every agent, and every answer returns to it; no agent has a tool
that runs another one (`tests/test_toolbox.py`, `tests/test_coordinator_graph.py`).

## 1. The decider

The whole system plugs in as a **decider** — the same contract the baseline uses — and is measured by the same code.

```python
def my_decider(engine, event, waiting) -> list[Decision]: ...
```

| | |
|---|---|
| Contract | `src/ed_admission/environment/decision.py` — `Decision`: place · temporary_place · wait · escalate, `target_ward` (every action — where the patient was meant to go), flags, `physician_approval_for_icu`, `handling_minutes` |
| Case mode | one patient (`engine.decide_case`) or two (`engine.decide_cases`, paired cases); nothing is applied |
| Scripted day | the case desk: an admission opens a **patient case** (that patient), a bed that frees while patients wait opens a **bed case** (all of them, ids and times only); patients who wait together come together. The clock goes on while a case is open: it closes after the time the decider **really took** (rounded up to whole minutes, under a second is instant). One case at a time by default, more wait in a queue (`config/hospital.yaml`). The bed is held when the case closes, and 15 minutes later the shift manager approves; a bed taken meanwhile by another case is a conflict, a bed that was not free is the decider's error |
| Run it | `scripts/run_cases.py --decider NAME [--set first|second|paired]` · `scripts/run_metrics.py --decider NAME` — NAME is `code_coordinator` (the baseline), `single_agent`, `multi_agent` (these two call a model: `--paid`), or any `module:function` |

## 2. The toolbox — permissions in code

`src/ed_admission/tools/toolbox.py`: `Toolbox(role, engine, ward_id=None, trace=None)`. The Coordinator has no toolbox: it is code.
A tool the role may not use raises `PermissionDenied`.

| Tool | Patient | Department | Judge | Single agent (comparison) |
|---|---|---|---|---|
| `read_patient_record` | ✓ | | | ✓ |
| `compute_news2` | ✓ | | | ✓ |
| `clinical_lists` (observation, isolation, chief complaints) | ✓ | | | ✓ |
| `record_assessment` (the assessment, in the Patient Agent's format) | ✓ | | | ✓ |
| `department_view` (a ward's report and notes) | | ✓ bound to its own ward | | ✓ any ward, by id |
| `answer_ward` (the ward's answer, its bed checked in code) | | ✓ | | |
| `read_case` (the Judge Agent's case file) | | | ✓ | |
| `give_verdict` (items 2, 8 and 9) | | | ✓ | |
| `all_department_status` (every report, no notes) | | | | ✓ |
| `rank_placements` | | | | ✓ |
| `escalate_to_human` | | | | ✓ |
| `decide` (the hard rules, in code) | | | | ✓ |

**The single agent's tools** (`agents/single_agent_tools.py`): nine tools, each with a Pydantic input model (the
schema the model sees, strict) and a description in four parts — What, When, When not, How. Its needs come from its
own assessment (`record_assessment`): `rank_placements` takes a patient id only, so it cannot search for a bed
different from the patient it assessed. It reads every ward's report and notes itself (`department_view`, by ward
id) — the Department Agents' job — so it has no wards to ask: the same task as the multi-agent system, not wards
that answer it from their true state (2026-10-15). It works only on the patients of its case, and a bed it gave is
not offered again in the same case. No agent holds or releases a bed: the environment holds it when the case closes.

**`record_assessment`** checks every finding's quote against the record (`grounded`), hands the findings to the
trace, and keeps the assessment. **`decide`** checks the decision against that kept assessment (`tools/decide.py`) and
refuses it, with the reasons, when a hard rule is broken — the multi-agent system runs the same check in its code
checks (`orchestration/code_checks.py`, item 1):

| The rule | |
|---|---|
| The need is never lowered | observation → an observation bed or the isolation room · isolation → the isolation room · intensive care → intensive care |
| Nothing above the need | intensive care only for a patient who needs it · a non-severe patient never in an observation bed |
| A severe patient never waits | |
| The target ward stands | a ward the physician requested is never changed; `internal_medicine` may become A or B, nothing else |
| Nothing is dropped | every flag the Patient Agent raised · intensive care without a physician's request is marked for a physician's approval |

A decision cannot change what the assessment found, and cannot come before the patient is assessed. An
escalation always passes.

## 3. A request to a ward — the Coordinator ⇄ a Department Agent

| | |
|---|---|
| Contract | `src/ed_admission/contracts/negotiation.py` — `WardRequest` (needs only: bed type, severe, observation, isolation, target ward, proposed bed — no identifier) · `WardAnswer` (accept · condition · decline, bed, reason, condition, minutes) |
| What the ward sees | `src/ed_admission/tools/negotiation.py` — `DepartmentView`: its report and its private notes; `bed_source_failure`: where the ward says the bed comes from is checked in code |
| Who asks | the graph (`orchestration/graph.py`): the wards of one rung of the ladder together, rung by rung |
| Limits | 3 requests per ward, 8 per case, counted per assessment → escalate (`placement_rules.yaml`, `negotiation`) |
| Time | not fixed per request — the engine measures the whole decision in a scripted day |

## 4. The Coordinator — the graph

`src/ed_admission/orchestration/graph.py`: the case state is saved after every node; routing reads closed fields
only. The Patient Agent first; the wards rung by rung; code writes the proposal (`proposal.py`) and checks it
(`code_checks.py`, the Judge's first layer); the Judge Agent last; one correction round, then escalation. Every
agent's answer passes a handoff check in code (`handoff.py`). The milestones every case must reach, in order:

```
patient_assessed → availability_collected → ranked → rules_checked → decided
an escalation may end the case at any step
```

## 5. The trace — complete and grounded

`src/ed_admission/observability/tracing.py`: one `Trace` per case. The toolbox records the tool calls, the Patient
Agent's call (model, prompt version, tokens, cost, time) and its claims with their quotes; the decider records the
milestones. When a case is decided, hand its trace to the measurement: `tracing.collect(trace)`.
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
.venv/Scripts/python scripts/run_agent_metrics.py --agent patient --function <module>:run --limit 5
```

| Agent | Its `run` | What is measured |
|---|---|---|
| Patient | `run(toolbox, patient_id) -> PatientAssessment` | on the 74 records: target ward · binding request · severe · intensive care · observation · isolation · flags `prompt_injection`, `missing_vital_sign`, `severity_judgment_note` |
| Department | `run(view, request) -> WardAnswer` — the negotiation responder | on 314 requests: a bed now or not, as the ward really is · an accepted bed is really free · apart: `the note decides`, the requests where the report alone gives the wrong answer |

`--cases P-007 P-043` or `--limit 5` try a few first. The code examples: `src/ed_admission/evaluation/example_agents.py`. The
multi-agent system's real agents: `--real --paid`, on the development set only.

**B. The whole decision.**

```
.venv/Scripts/python scripts/run_metrics.py --decider single_agent --set test --paid
.venv/Scripts/python scripts/run_baseline.py                                           (the baseline: the Code Coordinator)
```

What the scripts print — the goal in one table, in percentages, against the targets: **safe** (decisions with a hard rule
broken, needs met, a bed the ward cannot give), **suitable** (match), **fast** (minutes to a bed), and **tracked** (complete traces,
groundedness) once the decider keeps traces. `run_metrics.py`: the decider next to the baseline (the Code
Coordinator); `run_agent_metrics.py`: the agent next to its code example; `run_baseline.py`: the baseline
alone. Add `--details` for everything else: by rule, challenge, trap and template, each day, minutes saved,
cost and tokens, and every wrong case.

**The run history** — every run of `run_metrics.py` and `run_agent_metrics.py` adds to the run history, so improvement
is seen run after run: `runs.csv` (one row per run — did it improve?), `admissions.csv` (one row per patient — which
patient was fixed, which was broken) and `costs.csv` (one row per case of a decider that calls a model — what did it
cost? `scripts/compare_systems.py` sets the single agent against the multi-agent system — quality, latency and cost). Add `--label "what changed"`. The version is computed from the code, prompts,
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
| Single agent | `agents/single_agent.py` | `run(engine, event, waiting)` | `prompts/single_agent.md` |
| Multi-agent system | `agents/multi_agent.py` | `run(engine, event, waiting)` | — |
| Patient Agent | `agents/orchestra_agents.py`, tools in `agents/orchestra_tools.py` | the graph's `patient_agent` node | `prompts/patient.md` |
| Department Agent | the same | the graph's `department_agent` node, once per ward | `prompts/department.md` |
| Judge Agent | the same | the graph's `judge_agent` node | `prompts/judge.md` |
| Coordinator | `orchestration/graph.py` — code | | |

 The task of each agent, and the decisions behind it: the docstring of its file and `DECISIONS.md`.
