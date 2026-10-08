"""hold_bed / release_hold — a temporary hold on a bed that expires automatically after 30 minutes
(config/hospital.yaml, operations.hold_minutes). Two cases can never hold the same bed: the engine refuses.

Owner: integrator
"""


def hold_bed(engine, ward_id: str, bed_number: int, patient_id: str) -> None:
    engine.hold_bed(ward_id, bed_number, patient_id)


def release_hold(engine, ward_id: str, bed_number: int) -> None:
    engine.release_hold(ward_id, bed_number)
