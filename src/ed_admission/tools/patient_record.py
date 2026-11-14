"""read_patient_record — the synthetic Emergency Department record, frozen at the admission decision. Read only.

Returns the record as the contract defines it (contracts/patient_record.py) — never anything from the answer key.

Owner: Lea Gur-Arie
"""
import re

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


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n\s*\n")    # . ! ? or a blank line ends a sentence; a line break does not


def sentence_around(patient_id: str, quote: str) -> str | None:
    """The whole sentence of the record a quote was taken from — or the few sentences, when the quote crosses one.
    The Judge Agent reads it beside the quote (Lea, 2026-11-08): a quote can be found in the record and still be cut
    short — "TB" out of "TB cannot be ruled out" — and only the sentence shows how it was meant. Never the whole
    record. None: the quote is not in the record."""
    wanted = _normalize(quote)
    if not wanted:
        return None
    record = read_patient_record(patient_id)
    for text in (record["physician_note"], record.get("triage_note") or "", record["background_conditions"]):
        sentences = [_normalize(s) for s in _SENTENCE_END.split(str(text)) if s.strip()]
        for size in range(1, len(sentences) + 1):                 # the fewest sentences that hold the quote
            for first in range(len(sentences) - size + 1):
                joined = " ".join(sentences[first:first + size])
                if wanted in joined:
                    return joined
    return None
