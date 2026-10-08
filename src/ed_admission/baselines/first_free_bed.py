"""Baseline 1: "first free bed" — what a busy clerk does without looking at the patient.

Takes the first free regular or observation bed in any regular ward, in the order of
config/hospital.yaml. Never uses intensive care or isolation rooms. Ignores the
patient's problem, severity, observation and isolation needs — on purpose.
Serves waiting patients in the order the admission decisions were made.

It reads the free beds from the ward reports, like every decider — so it does not see a bed the ward freed but
has not updated in the system yet.

Owner: integrator
"""
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import BedType, WaitingPatient, WardLevel

ALLOWED_BED_TYPES = (BedType.REGULAR, BedType.OBSERVATION)


def first_free_bed(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    """One decision per waiting patient: the next free bed in the list, or wait if none is left."""
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
