"""Two simple deciders, for the tests only — they exercise the engine, the runner and the metrics with behaviour that
is easy to predict. They are not baselines and are not reported; the one baseline is the Code Coordinator
(src/ed_admission/baselines/code_coordinator.py).

  first_free_bed   takes the first free regular or observation bed in any regular ward, without looking at the
                   patient; serves the waiting patients in the order the admissions were decided
  never_asks       reads the structured fields and takes the first ranked option — it never asks a ward, so it does
                   not know what the wards' private notes say
"""
from ed_admission.baselines.code_coordinator import to_decision, waited_too_long
from ed_admission.baselines.reading import read_record
from ed_admission.environment.decision import Decision
from ed_admission.environment.hospital import BedType, WardLevel
from ed_admission.tools.rank_placements import Option, rank_placements

ALLOWED_BED_TYPES = (BedType.REGULAR, BedType.OBSERVATION)


def first_free_bed(engine, event, waiting) -> list[Decision]:
    free_beds = [(ward.ward_id, bed["bed"])
                 for ward in engine.hospital.wards.values() if ward.level == WardLevel.REGULAR
                 for bed in engine.department_status(ward.ward_id)["free_beds"]
                 if BedType(bed["bed_type"]) in ALLOWED_BED_TYPES]
    decisions = []
    for patient in sorted(waiting, key=lambda p: p.decided_at):
        if free_beds:
            ward_id, bed_number = free_beds.pop(0)
            decisions.append(Decision.place(patient.patient_id, ward_id, bed_number, reason="first free bed"))
        else:
            decisions.append(Decision.wait(patient.patient_id, "no free regular or observation bed in a regular ward"))
    return decisions


def never_asks(engine, event, waiting) -> list[Decision]:
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
