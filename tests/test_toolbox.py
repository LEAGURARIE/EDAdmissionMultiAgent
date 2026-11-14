"""The toolbox: who may use which tool, and the hard rules checked in code by decide (tools/decide.py)."""
from pathlib import Path

import pytest

from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.environment import load_template
from ed_admission.environment.decision import Decision, Flag
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
             for role in ("patient", "department", "judge")}
    assert tools["patient"] == {"read_patient_record", "compute_news2", "clinical_lists", "record_assessment"}
    assert tools["department"] == {"department_view", "answer_ward"}
    assert tools["judge"] == {"read_case", "give_verdict"}


def test_no_agent_holds_or_releases_a_bed():
    assert not {"hold_bed", "release_hold"} & set(PERMISSIONS)       # the environment holds it when the case closes


def test_the_patient_agent_sees_no_ward():
    patient = Toolbox("patient", engine())
    assert patient.read_patient_record("P-001")["case_id"] == "P-001"
    with pytest.raises(PermissionDenied):
        patient.all_department_status()
    with pytest.raises(PermissionDenied):
        patient.decide(place(1))


def test_neither_agent_reads_what_the_other_reads():
    """No handoff between agents: the Coordinator (the graph's code) runs every agent; an agent has no tool that runs
    another one, and neither agent can read what the other reads."""
    patient = Toolbox("patient", engine())
    department = Toolbox("department", engine(), ward_id=WARD)
    with pytest.raises(PermissionDenied):
        patient.department_view()                                    # the Patient Agent never reaches a ward
    with pytest.raises(PermissionDenied):
        department.read_patient_record("P-001")                      # a Department Agent never reaches the record


# ---------------------------------------------------------------- decide

def test_decide_needs_the_assessment_first():
    with pytest.raises(ValueError, match="assess"):
        Toolbox("single_agent", engine()).decide(place(1))


def test_a_decision_cannot_go_below_the_kept_assessment():
    single = Toolbox("single_agent", engine())
    single.record_assessment("P-001", assessment(observation=True, severe=True))
    with pytest.raises(RuleViolation, match="observation"):
        single.decide(place(1))                                      # a regular bed
    assert single.decide(place(11)).bed_number == 11                 # an observation bed


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


# ---------------------------------------------------------------- an intensive-care need sets the target ward

def test_a_patient_who_needs_intensive_care_has_intensive_care_as_the_target():
    """2026-10-15: a system rule in code, for both systems — whatever target the assessment wrote, an intensive-care
    need makes intensive care the target ward of the bed search and of the hard-rule check."""
    needs_it = assessment(target_ward="internal_medicine", severe=True, intensive_care=True)
    assert needs_it.target == "intensive_care" and assessment().target == WARD
    icu = Decision(patient_id="P-001", action="place", ward_id="intensive_care", bed_number=1,
                   target_ward="intensive_care", physician_approval_for_icu=True)
    assert broken_rules(icu, needs_it) == []
    wrong = Decision(patient_id="P-001", action="wait", target_ward="internal_medicine_a")      # the old target
    assert any("needs intensive care" in reason for reason in broken_rules(wrong, needs_it))


# ---- no vital sign at all: escalate (Lea, 2026-11-08)
NO_VITALS = {"respiratory_rate": None, "oxygen_saturation": None, "supplemental_oxygen": None, "spo2_scale_2": False,
             "systolic_bp": None, "heart_rate": None, "consciousness": None, "temperature": None}


@pytest.fixture
def no_vital_signs(monkeypatch):
    """P-001's record with every vital sign empty — no record in data/records is like this."""
    from ed_admission.tools import patient_record
    real = patient_record.read_patient_record
    monkeypatch.setattr(patient_record, "read_patient_record",
                        lambda patient_id: {**real(patient_id), "vitals": NO_VITALS})


@pytest.mark.parametrize("decision", [place(1), Decision(patient_id="P-001", action="wait", target_ward=WARD)])
def test_a_patient_with_no_vital_sign_is_neither_placed_nor_waits(no_vital_signs, decision):
    reasons = broken_rules(decision, assessment())
    assert len(reasons) == 1 and "no vital sign" in reasons[0] and "escalate" in reasons[0]


def test_a_patient_with_no_vital_sign_is_escalated(no_vital_signs):
    assert broken_rules(Decision.escalate("P-001", "no vital signs measured"), assessment()) == []


def test_one_missing_vital_sign_does_not_stop_a_placement():
    from ed_admission.tools.decide import no_vital_sign
    assert not no_vital_sign("P-002")                     # oxygen saturation missing, the other signs known
    assert broken_rules(place(1, patient_id="P-002"), assessment()) == []
