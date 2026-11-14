"""decide — the hard rules, checked in code before a decision leaves the Coordinator.

The Coordinator decides where the patient goes; this check makes sure the decision keeps what the Patient Agent found.
A decision that breaks a rule is refused with the reasons, and the Coordinator tries again or escalates:

  the need is never lowered   observation need -> an observation bed, or the isolation room · isolation need -> the
                              isolation room only · intensive care need -> intensive care only
  nothing above the need      intensive care only for a patient who needs it · a non-severe patient never in an
                              observation bed
  a severe patient never waits
  the target ward stands      a ward the physician requested is never changed; "internal_medicine" (the record names
                              neither A nor B) may become A or B, nothing else; a patient who needs intensive care has
                              intensive care as the target ward (PatientAssessment.target)
  nothing is dropped          every flag the Patient Agent raised is in the decision · intensive care that the
                              physician did not request (NEWS2 7 or more) is marked for a physician's approval
  no vital sign, no bed       a patient whose record has no vital sign at all is not placed and does not wait —
                              escalated to the shift manager (Lea, 2026-11-08): without one NEWS2 proves no level of
                              care, and a bed chosen from the free text alone may be below the need

An escalation always passes: handing the patient to the shift manager is never unsafe.
These are the same rules the metrics count as violations (evaluation/metrics.py) — there against the expected
answers, here against the Patient Agent's assessment.

Owner: Lea Gur-Arie
"""
from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.contracts.patient_record import Vitals
from ed_admission.environment import load_hospital
from ed_admission.environment.decision import Action, Decision
from ed_admission.tools import patient_record
from ed_admission.tools.news2 import compute_news2
from ed_admission.tools.rank_placements import INTERNAL_MEDICINE


def no_vital_sign(patient_id: str) -> bool:
    """The record has no vital sign at all: NEWS2 has not one known point."""
    vitals = Vitals(**patient_record.read_patient_record(patient_id)["vitals"])
    return not compute_news2(vitals).points


class RuleViolation(Exception):
    """The decision breaks a hard rule. `reasons` says which."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def broken_rules(decision: Decision, assessment: PatientAssessment) -> list[str]:
    """Every hard rule this decision breaks, in words. Empty: the decision may go to the shift manager."""
    if decision.action == Action.ESCALATE:
        return []
    if no_vital_sign(decision.patient_id):
        return ["no vital sign in the record: NEWS2 proves no level of care — escalate to the shift manager "
                "(missing_vital_sign)"]
    reasons = []
    needs_observation = assessment.observation or assessment.severe

    if decision.action in (Action.PLACE, Action.TEMPORARY_PLACE):
        bed_type = load_hospital().ward(decision.ward_id).bed(decision.bed_number).bed_type.value
        if assessment.intensive_care and bed_type != "intensive_care":
            reasons.append("the patient needs intensive care; the bed is not an intensive care bed")
        if not assessment.intensive_care and bed_type == "intensive_care":
            reasons.append("an intensive care bed for a patient who does not need intensive care")
        if assessment.isolation and bed_type != "isolation" and not assessment.intensive_care:
            reasons.append("the patient needs isolation; the bed is not an isolation room")
        if needs_observation and not assessment.intensive_care and bed_type == "regular":
            reasons.append("the patient needs observation; the bed is a regular bed")
        if not needs_observation and not assessment.intensive_care and bed_type == "observation":
            reasons.append("an observation bed for a patient who does not need observation")
    elif decision.action == Action.WAIT and assessment.never_waits:
        reasons.append("a severe patient never waits")

    target = assessment.target                       # intensive care whenever the patient needs it
    allowed = set(INTERNAL_MEDICINE) if target == "internal_medicine" else {target}
    if decision.target_ward not in allowed:
        kind = ("the patient needs intensive care" if target != assessment.target_ward
                else "requested by the physician" if assessment.binding_request else "found by the Patient Agent")
        reasons.append(f"the target ward is {target} ({kind}); the decision says {decision.target_ward}")

    requested_by_physician = assessment.binding_request and assessment.target_ward == "intensive_care"
    if (decision.action in (Action.PLACE, Action.TEMPORARY_PLACE) and assessment.intensive_care
            and not requested_by_physician and not decision.physician_approval_for_icu):
        reasons.append("intensive care without a physician's request needs a physician's approval "
                       "(physician_approval_for_icu)")

    missing = [flag.value for flag in assessment.flags if flag not in decision.flags]
    if missing:
        reasons.append(f"flags raised by the Patient Agent are missing from the decision: {', '.join(missing)}")
    return reasons


def decide(decision: Decision, assessment: PatientAssessment) -> Decision:
    """Return the decision if it keeps every hard rule; otherwise raise RuleViolation with the reasons."""
    reasons = broken_rules(decision, assessment)
    if reasons:
        raise RuleViolation(reasons)
    return decision
