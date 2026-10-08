# Agent interfaces — what the team builds on

Everything an agent needs is built and tested (`tests/test_agent_interfaces.py`). An agent is plugged in as a
**decider** — the same contract the baselines use — and is measured by the same code.

## 1. The decider

```python
def my_decider(engine, event, waiting) -> list[Decision]: ...
```

| | |
|---|---|
| Contract | `src/ed_admission/environment/decision.py` — `Decision`: place · temporary_place · wait · escalate, flags, `physician_approval_for_icu`, `handling_minutes` |
| Case mode | one patient (`engine.decide_case`) or two (`engine.decide_cases`, paired cases); nothing is applied |
| Scripted day | the engine calls the decider at every trigger and **measures how long it took** (rounded up to whole minutes, under a second is instant); a placement holds the bed, and that time plus 15 minutes later the shift manager approves |
| Run it | `scripts/run_cases.py --decider NAME [--set first|second|paired]` · `scripts/run_metrics.py --decider NAME` — NAME is a baseline, `example_pipeline`, or your own `module:function` (section 6) |
| Template | `src/ed_admission/orchestration/example_pipeline.py` — code only, uses every interface below; replace each step with the agent |

## 2. The toolbox — permissions in code

`src/ed_admission/tools/toolbox.py`: `Toolbox(role, engine, ward_id=None, negotiation=None, trace=None)`.
A tool the role may not use raises `PermissionDenied`. The single agent of the 30.10 comparison gets the same tools as
the system, and asks the wards through the same negotiation.

| Tool | Coordinator | Clinical Problem | Severity | Supervision | Department | Safety Critic | Single agent (30.10) |
|---|---|---|---|---|---|---|---|
| `read_patient_record` | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ |
| `compute_news2` | ✓ | | ✓ | ✓ | | ✓ | ✓ |
| `clinical_lists` (observation, isolation, chief complaints) | | ✓ | | ✓ | | ✓ | ✓ |
| `all_department_status` (every report, no notes) | ✓ | | | | | ✓ | ✓ |
| `department_view` (its own report and notes) | | | | | ✓ bound to one ward | |  |
| `rank_placements` | ✓ | | | | | | ✓ |
| `ask_ward` (negotiation) | ✓ | | | | | | ✓ |
| `hold_bed`, `release_hold`, `escalate_to_human` | ✓ | | | | | | ✓ |

## 3. The negotiation — Coordinator ⇄ Department Agent

| | |
|---|---|
| Contract | `src/ed_admission/contracts/negotiation.py` — `WardRequest` (needs only: bed type, severe, observation, isolation, target ward, proposed bed — no identifier) · `WardAnswer` (accept · condition · decline, bed, reason, condition, minutes) |
| Channel | `src/ed_admission/tools/negotiation.py` — `Negotiation(engine, responders)`; a responder is `(DepartmentView, WardRequest) -> WardAnswer` — the Department Agent |
| Limits | 3 requests per ward, 8 per case → `LimitReached` → escalate (`placement_rules.yaml`, `negotiation`) |
| Time | not fixed per request — the engine measures the whole decision in a scripted day |
| Code responder | `truthful_ward(engine)` answers from the ward's true state — for tests and for the "simulated coordinator" |

## 4. The placement case — steps in order

`src/ed_admission/orchestration/placement_case.py`: `PlacementCase.move(step)` refuses a step out of order.

```
triggered → patient_assessed → availability_collected → ranked → [negotiating ⇄ back_to_supervision]
          → critic_reviewed (at most 2) → decided        an escalation may end the case at any step
```

## 5. The trace — complete and grounded

`src/ed_admission/observability/tracing.py`: one `Trace` per case. Record every agent call with model, prompt
version, tokens, cost and time (`trace.agent_call`), and every claim about the patient with the exact words of the
record that back it (`Claim(claim, quote)`). The toolbox records the tool calls; the placement case records the steps.
When a case is decided, hand its trace to the measurement: `tracing.collect(trace)` — the Coordinator Agent's `run`
does it for every case (the example pipeline shows how). `scripts/run_metrics.py` takes them and prints complete
traces, groundedness and the cost per case; a decider that collects none has no trace metrics.

| Charter metric | Code | Target |
|---|---|---|
| Complete trace | `metrics.complete_trace_rate(traces)` | 100% |
| Minutes saved for each patient, against today | `metrics.minutes_saved(today_engine, system_engine)` — `scripts/run_metrics.py` prints it | more than 0, with safety |
| Groundedness — the quote really appears in the record | `metrics.groundedness(traces)` | ≥ 95% |

## 6. How to run and measure

Each owner builds their agent alone and measures it in two ways — no change to the scripts is needed.

