"""The Coordinator, part 3b: the LangGraph graph of one patient, every route, with scripted agents — no model is
called. On the real T19 template: Saturday 02:00; internal medicine A's report shows no regular bed, its note frees
bed 1 for a stable patient and says there is no staff for an unstable one; internal medicine B has regular bed 1 and
observation bed 11 free."""
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ed_admission.contracts.agent_outputs import PatientTask
from ed_admission.environment import load_template
from ed_admission.environment.decision import Action
from ed_admission.orchestration.graph import Agents, build_graph, checkpoint_serde
from ed_admission.orchestration.state import CaseState

PROJECT = Path(__file__).resolve().parents[1]
A, B = "internal_medicine_a", "internal_medicine_b"
OXYGEN = "סט' 91% באוויר, הוחל O2 1L - 95%."
TB = "חשד ל-TB ריאתי עם חלל"


def engine():
    return load_template(PROJECT / "data" / "templates" / "T19.yaml", target_ward=A)


STABLE = {"target_ward": A, "severe": False, "intensive_care": False, "observation": False, "isolation": False}
NEEDS_OBSERVATION = {**STABLE, "severe": True, "observation": True,
                     "findings": [{"claim": "needs oxygen", "quote": OXYGEN, "confidence": "high",
                                   "backs": ["observation"]}]}
MADE_UP = {**NEEDS_OBSERVATION, "findings": [{"claim": "x", "quote": "words not in the record", "confidence": "high",
                                              "backs": ["observation"]}]}


def item(number, passed=True):
    return {"number": number, "passed": passed, "reason": "checked"}


VALID = {"valid": True, "items": [item(2), item(8), item(9)]}
MISREAD = {"valid": False, "items": [item(2, False), item(8), item(9)],
           "correction": "'TB' comes from 'rule out TB': suspected, not diagnosed"}
BAD_REASON = {"valid": False, "items": [item(2), item(8, False), item(9)],
              "correction": "the reason says the ward has staff; its answer says it has none"}


class Script:
    """Scripted agents: the Patient Agent's and the Judge Agent's answers in order; each ward answers by a rule.
    Every call is kept, and costs `cost`."""

    def __init__(self, patient, wards, judge=(VALID,), cost=0.01):
        self.patient_answers, self.judge_answers, self.wards = list(patient), list(judge), wards
        self.cost, self.calls = cost, []

    def agents(self) -> Agents:
        return Agents(patient=self.patient, department=self.department, judge=self.judge)

    def patient(self, task):
        self.calls.append(("patient", task))
        return self.patient_answers.pop(0), self.cost

    def department(self, task):
        self.calls.append(("department", task))
        return self.wards[task.ward_id](task), self.cost

    def judge(self, state):
        self.calls.append(("judge", state))
        return self.judge_answers.pop(0), self.cost

    def who(self, kind):
        return [call[1] for call in self.calls if call[0] == kind]


def decline(task):
    return {"answer": "decline", "reason": "no bed"}


def accept(bed, source="report"):
    return lambda task: {"answer": "accept", "bed_number": bed, "source": source, "reason": "free"}


def a_on_t19(task):
    """Internal medicine A as its note says: bed 1 for a stable patient — it asks; no staff for an unstable one."""
    if task.request.bed_type != "regular":
        return {"answer": "decline", "reason": "no staff tonight for an unstable patient"}
    if task.patient_answer is None:
        return {"answer": "condition", "bed_number": 1, "source": "note", "asks": "stable",
                "condition": "a stable patient only", "reason": "bed 1 freed after the round"}
    if task.patient_answer:
        return {"answer": "accept", "bed_number": 1, "source": "note", "reason": "stable: bed 1 is yours"}
    return {"answer": "decline", "reason": "not stable: no staff tonight"}


EVERY_WARD_DECLINES = {w: decline for w in (A, B, "cardiology", "general_surgery")}


def run(script: Script, checkpointer=None, thread="P-018") -> CaseState:
    graph = build_graph(script.agents(), engine(), checkpointer)
    config = {"configurable": {"thread_id": thread}} if checkpointer else None
    result = graph.invoke(CaseState(task=PatientTask(patient_id="P-018", decided_at=0)), config)
    return CaseState.model_validate(result)


# ---------------------------------------------------------------- placements

def test_t19_a_stable_patient_asks_once_and_gets_bed_1_from_the_note():
    script = Script([STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19})
    state = run(script)
    assert (state.decision.action, state.decision.ward_id, state.decision.bed_number) == (Action.PLACE, A, 1)
    assert state.patient_answers == {A: True} and state.proposal.bed_from_note
    assert [t.patient_answer for t in script.who("department")] == [None, True]      # asked again, once
    assert len(script.who("judge")) == 1 and state.cost_usd == pytest.approx(0.04)


