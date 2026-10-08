"""read_patient_record — the synthetic Emergency Department record, frozen at the admission decision. Read only.

Returns the record as the contract defines it (contracts/patient_record.py) — never anything from the answer key.

Owner: integrator
"""
from ed_admission import config
from ed_admission.contracts.patient_record import load_record

RECORDS = config.PROJECT / "data" / "records"


def read_patient_record(patient_id: str) -> dict:
    return load_record(RECORDS / f"{patient_id}.yaml").model_dump(mode="json")
