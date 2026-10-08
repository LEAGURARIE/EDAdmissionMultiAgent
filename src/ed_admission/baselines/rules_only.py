"""Baseline 2: "rules only" — the system without agents (the course requires it).

Reads each record's structured fields (reading.py), sees every ward's report, and takes the first option of
rank_placements. It never phones a ward, so it does not know what only the wards know (their private notes).
Waiting patients are served severe first, then by the earlier admission decision. A non-severe patient who has
waited an hour with no bed on the ladder is escalated.

Owner: integrator
"""
from ed_admission import config
from ed_admission.baselines.reading import read_record
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.tools.rank_placements import Option, rank_placements


def to_decision(patient_id: str, option: Option, target: str, reading, handling_minutes: int = 0) -> Decision:
    common = {"flags": reading.flags, "reason": option.reason, "handling_minutes": handling_minutes,
              "target_ward": target}
    if option.action == "place":
        return Decision(patient_id=patient_id, action="place", ward_id=option.ward_id, bed_number=option.bed_number,
                        physician_approval_for_icu=reading.physician_approval_for_icu, **common)
    if option.action == "temporary_place":
        return Decision(patient_id=patient_id, action="temporary_place", ward_id=option.ward_id,
                        bed_number=option.bed_number, awaiting_transfer_to=target, **common)
    if option.action == "wait":
        return Decision(patient_id=patient_id, action="wait", **common,
                        wait_for={"ward_id": option.ward_id, "bed_number": option.bed_number,
                                  "because": option.wait_because})
    return Decision(patient_id=patient_id, action="escalate", **common)


def waited_too_long(engine, patient: WaitingPatient) -> bool:
    return engine.clock - patient.decided_at >= config.placement_rules()["waiting"]["non_severe_max_minutes"]


def rules_only(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    reports = {ward: engine.department_status(ward) for ward in engine.hospital.wards}
    readings = {p.patient_id: read_record(p.patient_id, reports) for p in waiting}
    order = sorted(waiting, key=lambda p: (not readings[p.patient_id].needs.severe, p.decided_at))
    taken, decisions = set(), []
    for patient in order:
        reading = readings[patient.patient_id]
        option = rank_placements(reports, reading.needs, taken).best
        if option.action == "wait" and waited_too_long(engine, patient):
            option = Option("escalate", reason="waited an hour and no bed on the ladder")
        if option.bed_number is not None and option.action != "wait":
            taken.add((option.ward_id, option.bed_number))
        decisions.append(to_decision(patient.patient_id, option, reading.needs.target_ward, reading))
    return decisions
