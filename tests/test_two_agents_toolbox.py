"""Two agents and a Coordinator — the toolbox: who may use which tool, the Patient Agent run through assess_patient,
and the hard rules checked in code by decide (tools/decide.py)."""
from pathlib import Path

import pytest

from ed_admission.contracts.agent_outputs import Finding, PatientAssessment
from ed_admission.contracts.negotiation import WardRequest
from ed_admission.environment import load_template
from ed_admission.environment.decision import Decision, Flag
from ed_admission.observability.tracing import Trace
from ed_admission.tools.decide import RuleViolation, broken_rules
from ed_admission.tools.toolbox import PERMISSIONS, PermissionDenied, Toolbox

PROJECT = Path(__file__).resolve().parents[1]
WARD = "internal_medicine_a"          # beds 1-10 regular, 11-12 observation, 13 isolation


def engine():
    return load_template(PROJECT / "data" / "templates" / "T1.yaml", target_ward=WARD)


def assessment(**changes) -> PatientAssessment:
    needs = {"target_ward": WARD, "severe": False, "intensive_care": False, "observation": False, "isolation": False}
    return PatientAssessment(**{**needs, **changes})


def place(bed: int, **changes) -> Decision:
    return Decision(**{"patient_id": "P-001", "action": "place", "ward_id": WARD, "bed_number": bed,
                       "target_ward": WARD, **changes})


# ---------------------------------------------------------------- who may use which tool

def test_each_role_has_its_own_tools():
    tools = {role: {tool for tool, roles in PERMISSIONS.items() if role in roles}
             for role in ("patient", "department", "coordinator")}
    assert tools["patient"] == {"read_patient_record", "compute_news2", "clinical_lists"}
    assert tools["department"] == {"department_view"}
    assert tools["coordinator"] == {"assess_patient", "all_department_status", "rank_placements", "ask_ward",
                                    "hold_bed", "release_hold", "escalate_to_human", "decide"}


def test_the_coordinator_never_reads_the_record_itself():
    coordinator = Toolbox("coordinator", engine())
    for tool in (coordinator.read_patient_record, coordinator.compute_news2):
        with pytest.raises(PermissionDenied):
            tool("P-001")
    with pytest.raises(PermissionDenied):
        coordinator.clinical_lists()


def test_the_patient_agent_sees_no_ward():
    patient = Toolbox("patient", engine())
    assert patient.read_patient_record("P-001")["case_id"] == "P-001"
    with pytest.raises(PermissionDenied):
        patient.all_department_status()
    with pytest.raises(PermissionDenied):
        patient.decide(place(1))


def test_no_agent_can_call_another_agent_only_the_coordinator_can():
    """No handoff between agents: the tools that run an agent — assess_patient (the Patient Agent) and ask_ward (a
    Department Agent) — belong to the Coordinator alone, and neither agent can read what the other reads."""
    patient = Toolbox("patient", engine())
    department = Toolbox("department", engine(), ward_id=WARD)
    request = WardRequest(bed_type="regular", severe=False, observation=False, isolation=False, target_ward=WARD)
    for agent in (patient, department):
        with pytest.raises(PermissionDenied):
            agent.assess_patient("P-001")
        with pytest.raises(PermissionDenied):
            agent.ask_ward(WARD, request)
    with pytest.raises(PermissionDenied):
        patient.department_view()                                    # the Patient Agent never reaches a ward
    with pytest.raises(PermissionDenied):
        department.read_patient_record("P-001")                      # a Department Agent never reaches the record
    for tool in ("assess_patient", "ask_ward"):
        assert not PERMISSIONS[tool] & {"patient", "department"}


# ---------------------------------------------------------------- assess_patient

def test_assess_patient_runs_the_patient_agent_with_its_own_toolbox_and_checks_the_quotes():
    seen = {}

    def patient_agent(toolbox, patient_id):
        seen["role"] = toolbox.role
        record = toolbox.read_patient_record(patient_id)
        real = record["physician_note"].split()[0]
        return assessment(findings=[Finding(claim="in the record", quote=real),
                                    Finding(claim="made up", quote="words that are not in the record")])

    trace = Trace("P-001", "test")
    coordinator = Toolbox("coordinator", engine(), trace=trace, patient_agent=patient_agent)
    found = coordinator.assess_patient("P-001")
    assert seen["role"] == "patient"
    assert [f.grounded for f in found.findings] == [True, False]
    assert len(trace.claims) == 2                                   # handed to the trace: groundedness is measured


def test_decide_needs_the_assessment_first():
    with pytest.raises(ValueError, match="assess"):
        Toolbox("coordinator", engine()).decide(place(1))


def test_the_coordinator_cannot_change_what_the_patient_agent_found():
    coordinator = Toolbox("coordinator", engine(), patient_agent=lambda toolbox, pid: assessment(observation=True, severe=True))
    coordinator.assess_patient("P-001")
    with pytest.raises(RuleViolation, match="observation"):
        coordinator.decide(place(1))                                 # a regular bed
    assert coordinator.decide(place(11)).bed_number == 11            # an observation bed


# ---------------------------------------------------------------- the hard rules

@pytest.mark.parametrize("needs, decision, words", [
    ({"observation": True, "severe": True}, place(1), "needs observation"),
    ({"isolation": True}, place(1), "needs isolation"),
    ({"isolation": True}, place(11), "needs isolation"),
    ({"intensive_care": True, "severe": True}, place(11), "needs intensive care"),
    ({}, place(11), "does not need observation"),
    ({}, Decision(patient_id="P-001", action="place", ward_id="intensive_care", bed_number=1,
                  target_ward="intensive_care"), "does not need intensive care"),
    ({"observation": True, "severe": True}, Decision(patient_id="P-001", action="wait", target_ward=WARD),
     "never waits"),
    ({"binding_request": True}, Decision(patient_id="P-001", action="place", ward_id="cardiology", bed_number=1,
                                         target_ward="cardiology"), "requested by the physician"),
    ({"flags": [Flag.PROMPT_INJECTION]}, place(1), "flags"),
])
def test_a_decision_that_breaks_a_hard_rule_is_refused(needs, decision, words):
    assert any(words in reason for reason in broken_rules(decision, assessment(**needs)))


@pytest.mark.parametrize("needs, decision", [
    ({}, place(1)),                                                              # a regular bed, no needs
    ({"observation": True, "severe": True}, place(11)),                          # observation
    ({"observation": True, "severe": True}, place(13)),                          # the isolation room, as a last resort
    ({"isolation": True, "observation": True, "severe": True}, place(13)),       # both needs: the isolation room
    ({"isolation": True}, Decision(patient_id="P-001", action="wait", target_ward=WARD)),   # non-severe may wait
    ({"flags": [Flag.MISSING_VITAL_SIGN]}, place(1, flags=[Flag.MISSING_VITAL_SIGN])),
    ({"observation": True, "severe": True}, Decision.escalate("P-001", "no safe bed")),     # escalating always passes
])
def test_a_safe_decision_passes(needs, decision):
    assert broken_rules(decision, assessment(**needs)) == []


def test_internal_medicine_without_a_or_b_may_become_either_and_nothing_else():
    unnamed = assessment(target_ward="internal_medicine")
    b = Decision(patient_id="P-001", action="place", ward_id="internal_medicine_b", bed_number=1,
                 target_ward="internal_medicine_b")
    assert broken_rules(place(1), unnamed) == [] and broken_rules(b, unnamed) == []
    cardiology = Decision(patient_id="P-001", action="place", ward_id="cardiology", bed_number=1,
                          target_ward="cardiology")
    assert broken_rules(cardiology, unnamed)
