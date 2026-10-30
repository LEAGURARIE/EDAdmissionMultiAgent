"""The single agent (agents/single_agent.py) with a scripted model — no call leaves the computer, nothing is paid.
The checks are on what happens: the decisions the engine gets, what is escalated in code and why, the trace, the
run's budget, and what the task tells the agent."""
from pathlib import Path

import pytest

from ed_admission.agents import agent_loop, single_agent
from ed_admission.agents.budget import RunBudget, RunStopped
from ed_admission.environment import load_template
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.evaluation import run_cases
from ed_admission.observability import tracing
from test_agent_loop import ScriptedModel, answer, text, use

PROJECT = Path(__file__).resolve().parents[1]
WARD = "internal_medicine_a"
ASSESSMENT = {"target_ward": WARD, "binding_request": False, "severe": False, "intensive_care": False,
              "observation": False, "isolation": False, "flags": [],
              "findings": [{"claim": "takes amlodipine", "quote": "amlodipine 5mg"}]}


def engine():
    return load_template(PROJECT / "data" / "templates" / "T1.yaml", target_ward=WARD)


def waiting(*patients):
    hospital = engine()
    return [WaitingPatient(patient_id=pid, decided_at=hospital.clock - 10) for pid in patients]


def free_beds(count=1):
    """Free regular beds of the target ward, read from the report of an identical hospital — so the script can
    name them."""
    report = engine().department_status(WARD)
    beds = [b["bed"] for b in report["free_beds"] if b["bed_type"] == "regular"]
    assert len(beds) >= count, "template T1 has fewer free regular beds than the test needs"
    return beds[:count]


def assess(pid, tool_id):
    return use("record_assessment", tool_id, patient_id=pid, **ASSESSMENT)


def place(pid, bed, tool_id):
    return use("decide", tool_id, patient_id=pid, action="place", target_ward=WARD, ward_id=WARD, bed_number=bed,
               reason="A regular bed free in the target ward.")


@pytest.fixture
def model(monkeypatch):
    """Put a scripted model where the real one would be; a clean trace collection and no run budget."""
    tracing.take()
    holder = {}

    def script(*answers):
        holder["model"] = ScriptedModel(*answers)
        monkeypatch.setattr(agent_loop, "_client", lambda: holder["model"])
        return holder["model"]
    yield script
    single_agent.end_run()
    tracing.take()


# ---------- a whole case ----------
def test_one_patient_from_the_record_to_a_decision(model):
    bed = free_beds()[0]
    scripted = model(answer(use("read_patient_record", "a", patient_id="P-001"),
                            use("compute_news2", "b", patient_id="P-001"), use("clinical_lists", "c")),
                     answer(assess("P-001", "d"), use("all_department_status", "e")),
                     answer(use("rank_placements", "f", patient_id="P-001")),
                     answer(use("department_view", "g", ward_id=WARD)),
                     answer(place("P-001", bed, "h")))
    decisions = single_agent.run(engine(), None, waiting("P-001"))
    assert [(d.patient_id, d.action.value, d.bed_number) for d in decisions] == [("P-001", "place", bed)]
    assert len(scripted.requests) == 5                          # the loop ended when the patient was decided

    trace, = tracing.take()
    assert trace.ended == "final" and trace.system == "single_agent" and trace.complete
    assert trace.model_calls == 5 and trace.cost_usd > 0
    assert [c.quote for c in trace.claims] == ["amlodipine 5mg"]            # measured for groundedness
    call = next(s for s in trace.steps if s.kind == "agent_call")
    assert call.prompt_version == single_agent.system_prompt()[1] and call.model == "claude-opus-5-5"


def test_two_patients_are_decided_in_the_order_the_agent_chose(model):
    first, second = free_beds(2)
    model(answer(assess("P-001", "a"), assess("P-002", "b")),
          answer(use("rank_placements", "c", patient_id="P-002"), use("rank_placements", "d", patient_id="P-001")),
          answer(place("P-002", first, "e"), place("P-001", second, "f")))
    decisions = single_agent.run(engine(), None, waiting("P-001", "P-002"))
    assert [d.patient_id for d in decisions] == ["P-002", "P-001"]


def test_a_second_decision_for_the_same_patient_is_refused(model):
    first, second = free_beds(2)
    model(answer(assess("P-001", "a")), answer(place("P-001", first, "b"), place("P-001", second, "c")))
    decisions = single_agent.run(engine(), None, waiting("P-001"))
    assert [d.bed_number for d in decisions] == [first]
    loop = next(s for s in tracing.take()[0].steps if s.kind == "agent_call").output["loop"]
    assert "already has a decision" in [s for s in loop if s.get("tool") == "decide"][1]["error"]


# ---------- what is left is escalated in code ----------
def test_one_reminder_then_the_agent_decides(model):
    bed = free_beds()[0]
    scripted = model(answer(assess("P-001", "a")), answer(text("I will think about it."), stop="end_turn"),
                     answer(place("P-001", bed, "b")))
    decisions = single_agent.run(engine(), None, waiting("P-001"))
    assert decisions[0].action.value == "place"
    assert "Still without a decision: P-001" in scripted.requests[2]["messages"][-1]["content"]


def test_after_the_reminder_what_is_left_goes_to_the_shift_manager(model):
    model(answer(assess("P-001", "a")), answer(text("Done."), stop="end_turn"), answer(text("Done."), stop="end_turn"))
    decisions = single_agent.run(engine(), None, waiting("P-001"))
    assert decisions[0].action.value == "escalate" and "stopped without deciding" in decisions[0].reason
    trace, = tracing.take()
    assert trace.ended == "no_final" and not trace.complete                # the agent did not decide it


