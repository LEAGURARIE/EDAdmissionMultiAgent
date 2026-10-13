"""read_patient_record — the synthetic Emergency Department record, frozen at the admission decision. Read only.

Returns the record as the contract defines it (contracts/patient_record.py) — never anything from the answer key.

Owner: Lea Gur-Arie
"""
from ed_admission import config
from ed_admission.contracts.patient_record import load_record

RECORDS = config.PROJECT / "data" / "records"


def read_patient_record(patient_id: str) -> dict:
    return load_record(RECORDS / f"{patient_id}.yaml").model_dump(mode="json")


def _normalize(text: str) -> str:
    return " ".join(text.split())


def record_text(patient_id: str) -> str:
    """Everything written in the record, as one text — where a quote must be found."""
    record = read_patient_record(patient_id)
    return _normalize(" ".join(str(v) for v in (record["physician_note"], record.get("triage_note") or "",
                                                record["background_conditions"], record["chief_complaint"],
                                                record["medications"], record["allergies"])))


def quote_in_record(patient_id: str, quote: str) -> bool:
    """Do these exact words appear in the record? (spaces and line breaks aside)"""
    return bool(quote.strip()) and _normalize(quote) in record_text(patient_id)
