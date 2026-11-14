"""The multi-agent system as a decider — the same contract as the single agent and the baseline:

    run(engine, event, waiting) -> list[Decision]

One case at a time: the patients the engine hands over (one at an admission decision; everyone waiting when a bed
frees). For each case:
  1  every patient is assessed first — the graph of each pauses after the Patient Agent (orchestration/graph.py)
  2  code orders them by the approved rule for patients competing for a bed (placement_rules.yaml,
     competing_for_one_bed): the one who needs the freed bed's type first, then the severe, then the earlier
     admission decision
  3  each patient's graph goes on, in that order; a bed given to one is never proposed to the next (`taken`)
  4  the case's cost cap covers every patient of the case together: before a patient's graph goes on, what the
     others spent is added to its state
  5  the trace: every agent run (its role, cost and claims), every handoff check, the placement steps reached —
     handed to the measurement (tracing.collect)
  6  the state of every patient's graph is saved after every node: in memory, or outputs/checkpoints.sqlite when a
     script asks for it (save_checkpoints)

The agents: `use_agents(...)` — scripted ones in the tests (an Agents), or the real ones (agents/orchestra_agents.py:
a builder called once per case with the engine, the case's trace and the run's budget). start_run() uses the real
ones unless a test already gave its own — and only a paid run calls start_run (evaluation/deciders.paid_run), so
the real agents are never reached by mistake. The run's budget: start_run() / end_run(), as the single agent's;
when it is spent, RunStopped stops the script.

Owner: Lea Gur-Arie
"""
import sqlite3
from functools import partial
from typing import Callable

from langgraph.checkpoint.memory import InMemorySaver

from ed_admission import config
from ed_admission.agents import budget, orchestra_agents
from ed_admission.agents.agent_loop import LangChainCaller
from ed_admission.agents.budget import RunStopped, agents_config
from ed_admission.contracts.agent_outputs import PatientTask
from ed_admission.environment.decision import Action, Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.observability.tracing import Claim, Trace, collect
from ed_admission.orchestration.graph import Agents, build_graph, checkpoint_serde
from ed_admission.orchestration.state import CaseState

DECIDER = "multi_agent"
_agents: Agents | Callable[..., Agents] | None = None   # scripted agents, or the real agents' builder
_real = False                                            # the real agents were set by start_run
_run_budget: budget.RunBudget | None = None
_saver = None


def use_agents(agents: Agents | Callable[..., Agents] | None) -> None:
    """An Agents (scripted, in the tests), or a builder (engine, trace, run_budget) -> Agents, called per case."""
    global _agents
    _agents = agents


def start_run() -> budget.RunBudget:
    """Open the run's budget (the prices are checked first), and use the real agents unless a test gave its own —
    one LangChain caller for the whole run. Called once by a script before the first case."""
    global _run_budget, _agents, _real
    _run_budget = budget.start_run(DECIDER)
    if _agents is None:
        _agents, _real = partial(orchestra_agents.build, caller=LangChainCaller()), True
    return _run_budget


def end_run() -> None:
    global _run_budget, _agents, _real
    _run_budget = None
    if _real:
        _agents, _real = None, False


def save_checkpoints(path=None) -> None:
    """Save every graph's state after every node to an SQLite file (outputs/checkpoints.sqlite by default)."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    global _saver
    path = path or config.outputs_dir() / "checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    _saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False),   # the wards of a rung run in threads
                         serde=checkpoint_serde())


def _agents_for(trace: Trace, engine) -> Agents:
    """This case's agents. The real ones record themselves in the trace and their loop adds every model call to the
    run's budget; scripted ones are wrapped to do the same — so every cost is counted once."""
    if isinstance(_agents, Agents):
        return _traced(_agents, trace)
    return _agents(engine, trace, _run_budget)


def _traced(agents: Agents, trace: Trace) -> Agents:
    """Scripted agents, each run recorded in the trace and added to the run's budget — a spent run stops here."""
    def wrap(role, call, claims=lambda answer: []):
        def run(task):
            if _run_budget is not None and _run_budget.exhausted:
                raise RunStopped("the run's budget is spent")
            answer, cost = call(task)
            trace.agent_call(role, task if role != "judge" else "the case file", answer, model="", prompt_version="",
                             cost_usd=cost, claims=claims(answer), model_calls=1)
            if _run_budget is not None:
                _run_budget.add(cost)
            return answer, cost
        return run

    def findings(answer):
        found = answer.get("findings", []) if isinstance(answer, dict) else getattr(answer, "findings", [])
        return [Claim(f["claim"], f["quote"]) if isinstance(f, dict) else Claim(f.claim, f.quote) for f in found]

    return Agents(patient=wrap("patient", agents.patient, findings), department=wrap("department", agents.department),
                  judge=wrap("judge", agents.judge))


