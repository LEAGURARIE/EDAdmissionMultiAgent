"""The single agent — the whole placement by one agent, for the comparison with the multi-agent system.

    run(engine, event, waiting) -> list[Decision]       the decider contract, the same as the baseline's

One case at a time: the patients the engine hands over (one at an admission decision; everyone waiting when a bed
frees). For each case:
  1  the case's tools — a single_agent toolbox and the nine tools bound to this case's patients
     (agents/single_agent_tools.py). It reads every ward's notes itself (department_view): one agent doing every
     agent's job, so the comparison with the multi-agent system is the same task (2026-10-15)
  2  the system prompt (prompts/single_agent.md, written with Lea) and its version — a fingerprint of the text, so
     every decision says which prompt made it
  3  the task: the time now, and each patient's id with the time their admission was decided — nothing else. The
     event (what triggered the case, a ward's note) is never shown: the agent finds out from the records and asks
     the wards (tests/test_no_hints.py)
  4  the loop (agent_loop.run_agent): it ends when every patient has a decision or an escalation; if the model
     stops before that, it gets one reminder of who is left. After every round the code sends the case state
     (case_state): each patient's assessment, ranking and decision, the wards whose notes were read, the model
     calls left — so the agent does not spend its thinking reconstructing it
  5  what is left after the loop — budget, refusal, an API error, or no decision after the reminder — is escalated
     to the shift manager in code, with the reason and the flags of the agent's assessment. Decisions already made
     stay. Never a guess.
  6  the trace: how the case ended, model calls, tokens, cost, the prompt version, the claims and the steps the
     complete-trace metric looks for; handed to the measurement (tracing.collect)

The run's budget: a script that pays for model calls opens it with start_run() (the prices are checked first) and
closes it with end_run(). When it is spent, run() raises RunStopped: the script keeps the cases already decided,
marks the run partial and stops — the remaining cases are not escalated, which would distort the metrics.

Owner: Lea Gur-Arie
"""
import hashlib
import time
from functools import cache
from pathlib import Path

from ed_admission.agents import budget
from ed_admission.agents.budget import RunStopped
from ed_admission.agents.agent_loop import AgentResult, run_agent
from ed_admission.agents.single_agent_tools import single_agent_tools
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event, to_clock
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.observability.tracing import Trace, collect
from ed_admission.tools.toolbox import Toolbox

AGENT = "single_agent"
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "single_agent.md"

# Why the code escalates a patient the agent left without a decision, by how the loop ended.
STOPPED = {"budget": "the case reached its budget", "run_budget": "the run reached its budget",
           "refused": "the model declined the case", "failed": "the model could not be reached",
           "no_final": "the agent stopped without deciding", "final": "the agent stopped without deciding"}


# ============ THE RUN'S BUDGET ============
_run_budget: budget.RunBudget | None = None


def start_run() -> budget.RunBudget:
    """Open the run's budget (the prices are checked first). Called once by a script before the first case."""
    global _run_budget
    _run_budget = budget.start_run(AGENT)
    return _run_budget


def end_run() -> None:
    global _run_budget
    _run_budget = None


# ============ THE PROMPT AND THE TASK ============
@cache
def system_prompt() -> tuple[str, str]:
    """The system prompt and its version: the first 10 characters of its fingerprint."""
    text = PROMPT_FILE.read_text(encoding="utf8")
    return text, hashlib.sha256(text.encode("utf8")).hexdigest()[:10]


def task_text(engine, waiting: list[WaitingPatient]) -> str:
    """What the agent is told about the case: the time, the patients and when each admission was decided."""
    lines = [f"The time now: {to_clock(engine.clock)}.",
             "The patients of this case, each with the time the admission was decided:"]
    lines += [f"- {patient.patient_id}: {to_clock(patient.decided_at)}" for patient in waiting]
    lines.append("Give every one of them a decision or an escalation.")
    return "\n".join(lines)


