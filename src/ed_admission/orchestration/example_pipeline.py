"""The Coordinator's flow in code — no language model. It is NOT a baseline and is not reported: it shows, end to end,
how a placement case uses the Coordinator's tools, and it checks in the tests that everything connects before the
agents are built. It is also the number the real Coordinator has to beat.

    assess_patient          the Patient Agent              (here: its code example — structured fields only)
    all_department_status   every ward's report
    rank_placements         the options, in order — code
    ask_ward                each Department Agent answers  (here: truthful_ward — the ward's true state)
    decide                  the hard rules — code; a refused decision moves on to the next option
    escalate_to_human       no ward accepted, or the limits were reached

What it cannot do, and the Coordinator Agent is there for: it takes the first ward that says yes and walks on at
anything else — it does not weigh a condition, a ward's reason or the Patient Agent's judgment note.

Owner: Lea Gur-Arie
"""
from ed_admission.contracts.negotiation import Answer, WardRequest
from ed_admission.environment.decision import Decision
from ed_admission.observability.tracing import Trace, collect
from ed_admission.orchestration import example_agents
from ed_admission.orchestration.placement_case import PlacementCase
from ed_admission.tools.decide import RuleViolation
from ed_admission.tools.negotiation import LimitReached, Negotiation, truthful_ward
from ed_admission.tools.rank_placements import PatientNeeds, resolve_target_ward
from ed_admission.tools.toolbox import Toolbox


def example_pipeline(engine, event, waiting) -> list:
    decisions, taken = [], set()
    for patient in sorted(waiting, key=lambda p: p.decided_at):
        pid = patient.patient_id
        trace = Trace(case_id=pid, decider="example_pipeline")
        responders = {ward: truthful_ward(engine) for ward in engine.hospital.wards}
        case = PlacementCase(pid, trace, Negotiation(engine, responders))
        coordinator = Toolbox("coordinator", engine, case=case, patient_agent=example_agents.patient)

        assessment = coordinator.assess_patient(pid)
        reports = coordinator.all_department_status()
        target = resolve_target_ward(assessment.target_ward, reports)      # internal medicine: A or B by occupancy
        needs = PatientNeeds(target, assessment.bed_type, severe=assessment.never_waits)
        ranking = coordinator.rank_placements(needs, taken)

        common = {"patient_id": pid, "target_ward": target, "flags": assessment.flags}
        approval = assessment.intensive_care and not (assessment.binding_request and target == "intensive_care")
        decision = None
        try:
            for option in ranking.options:
                if case.must_escalate:
                    break
                if option.action == "escalate":
                    break
                if option.action == "wait":
                    candidate = Decision(action="wait", reason=option.reason, **common,
                                         wait_for={"ward_id": option.ward_id, "bed_number": option.bed_number,
                                                   "because": option.wait_because})
                else:
                    answer = coordinator.ask_ward(option.ward_id, WardRequest(
                        bed_type=option.bed_type, severe=needs.severe, observation=assessment.observation,
                        isolation=assessment.isolation, target_ward=target, proposed_bed=option.bed_number))
                    if answer.answer != Answer.ACCEPT or (option.ward_id, answer.bed_number) in taken:
                        continue
                    place = option.action == "place"
                    candidate = Decision(action=option.action, ward_id=option.ward_id, bed_number=answer.bed_number,
                                         reason=answer.reason, physician_approval_for_icu=approval and place,
                                         awaiting_transfer_to=None if place else target, **common)
                try:
                    decision = coordinator.decide(candidate)
                    break
                except RuleViolation:
                    continue                                             # refused: the next option
        except LimitReached as limit:
            decision = coordinator.escalate_to_human(pid, str(limit))
        if decision is None:
            decision = coordinator.escalate_to_human(pid, "no ward gave a bed that meets the need")

        if decision.bed_number is not None:
            taken.add((decision.ward_id, decision.bed_number))
        collect(trace)                    # handed to the measurement (observability/tracing.py)
        decisions.append(decision)
    return decisions
