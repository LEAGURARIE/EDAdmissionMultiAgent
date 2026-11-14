"""The multi-agent system as a decider (agents/multi_agent.py), part 3c: the same contract as the single agent —
run(engine, event, waiting) -> list[Decision] — with scripted agents on T19. No model is called."""
import sqlite3

import pytest

from ed_admission.agents import multi_agent
from ed_admission.agents.budget import RunBudget, RunStopped
from ed_admission.environment.decision import Action
from ed_admission.environment.events import Event, EventType
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.observability import tracing
from ed_admission.orchestration.graph import Agents
from test_coordinator_graph import (A, B, EVERY_WARD_DECLINES, NEEDS_OBSERVATION, STABLE, VALID, accept, a_on_t19,
                                    engine)

ADMISSION = Event(time=0, event_type=EventType.ADMISSION_DECISION)


class Patients:
    """Scripted agents for a case of several patients: each patient's assessment by id; every call kept."""

    def __init__(self, assessments: dict, wards: dict, cost: float = 0.01):
        self.assessments, self.wards, self.cost, self.calls = assessments, wards, cost, []

    def agents(self) -> Agents:
        return Agents(patient=self.patient, department=self.department, judge=self.judge)

    def patient(self, task):
        self.calls.append(("patient", task.patient_id))
        return self.assessments[task.patient_id], self.cost

    def department(self, task):
        self.calls.append(("department", task.ward_id))
        return self.wards[task.ward_id](task), self.cost

    def judge(self, state):
        self.calls.append(("judge", state.task.patient_id))
        return VALID, self.cost


@pytest.fixture(autouse=True)
def clean():
    tracing.take()
    yield
    multi_agent.use_agents(None)
    multi_agent.end_run()
    multi_agent._saver = None
    tracing.take()


def decide(script: Patients, waiting, event=ADMISSION, hospital=None):
    multi_agent.use_agents(script.agents())
    return multi_agent.run(hospital or engine(), event, waiting)


def test_one_patient_one_decision_and_a_complete_trace():
    script = Patients({"P-018": STABLE}, {**EVERY_WARD_DECLINES, A: a_on_t19})
    decisions = decide(script, [WaitingPatient(patient_id="P-018", decided_at=0)])
    assert [(d.patient_id, d.action, d.ward_id, d.bed_number) for d in decisions] == [("P-018", Action.PLACE, A, 1)]
    [trace] = tracing.take()
    assert trace.decider == "multi_agent" and trace.complete and trace.system == "multi_agent"
    assert set(trace.cost_by_role) == {"patient", "department", "judge"}
    assert any(step.name == "validate_handoff" for step in trace.steps)


def test_two_patients_one_bed_the_earlier_decision_gets_it_the_other_goes_down_the_ladder():
    script = Patients({"P-001": STABLE, "P-002": STABLE}, {**EVERY_WARD_DECLINES, A: a_on_t19, B: accept(1)})
    waiting = [WaitingPatient(patient_id="P-002", decided_at=20), WaitingPatient(patient_id="P-001", decided_at=5)]
    first, second = decide(script, waiting)
    assert (first.patient_id, first.ward_id, first.bed_number) == ("P-001", A, 1)            # the earlier decision
    assert (second.patient_id, second.action, second.ward_id) == ("P-002", Action.TEMPORARY_PLACE, B)
    assert [who for kind, who in script.calls if kind == "patient"] == ["P-002", "P-001"]   # both assessed first


def test_the_severe_patient_goes_first_whoever_was_decided_earlier():
    script = Patients({"P-001": STABLE, "P-018": NEEDS_OBSERVATION},      # P-018's quote is in P-018's record
                      {**EVERY_WARD_DECLINES, A: a_on_t19, B: accept(11)})
    waiting = [WaitingPatient(patient_id="P-001", decided_at=0), WaitingPatient(patient_id="P-018", decided_at=30)]
    assert [d.patient_id for d in decide(script, waiting)] == ["P-018", "P-001"]


def test_the_case_cost_cap_covers_every_patient_of_the_case():
    script = Patients({"P-001": STABLE, "P-002": STABLE}, {**EVERY_WARD_DECLINES, A: a_on_t19, B: accept(1)},
                      cost=0.25)
    first, second = decide(script, [WaitingPatient(patient_id="P-001", decided_at=0),
                                    WaitingPatient(patient_id="P-002", decided_at=5)])
    assert second.action == Action.ESCALATE and "cost cap" in second.reason
    [trace] = tracing.take()
    assert trace.cost_usd <= 1.20 + 0.25 * 2                       # stops within the cap's last runs


def test_without_agents_the_decider_refuses_to_guess():
    multi_agent.use_agents(None)
    with pytest.raises(RuntimeError, match="no agents"):
        multi_agent.run(engine(), ADMISSION, [WaitingPatient(patient_id="P-018", decided_at=0)])


def test_a_spent_run_budget_stops_the_script():
    multi_agent._run_budget = RunBudget("multi_agent", cap_usd=0.015)
    script = Patients({"P-018": STABLE}, {**EVERY_WARD_DECLINES, A: a_on_t19})
    with pytest.raises(RunStopped):
        decide(script, [WaitingPatient(patient_id="P-018", decided_at=0)])


def test_the_state_of_every_node_is_saved_to_sqlite(tmp_path):
    multi_agent.save_checkpoints(tmp_path / "checkpoints.sqlite")
    script = Patients({"P-018": STABLE}, {**EVERY_WARD_DECLINES, A: a_on_t19})
    decide(script, [WaitingPatient(patient_id="P-018", decided_at=0)])
    rows = sqlite3.connect(tmp_path / "checkpoints.sqlite").execute("select count(*) from checkpoints").fetchone()[0]
    assert rows > 5