# ============ ONE CASE ============
def run(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    """One case: a decision for every waiting patient, in the order the agent made them."""
    if _run_budget is not None and _run_budget.exhausted:
        raise RunStopped("the run's budget is spent")
    patients = [patient.patient_id for patient in waiting]
    if not patients:
        return []

    trace = Trace(case_id="+".join(patients), decider=AGENT)
    toolbox = Toolbox(AGENT, engine, trace=trace)
    decisions: dict[str, Decision] = {}
    tools = single_agent_tools(toolbox, patients, decisions)

    def left() -> list[str]:
        return [pid for pid in patients if pid not in decisions]

    def reminder() -> str | None:
        return (f"Still without a decision: {', '.join(left())}. Give each of them a decision or an escalation."
                if left() else None)

    prompt, version = system_prompt()
    started = time.perf_counter()
    result = run_agent(AGENT, prompt, task_text(engine, waiting), tools, run_budget=_run_budget,
                       done=lambda: not left(), remind=reminder,
                       state=lambda so_far: case_state(patients, toolbox.assessed(), decisions, so_far))

    for patient_id in left():                               # never a guess: the shift manager takes it
        decisions[patient_id] = toolbox.escalate_to_human(
            patient_id, f"Escalated by the system: {STOPPED[result.status]} before this patient had a decision.")

    _close_trace(trace, result, version, time.perf_counter() - started, all_by_the_agent=result.status == "final")
    collect(trace)
    if result.status == "run_budget":
        raise RunStopped("the run's budget was reached during this case")
    return list(decisions.values())


def case_state(patients: list[str], assessed: dict, decisions: dict[str, Decision], so_far: AgentResult) -> str:
    """Where the case stands, built by the code from what really happened — sent after every round, so the agent does
    not work it out again: each patient's assessment, ranking and decision, the wards whose notes were read, and the
    model calls left."""
    succeeded = [step for step in so_far.trace if "tool" in step and "error" not in step]
    ranked = {step["args"].get("patient_id") for step in succeeded if step["tool"] == "rank_placements"}
    wards = list(dict.fromkeys(step["args"].get("ward_id") for step in succeeded
                               if step["tool"] == "department_view"))
    lines = ["Case state"]
    for patient_id in patients:
        if patient_id not in assessed:
            lines.append(f"- {patient_id}: not assessed yet")
            continue
        assessment = assessed[patient_id]
        parts = [f"assessed (bed: {assessment.bed_type}, may wait: {'no' if assessment.never_waits else 'yes'}, "
                 f"target: {assessment.target_ward})", "ranked" if patient_id in ranked else "not ranked yet"]
        decision = decisions.get(patient_id)
        if decision is None:
            parts.append("no decision yet")
        else:
            where = f" {decision.ward_id} bed {decision.bed_number}" if decision.bed_number is not None else ""
            parts.append(f"decided: {decision.action.value}{where}")
        lines.append(f"- {patient_id}: " + " · ".join(parts))
    lines.append(f"Wards whose notes you read: {', '.join(wards) if wards else 'none yet'}.")
    lines.append(f"Model calls: {so_far.iterations} of {budget.settings(AGENT)['max_iterations']}.")
    return "\n".join(lines)


def _close_trace(trace: Trace, result: AgentResult, version: str, seconds: float, all_by_the_agent: bool) -> None:
    """How the case ended, what it cost, and the steps of the placement the agent really reached."""
    trace.ended = result.status
    trace.agent_call(AGENT, "case", {"status": result.status, "loop": result.trace}, model=result.model,
                     prompt_version=version, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                     cost_usd=result.cost_usd, seconds=seconds, model_calls=result.iterations,
                     error=f"the model could not be reached: {result.trace[-1].get('error', '')}"
                     if result.status == "failed" else "")
    done = {step["tool"] for step in result.trace if "tool" in step and "error" not in step}
    reached = {"patient_assessed": "record_assessment" in done,
               "availability_collected": bool({"all_department_status", "rank_placements"} & done),
               "ranked": "rank_placements" in done,
               "rules_checked": bool({"decide", "escalate_to_human"} & done),
               "decided": all_by_the_agent}
    for step, was_reached in reached.items():
        if was_reached:
            trace.milestone(step)