**A. The agent alone**, against the part of the expected answers that is its job. The agent gets only its own
toolbox (or, for the Department Agent, its ward's view and the request) and never sees the answers.

```
.venv/Scripts/python scripts/run_agent_metrics.py --agent severity                     (the code example: the number to beat)
.venv/Scripts/python scripts/run_agent_metrics.py --agent severity --function ed_admission.agents.severity_agent:run
```

| Agent | Its `run` | What is measured |
|---|---|---|
| Clinical Problem | `run(toolbox, patient_id) -> ClinicalProblemOutput` | target ward · flag `prompt_injection` — 74 records |
| Severity | `run(toolbox, patient_id) -> SeverityOutput` | severe · intensive care · flags `missing_vital_sign`, `severity_judgment_note` |
| Supervision | `run(toolbox, patient_id) -> SupervisionOutput` | observation · isolation |
| Department | `run(view, request) -> WardAnswer` — the negotiation responder | a bed now or not, as the ward really is · an accepted bed is really free · apart: `the note decides`, the requests where the report alone gives the wrong answer |
| Safety Critic | `run(toolbox, patient_id, decision) -> SafetyVerdict` | the three baselines' decisions on every pair: blocks the unsafe ones (a hard-rule violation or a missing flag), approves the safe ones |

The outputs: `src/ed_admission/contracts/agent_outputs.py` — extra fields (reasons, quotes, notes) are allowed.
The code examples: `src/ed_admission/orchestration/example_agents.py`. The script prints the rate of every check,
by challenge and trap (or template / decider), and every wrong row; the rows go to `outputs/`.

**B. The whole decision.** Put the agent's `run` in its step of a copy of `example_pipeline.py`, then:

```
.venv/Scripts/python scripts/run_metrics.py --decider my_module:my_decider
```

Compare with `--decider example_pipeline`: the difference is what the agent adds to the final placement.

What `run_metrics.py` prints: every metric for the decider and for today (the simulated coordinator) side by side,
with the difference and the charter's target · match and violations by challenge, trap and template, the decider
and today · the three scripted days side by side · minutes saved for each patient · time per case, and from the
traces: complete traces, groundedness, cost per case and tokens. Everything is saved to `outputs/metrics_<decider>.json`.

**C. Connecting everything.** Every agent file has one fixed entry point, `run` — the signature in the table above,
the one it is measured through — so the agents connect without any change. The Coordinator Agent's `run` is the
whole system as one decider: it calls each agent's `run`, joins the three patient-side outputs into the patient's
needs, ranks with `rank_placements`, negotiates with the Department Agents' `run` as the responders, goes back to
Supervision when no ward offers a bed that meets the need, and has the Safety Critic check the decision:

```
.venv/Scripts/python scripts/run_metrics.py --decider ed_admission.agents.coordinator_agent:run
```

Back to Supervision has its own entry point: `run_back(toolbox, patient_id, offers) -> BackToSupervisionAnswer`
(`isolation_room_last_resort` or `escalate`; `offers`: every ward's `Offer`). The signatures are tested
(`tests/test_agent_entry_points.py`): a changed signature fails in the owner's tests, before it reaches the integrator.

## 7. What an agent must never do

**The team may see the expected answers; the agents may not.** The answers are in the repository so every owner
can measure their agent and see where it went wrong. Nothing of them may reach an agent: not through code, and not
through its prompt. A prompt is written from the agent's role and `DECISIONS.md` — never from the answers, never
with an evaluation record, case id or template as an example, and never tuned to one case. An agent that only does
well on cases it was shown has learned nothing; the scripted days and new records will show it.

- Read `data/answer_key/` — a test fails if any code outside `src/ed_admission/evaluation/` refers to it.
- Read the templates or scenarios, or a ward's `note_effects` — the agents get the notes' text only, through the
  Department Agent's `department_view`.
- Send `temperature` to a Claude model; handle secrets (`.env` is filled by the user).

## 8. Where each agent lives

| Agent | Code | Entry point | Prompt | Owner |
|---|---|---|---|---|
| Coordinator | `agents/coordinator_agent.py` | `run(engine, event, waiting)` | `prompts/coordinator_agent.md` | Lea |
| Clinical Problem | `agents/clinical_problem_agent.py` | `run` | `prompts/clinical_problem_agent.md` | Ron |
| Severity | `agents/severity_agent.py` | `run` | `prompts/severity_agent.md` | Galit |
| Supervision | `agents/supervision_agent.py` | `run` · `run_back` | `prompts/supervision_agent.md` | Galit |
| Department (one per ward) | `agents/department_agent.py` | `run` | `prompts/department_agent.md` | Amir |
| Safety Critic | `agents/safety_critic_agent.py` | `run` | `prompts/safety_critic_agent.md` | Ron |

The task of each agent, and the decisions behind it: the docstring of its file and `DECISIONS.md`.
