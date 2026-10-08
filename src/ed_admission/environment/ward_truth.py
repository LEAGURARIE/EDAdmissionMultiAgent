"""What each ward's private notes mean for its beds — the true state of a ward, beyond its report.

A ward's report shows what the system knows; its notes say what only the ward knows. Each note may carry effects
(written next to it in the template or scenario), and applying them gives the true state:
  unavailable            the free beds of this type cannot take a patient now (staffing, broken, promised)
  hidden_free            one bed of this type is free, but the report still shows it occupied
  discharge_not_today    the discharges the report expects today will not happen
  discharge_within_hour  one bed of this type frees within about an hour (not in the report)
  cleaning_long          the beds in cleaning will not be ready within the hour
  only_own_patients      the ward takes only patients whose target ward it is

Used by the expected answers, and by the phone call of the "simulated coordinator" baseline — a ward that is asked
answers from its true state. A decider never reads the effects; the agents get the notes' text only.

Owner: integrator
"""
import copy

from ed_admission.environment.hospital import BedStatus, BedType


def apply_effect(engine, ward_id: str, effect: dict, patient_target: str | None = None) -> None:
    """Apply one note effect to one ward of `engine`, in place."""
    bed_type = BedType(effect["bed_type"]) if effect.get("bed_type") else None
    beds = engine.hospital.ward(ward_id).beds
    typed = [b for b in beds if b.bed_type == bed_type]
    occupied = [b for b in typed if b.status == BedStatus.OCCUPIED and b.expected_discharge_at is None]
    kind = effect["effect"]
    if kind == "unavailable":
        for bed in typed:
            if bed.status == BedStatus.FREE:
                bed.status = BedStatus.OCCUPIED
    elif kind == "hidden_free":
        if occupied:
            occupied[0].status, occupied[0].patient_id = BedStatus.FREE, None
    elif kind == "discharge_not_today":
        for bed in beds:
            bed.expected_discharge_at = None
    elif kind == "discharge_within_hour":
        if occupied:
            occupied[0].expected_discharge_at = engine.clock
            engine.note_discharges = getattr(engine, "note_discharges", set()) | {(ward_id, occupied[0].number)}
    elif kind == "cleaning_long":
        for bed in beds:
            if bed.status == BedStatus.CLEANING:
                bed.status = BedStatus.OCCUPIED
    elif kind == "only_own_patients":
        if patient_target is not None and patient_target != ward_id:
            for bed in beds:
                if bed.status == BedStatus.FREE:
                    bed.status = BedStatus.OCCUPIED
    else:
        raise ValueError(f"unknown ward-note effect {kind!r}")


def apply_all(engine, patient_target: str | None = None) -> None:
    """Apply every ward's note effects, in place. A bed freed but not updated is already free in the engine."""
    for ward_id, ward in engine.hospital.wards.items():
        for effect in ward.note_effects:
            apply_effect(engine, ward_id, effect, patient_target)


def true_state(engine, patient_target: str | None = None):
    """A copy of the hospital as it really is, for a patient meant for `patient_target`. The engine is unchanged."""
    real = copy.deepcopy(engine)
    apply_all(real, patient_target)
    return real