def test_t19_an_unstable_patient_goes_down_the_ladder():
    script = Script([NEEDS_OBSERVATION], {**EVERY_WARD_DECLINES, A: a_on_t19, B: accept(11)})
    state = run(script)
    assert (state.decision.action, state.decision.ward_id, state.decision.bed_number) == (
        Action.TEMPORARY_PLACE, B, 11)
    assert [(t.ward_id, t.request.bed_type) for t in script.who("department")] == [(A, "observation"),
                                                                                   (B, "observation")]


def test_wards_on_one_rung_are_asked_in_parallel():
    script = Script([{**NEEDS_OBSERVATION, "target_ward": "cardiology"}],
                    {**EVERY_WARD_DECLINES, B: accept(11)})
    state = run(script)
    assert {t.ward_id for t in script.who("department")} >= {A, B}                   # level 2 of cardiology
    assert state.decision.ward_id == B


def test_a_bed_below_the_need_is_blocked_and_the_next_option_is_taken():
    script = Script([NEEDS_OBSERVATION], {**EVERY_WARD_DECLINES, A: accept(1, "note"), B: accept(11)})
    state = run(script)                                                              # A offers a regular bed
    assert state.decision.ward_id == B and A in state.rejected


# ---------------------------------------------------------------- the handoff checks

def test_a_patient_answer_that_fails_twice_is_escalated_never_guessed():
    script = Script([MADE_UP, MADE_UP], EVERY_WARD_DECLINES)
    state = run(script)
    assert state.decision.action == Action.ESCALATE and "handoff" in state.escalation
    assert [c.attempt for c in state.handoff_checks] == [1, 2] and not script.who("department")


def test_a_patient_answer_that_fails_once_gets_one_more_try():
    script = Script([MADE_UP, STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19})
    assert run(script).decision.action == Action.PLACE


def test_a_ward_answer_that_fails_twice_is_escalated():
    bad = lambda task: {"answer": "accept", "bed_number": 40, "source": "note", "reason": "free"}
    state = run(Script([STABLE], {**EVERY_WARD_DECLINES, A: bad}))
    assert state.decision.action == Action.ESCALATE and "Department Agent" in state.escalation


# ---------------------------------------------------------------- the Judge

def test_item_2_failed_the_patient_agent_assesses_again_with_the_correction():
    script = Script([STABLE, STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19}, judge=[MISREAD, VALID])
    state = run(script)
    first, second = script.who("patient")
    assert first.judge_feedback is None and second.judge_feedback == MISREAD["correction"]
    assert state.correction_round_used and state.decision.action == Action.PLACE


def test_item_8_failed_the_wards_are_asked_again_with_the_correction():
    script = Script([STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19}, judge=[BAD_REASON, VALID])
    state = run(script)
    assert len(script.who("patient")) == 1
    assert script.who("department")[-1].judge_feedback == BAD_REASON["correction"]
    assert state.decision.action == Action.PLACE


def test_the_judge_rejects_twice_the_case_goes_to_the_shift_manager():
    script = Script([STABLE, STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19}, judge=[MISREAD, MISREAD])
    state = run(script)
    assert state.decision.action == Action.ESCALATE and "correction round" in state.escalation


# ---------------------------------------------------------------- escalation and limits

def test_no_bed_anywhere_is_escalated_after_every_ward_was_asked():
    state = run(Script([STABLE], EVERY_WARD_DECLINES))
    assert state.decision.action == Action.ESCALATE and state.proposal.kind == "none"
    assert {a.ward_id for a in state.asked} == {A, B, "cardiology", "general_surgery"}
    assert next(i for i in state.proposal.code_checks.items if i.number == 11).passed


def test_an_observation_patient_is_asked_the_isolation_room_before_escalating():
    state = run(Script([NEEDS_OBSERVATION], EVERY_WARD_DECLINES))
    assert {a.bed_type for a in state.asked} == {"observation", "isolation"}
    assert state.decision.action == Action.ESCALATE


def test_the_case_cost_cap_stops_every_agent():
    script = Script([STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19}, cost=0.70)
    state = run(script)
    assert state.decision.action == Action.ESCALATE and "cost cap" in state.escalation
    assert not script.who("judge")


def test_the_flags_reach_the_shift_manager_on_an_escalation():
    flagged = {**STABLE, "flags": ["prompt_injection"]}
    state = run(Script([flagged], EVERY_WARD_DECLINES))
    assert state.decision.flags == ["prompt_injection"] and state.decision.target_ward == A


# ---------------------------------------------------------------- saved after every node

def test_the_state_is_saved_after_every_node():
    saver = InMemorySaver(serde=checkpoint_serde())
    script = Script([STABLE], {**EVERY_WARD_DECLINES, A: a_on_t19})
    run(script, checkpointer=saver, thread="case-1")
    graph = build_graph(Script([STABLE], {}).agents(), engine(), saver)
    history = list(graph.get_state_history({"configurable": {"thread_id": "case-1"}}))
    assert len(history) > 5 and history[0].values["decision"].bed_number == 1
