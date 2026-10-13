"""Example agents in code — one per agent, no language model — with the same signatures as each agent's `run` in
agents/, the ones evaluation/agent_metrics.py measures. They read structured fields and ward reports only, so they
are the number each agent has to beat:

    patient(toolbox, patient_id)     -> PatientAssessment
    department(view, request)        -> WardAnswer          (a negotiation responder)

The toolbox is built with the agent's role (tools/toolbox.py), so an example uses only the tools its agent may use.
Not a baseline and not reported.

Owner: Lea Gur-Arie
"""
from ed_admission import config
from ed_admission.baselines.reading import _sign_holds
from ed_admission.contracts.agent_outputs import Finding, PatientAssessment
from ed_admission.contracts.negotiation import Answer, WardAnswer, WardRequest
from ed_admission.contracts.patient_record import PatientRecord
from ed_admission.environment.decision import Flag


def patient(toolbox, patient_id: str) -> PatientAssessment:
    """The structured fields only — it never reads the physician's text.
      target ward     the ward the physician requested; otherwise the ward of the chief complaint —
                      "internal_medicine" when it points to both, for the Coordinator to choose by occupancy
      intensive care  the physician's request for it, or NEWS2 7 or more — the target ward is then intensive care
      observation     the NEWS2 level, or a structured sign (config/clinical_rules/structured_signs.yaml)
      isolation       never — no structured field says it
      flags           a missing vital sign"""
    record, news2 = toolbox.read_patient_record(patient_id), toolbox.compute_news2(patient_id)
    requested = record["physician_request"]["ward"]
    if requested:
        target = requested
    else:
        wards = config.chief_complaints()[record["chief_complaint"]]
        target = "internal_medicine" if len(wards) > 1 else wards[0]

    intensive = requested == "intensive_care" or news2["known_total"] >= config.news2_levels()["intensive_care_from"]
    if intensive:
        target = "intensive_care"                 # a patient who needs intensive care is meant for it
    signs = config.structured_signs()["observation_signs"]
    observation = news2["level_of_care"] == "observation" or any(
        _sign_holds(sign, PatientRecord(**record)) for sign in signs)
    return PatientAssessment(target_ward=target, binding_request=bool(requested), severe=intensive or observation,
                             intensive_care=intensive, observation=observation, isolation=False,
                             flags=[Flag.MISSING_VITAL_SIGN] if news2["missing"] else [],
                             findings=[Finding(claim="chief complaint", quote=record["chief_complaint"])])


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