# The approved rule for patients competing for a bed (placement_rules.yaml, competing_for_one_bed), as sort keys —
# a smaller key comes first. Written here from the assessment, never from the evaluation code: a decider reads no
# answer key (tests/test_no_hints.py).
ORDER_KEYS = {
    "needs_the_bed_type_first": lambda assessment, freed, decided: freed is not None and assessment.bed_type != freed,
    "severe_first": lambda assessment, freed, decided: not assessment.never_waits,
    "earlier_decision_first": lambda assessment, freed, decided: decided,
}


def _order(states: dict[str, CaseState], waiting: list[WaitingPatient], event: Event, engine) -> list[str]:
    """The patients in the rule's order. A patient whose assessment failed goes last: they are escalated."""
    freed = (engine.hospital.ward(event.ward_id).bed(event.bed_number).bed_type.value
             if event.ward_id and event.bed_number else None)
    rule = config.placement_rules()["competing_for_one_bed"]

    def key(patient: WaitingPatient):
        assessment = states[patient.patient_id].assessment
        if assessment is None:
            return (True,)
        return (False, *(ORDER_KEYS[name](assessment, freed, patient.decided_at) for name in rule))
    return [p.patient_id for p in sorted(waiting, key=key)]


def run(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    """One case: a decision for every waiting patient, in the order the rule gives."""
    if _agents is None:
        raise RuntimeError("the multi-agent system has no agents: use_agents(...) first")
    if _run_budget is not None and _run_budget.exhausted:
        raise RunStopped("the run's budget is spent")
    patients = [patient.patient_id for patient in waiting]
    if not patients:
        return []

    trace = Trace(case_id="+".join(patients), decider=DECIDER)
    taken: set[tuple[str, int]] = set()
    saver = _saver or InMemorySaver(serde=checkpoint_serde())
    graph = build_graph(_agents_for(trace, engine), engine, saver, taken, interrupt_after=["patient_agent"])
    threads = {pid: {"configurable": {"thread_id": f"{trace.case_id}:{pid}:{engine.clock}"},
                     "max_concurrency": agents_config().parallel_agent_calls} for pid in patients}

    # 1 — every patient assessed first; each graph pauses after the Patient Agent
    states = {}
    for patient in waiting:
        task = PatientTask(patient_id=patient.patient_id, decided_at=patient.decided_at)
        graph.invoke(CaseState(task=task), threads[patient.patient_id])
        states[patient.patient_id] = CaseState.model_validate(graph.get_state(threads[patient.patient_id]).values)

    # 2, 3, 4 — in the rule's order, each graph goes on; the case's cost cap covers them all
    decisions = []
    for pid in _order(states, waiting, event, engine):
        others = sum(CaseState.model_validate(graph.get_state(threads[other]).values).cost_usd
                     for other in patients if other != pid)
        if others:
            graph.update_state(threads[pid], {"cost_usd": others})
        graph.invoke(None, threads[pid])
        final = CaseState.model_validate(graph.get_state(threads[pid]).values)
        decision = final.decision
        if decision.action in (Action.PLACE, Action.TEMPORARY_PLACE):
            taken.add((decision.ward_id, decision.bed_number))
        decisions.append(decision)
        _milestones(trace, final)

    trace.ended = _how_it_ended(trace)
    collect(trace)
    return decisions


def _how_it_ended(trace: Trace) -> str:
    """The case's `ended`, as the single agent's: final when every real agent run ended with its answer; otherwise
    the first other status (no_final, refused, failed, budget) — so costs.csv tells a case some agent did not finish
    from one that went as planned. Scripted agents record no status: final."""
    for step in trace.steps:
        status = step.output.get("status") if step.kind == "agent_call" and isinstance(step.output, dict) else None
        if status not in (None, "final"):
            return status
    return "final"


def _milestones(trace: Trace, state: CaseState) -> None:
    """The placement steps this patient's graph really reached, and every handoff check."""
    for check in state.handoff_checks:
        trace.tool_call("coordinator", "validate_handoff", {"stage": check.stage, "attempt": check.attempt},
                        check.model_dump(mode="json"), seconds=0.0)
    reached = {"patient_assessed": state.assessment is not None,
               "availability_collected": state.target is not None,
               "ranked": state.target is not None,
               "rules_checked": state.proposal is not None and state.proposal.code_checks is not None
                                or state.decision is not None and state.decision.action == Action.ESCALATE,
               "decided": state.decision is not None}
    for step, was_reached in reached.items():
        if was_reached:
            trace.milestone(step, state.task.patient_id)