@pytest.mark.parametrize("last, words", [
    (answer(use("read_patient_record", patient_id="P-001"), tokens_in=1_000_000), "reached its budget"),
    (answer(stop="refusal"), "declined"),
])
def test_a_case_that_stops_escalates_every_patient_left(model, last, words):
    model(last)
    decisions = single_agent.run(engine(), None, waiting("P-001", "P-002"))
    assert [d.action.value for d in decisions] == ["escalate", "escalate"]
    assert all(words in d.reason for d in decisions)


def test_an_escalation_by_the_code_keeps_the_flags_of_the_assessment(model):
    model(answer(use("record_assessment", "a", patient_id="P-001", **{**ASSESSMENT, "flags": ["prompt_injection"]})),
          answer(stop="refusal"))
    decision, = single_agent.run(engine(), None, waiting("P-001"))
    assert decision.action.value == "escalate" and [f.value for f in decision.flags] == ["prompt_injection"]


# ---------- the run's budget ----------
def test_a_spent_run_stops_before_any_call(model):
    scripted = model()
    single_agent._run_budget = RunBudget("single_agent", cap_usd=0.01)
    single_agent._run_budget.add(0.02)
    with pytest.raises(RunStopped):
        single_agent.run(engine(), None, waiting("P-001"))
    assert scripted.requests == []


def test_the_case_that_spends_the_run_is_kept_then_the_run_stops(model):
    model(answer(use("read_patient_record", patient_id="P-001"), tokens_in=10_000))
    single_agent._run_budget = RunBudget("single_agent", cap_usd=0.01)
    with pytest.raises(RunStopped):
        single_agent.run(engine(), None, waiting("P-001"))
    trace, = tracing.take()                                     # its cost is kept for costs.csv
    assert trace.ended == "run_budget" and trace.cost_usd > 0


def test_the_case_runner_lets_a_stopped_run_through():
    def spent(engine, event, waiting):
        raise RunStopped("spent")
    with pytest.raises(RunStopped):
        run_cases(spent, evaluation_set="first")


# ---------- what the agent is told ----------
def test_the_task_tells_only_the_time_the_ids_and_when_each_admission_was_decided():
    hospital = engine()
    task = single_agent.task_text(hospital, waiting("P-001", "P-002"))
    lines = task.splitlines()
    assert lines[0].startswith("The time now: ") and lines[-1] == "Give every one of them a decision or an escalation."
    assert [line.split(":")[0] for line in lines[2:-1]] == ["- P-001", "- P-002"]
    assert len(lines) == 5                                      # nothing else: no event, no note, no need


def test_the_prompt_has_its_six_components_and_a_version():
    prompt, version = single_agent.system_prompt()
    headings = [line for line in prompt.splitlines() if line.startswith("# ")]
    assert headings == ["# Persona", "# Goal", "# Tools", "# Process", "# Constraints", "# Output format"]
    assert len(version) == 10


# ---------- a paid decider runs only when asked for ----------
def test_the_scripts_refuse_the_single_agent_without_paid():
    from ed_admission.evaluation import deciders
    with pytest.raises(deciders.PaidWithoutApproval):
        deciders.resolve("single_agent")
    assert deciders.resolve("single_agent", paid=True) is single_agent.run
    assert not deciders.is_paid(deciders.resolve("code_coordinator"))


def test_a_paid_run_opens_the_budget_after_checking_the_prices_and_closes_it(monkeypatch):
    from ed_admission.agents import prices
    from ed_admission.evaluation import deciders
    checked = []
    monkeypatch.setattr(prices, "ensure_current", lambda: checked.append(True))
    with deciders.paid_run(single_agent.run):
        assert checked == [True] and single_agent._run_budget.cap_usd == 12.0
    assert single_agent._run_budget is None


# ---------- the case state ----------
def test_the_case_state_says_what_is_done_and_what_is_left(model):
    bed = free_beds()[0]
    scripted = model(answer(assess("P-001", "a"), use("rank_placements", "b", patient_id="P-001"),
                            use("department_view", "c", ward_id=WARD)),
                     answer(place("P-001", bed, "d")),
                     answer(use("escalate_to_human", "e", patient_id="P-002", reason="No record could be read.")))
    single_agent.run(engine(), None, waiting("P-001", "P-002"))
    assert "P-001: assessed" in scripted.requests[2]["messages"][-1]["content"]
    assert "decided: place" in scripted.requests[2]["messages"][-1]["content"]
    state = scripted.requests[1]["messages"][-1]
    assert state["role"] == "system"
    lines = state["content"].splitlines()
    assert lines[0] == "Case state"
    assert lines[1] == (f"- P-001: assessed (bed: regular, may wait: yes, target: {WARD}) · ranked · no decision yet")
    assert lines[2] == "- P-002: not assessed yet"
    assert lines[3] == f"Wards whose notes you read: {WARD}." and lines[4] == "Model calls: 1 of 15."


def test_groundedness_reads_each_claim_against_its_own_patient_in_a_case_of_two(model):
    """2026-10-15: a paired case's trace is named "P-001+P-002"; each claim is checked against the record of the
    patient it was made about — before, the first test run stopped on a record named "P-001+P-002"."""
    from ed_admission.evaluation import metrics
    first, second = free_beds(2)
    model(answer(assess("P-001", "a"), use("record_assessment", "b", patient_id="P-002", **{**ASSESSMENT,
                 "findings": [{"claim": "a claim", "quote": "a sentence no record has"}]})),
          answer(place("P-001", first, "c"), place("P-002", second, "d")))
    single_agent.run(engine(), None, waiting("P-001", "P-002"))
    trace, = tracing.take()
    assert trace.case_id == "P-001+P-002"
    assert metrics.groundedness([trace]) == 0.5                 # P-001's quote found, P-002's invented one not
