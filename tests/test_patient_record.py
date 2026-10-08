"""Tests for the patient-record contract (src/ed_admission/contracts/patient_record.py)."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ed_admission.contracts.patient_record import PatientRecord, chief_complaints, load_record

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = Path(__file__).parent / "fixtures" / "example_record.yaml"


def example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf8"))


def test_example_record_passes():
    record = load_record(EXAMPLE)
    assert record.chief_complaint == "shortness_of_breath" and record.vitals.oxygen_saturation == 93
    assert "לשלול שחפת" in record.physician_note


def test_every_record_in_data_passes():
    for path in (PROJECT / "data" / "records").glob("*.yaml"):
        load_record(path)


def test_vitals_may_be_empty():
    data = example()
    data["vitals"]["oxygen_saturation"] = None             # traps 10 and 12: the field is empty
    assert PatientRecord.model_validate(data).vitals.oxygen_saturation is None


@pytest.mark.parametrize("field, value", [("oxygen_saturation", 120), ("heart_rate", 5), ("temperature", 45)])
def test_implausible_vital_is_rejected(field, value):
    data = example()
    data["vitals"][field] = value
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)


def test_chief_complaint_must_be_from_the_closed_list():
    data = example()
    data["chief_complaint"] = "headache"
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)
    assert "shortness_of_breath" in chief_complaints() and len(chief_complaints()) == 11


@pytest.mark.parametrize("field", ["expected", "traps", "severe", "arrival_time", "decision_time"])
def test_unknown_fields_are_rejected(field):
    """No answers and no clock times in a record."""
    data = example()
    data[field] = "anything"
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)


def test_unknown_lab_is_rejected():
    data = example()
    data["labs"]["ferritin"] = 300                         # not in the closed list of labs
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)


def test_requested_ward_must_exist():
    data = example()
    data["physician_request"]["ward"] = "neurology"
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)
    data["physician_request"]["ward"] = "cardiology"
    assert PatientRecord.model_validate(data).physician_request.ward == "cardiology"


def test_adults_only():
    data = example()
    data["patient"]["age"] = 12
    with pytest.raises(ValidationError):
        PatientRecord.model_validate(data)
