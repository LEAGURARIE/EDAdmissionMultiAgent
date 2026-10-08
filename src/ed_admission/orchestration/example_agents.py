"""Example agents in code — one per role, no language model — with the same signatures as each agent's `run` in
agents/, the ones evaluation/agent_metrics.py measures. They read structured fields and ward reports only, so they
are the number each agent has to beat:

    clinical_problem(toolbox, patient_id)          -> ClinicalProblemOutput
    severity(toolbox, patient_id)                  -> SeverityOutput
    supervision(toolbox, patient_id)               -> SupervisionOutput
    supervision_back(toolbox, patient_id, offers)  -> BackToSupervisionAnswer   (back to Supervision)
    department(view, request)                      -> WardAnswer          (a negotiation responder)
    safety_critic(toolbox, patient_id, decision)   -> SafetyVerdict

The toolbox is built with the agent's role (tools/toolbox.py), so an example uses only the tools its agent may use.
Not a baseline and not reported.

Owner: Lea (integrator)
"""
from ed_admission import config
from ed_admission.baselines.reading import _sign_holds
from ed_admission.contracts.agent_outputs import (BackToSupervisionAnswer, ClinicalProblemOutput, Offer,
                                                  SafetyVerdict, SeverityOutput, SupervisionOutput)
from ed_admission.contracts.negotiation import Answer, WardAnswer, WardRequest
from ed_admission.contracts.patient_record import PatientRecord
from ed_admission.environment import load_hospital
from ed_admission.environment.decision import Decision, Flag


def clinical_problem(toolbox, patient_id: str) -> ClinicalProblemOutput:
    """The physician's requested ward; otherwise the first ward of the chief complaint. Never reads the text."""
    record = toolbox.read_patient_record(patient_id)
    requested = record["physician_request"]["ward"]
    if requested:
        return ClinicalProblemOutput(target_ward=requested)
    return ClinicalProblemOutput(target_ward=config.chief_complaints()[record["chief_complaint"]][0])


def _observation(record: dict, news2: dict) -> bool:
    signs = config.structured_signs()["observation_signs"]
    return news2["level_of_care"] == "observation" or any(_sign_holds(s, PatientRecord(**record)) for s in signs)


def severity(toolbox, patient_id: str) -> SeverityOutput:
    """NEWS2 and the structured signs. Intensive care: the physician's request, or NEWS2 7 or more."""
    record, news2 = toolbox.read_patient_record(patient_id), toolbox.compute_news2(patient_id)
    intensive = (record["physician_request"]["ward"] == "intensive_care"
                 or news2["known_total"] >= config.news2_levels()["intensive_care_from"])
    flags = [Flag.MISSING_VITAL_SIGN] if news2["missing"] else []
    return SeverityOutput(severe=intensive or _observation(record, news2), intensive_care=intensive, flags=flags)


def supervision(toolbox, patient_id: str) -> SupervisionOutput:
    """Observation from NEWS2 and the structured signs; isolation never — no structured field says it."""
    record, news2 = toolbox.read_patient_record(patient_id), toolbox.compute_news2(patient_id)
    return SupervisionOutput(observation=_observation(record, news2), isolation=False)


def department(view, request: WardRequest) -> WardAnswer:
    """Answers from its report only — it never reads its ward's notes."""
    free = [b["bed"] for b in view.report["free_beds"] if b["bed_type"] == request.bed_type]
    if free:
        bed = request.proposed_bed if request.proposed_bed in free else free[0]
        return WardAnswer(answer=Answer.ACCEPT, bed_number=bed, reason="a bed is free in our report")
    cleaning = [b["bed"] for b in view.report["cleaning_beds"] if b["bed_type"] == request.bed_type]
    if cleaning and not request.severe:
        return WardAnswer(answer=Answer.CONDITION, bed_number=cleaning[0], reason="a bed is being cleaned",
                          condition="after cleaning")
    return WardAnswer(answer=Answer.DECLINE, reason=f"no free {request.bed_type} bed in our report")


def safety_critic(toolbox, patient_id: str, decision: Decision) -> SafetyVerdict:
    """Checks the hard rules against its own reading of the structured fields."""
    record, news2 = toolbox.read_patient_record(patient_id), toolbox.compute_news2(patient_id)
    intensive = news2["known_total"] >= config.news2_levels()["intensive_care_from"] \
        or record["physician_request"]["ward"] == "intensive_care"
    severe = intensive or _observation(record, news2)
    reasons = []
    if decision.ward_id is not None:
        bed_type = load_hospital().ward(decision.ward_id).bed(decision.bed_number).bed_type.value
        if intensive and bed_type != "intensive_care":
            reasons.append("an intensive care patient outside intensive care")
        if not intensive and bed_type == "intensive_care":
            reasons.append("intensive care without a need")
        if severe and bed_type == "regular":
            reasons.append("a severe patient in a regular bed")
        if not severe and bed_type == "observation":
            reasons.append("a non-severe patient in an observation bed")
    if severe and decision.action.value == "wait":
        reasons.append("a severe patient told to wait")
    if news2["missing"] and Flag.MISSING_VITAL_SIGN not in decision.flags:
        reasons.append("a missing vital sign was not flagged")
    return SafetyVerdict(approve=not reasons, reasons=reasons)


def supervision_back(toolbox, patient_id: str, offers: list[Offer]) -> BackToSupervisionAnswer:
    """Back to Supervision: an observation-only patient may get the isolation room as a last resort; any other
    unmet need is escalated. The offers are not weighed here — the agent reads them."""
    needs = supervision(toolbox, patient_id)
    if needs.observation and not needs.isolation:
        return BackToSupervisionAnswer(decision="isolation_room_last_resort",
                                       reason="no observation bed on the ladder; the isolation room as a last resort")
    return BackToSupervisionAnswer(decision="escalate", reason="no bed on the ladder meets the need")
