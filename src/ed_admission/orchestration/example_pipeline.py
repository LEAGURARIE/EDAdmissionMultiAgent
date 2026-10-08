"""An example of the wiring the agents will use — code only, no language model. It is NOT a baseline and is not
reported: it shows, end to end, how a placement case uses the toolbox, the negotiation and the trace, and it checks
in the tests that the plumbing works before the team plugs in the agents.

Each step is where an agent goes:
  patient_assessed        Clinical Problem, Severity, Supervision      (here: reading.py, structured fields only)
  availability_collected  Coordinator — all_department_status
  ranked                  Coordinator — rank_placements
  negotiating             Coordinator asks the wards; each Department Agent answers   (here: truthful_ward)
  critic_reviewed         Safety Critic                                (here: a hard-rule check)
  decided                 Coordinator → the shift manager

Owner: Lea (integrator)
"""
from ed_admission.baselines.reading import read_record
from ed_admission.baselines.rules_only import to_decision
from ed_admission.contracts.negotiation import Answer, WardRequest
from ed_admission.observability.tracing import Claim, Trace, collect
from ed_admission.orchestration.placement_case import PlacementCase
from ed_admission.tools.negotiation import LimitReached, Negotiation, truthful_ward
from ed_admission.tools.rank_placements import Option
from ed_admission.tools.toolbox import Toolbox


def example_pipeline(engine, event, waiting) -> list:
    decisions, taken = [], set()
    for patient in sorted(waiting, key=lambda p: p.decided_at):
        trace = Trace(case_id=patient.patient_id, decider="example_pipeline")
        responders = {ward: truthful_ward(engine) for ward in engine.hospital.wards}
        case = PlacementCase(patient.patient_id, trace, Negotiation(engine, responders))
        coordinator = Toolbox("coordinator", engine, negotiation=case.negotiation, trace=trace)

        record = Toolbox("clinical_problem", engine, trace=trace).read_patient_record(patient.patient_id)
        reading = read_record(patient.patient_id, coordinator.all_department_status())
        trace.agent_call("clinical_problem", patient.patient_id, reading.needs.target_ward, model="code",
                         prompt_version="-", claims=[Claim("chief complaint", record["chief_complaint"])])
        case.move("patient_assessed", reading.needs.__dict__)
        case.move("availability_collected", "reports")
        ranking = coordinator.rank_placements(reading.needs, taken)
        case.move("ranked", [o.__dict__ for o in ranking.options])

        chosen = None
        try:
            for option in ranking.options:
                if option.action not in ("place", "temporary_place"):
                    chosen = option
                    break
                if case.step != "negotiating":
                    case.move("negotiating")
                answer = coordinator.ask_ward(option.ward_id, WardRequest(
                    bed_type=option.bed_type, severe=reading.needs.severe,
                    observation=reading.needs.bed_type == "observation", isolation=False,
                    target_ward=reading.needs.target_ward, proposed_bed=option.bed_number))
                if answer.answer == Answer.ACCEPT and (option.ward_id, answer.bed_number) not in taken:
                    chosen = Option(option.action, option.ward_id, answer.bed_number, option.bed_type,
                                    option.ladder_level, reason=answer.reason)
                    break
        except LimitReached as limit:
            chosen = Option("escalate", reason=str(limit))
        chosen = chosen or Option("escalate", reason="no ward accepted")

        decision = to_decision(patient.patient_id, chosen, reading.needs.target_ward, reading)
        case.move("critic_reviewed", "hard rules checked")
        case.move("decided", decision)
        if decision.bed_number is not None:
            taken.add((decision.ward_id, decision.bed_number))
        collect(trace)                    # handed to the measurement (observability/tracing.py)
        decisions.append(decision)
    return decisions
