"""The patient record: the ED record frozen at the admission decision — what the system may see.

Every file in data/records/ must pass this contract. It holds facts only: no expected answer, no
severity, no needs, no template, and no clock times (the time comes from the template or the scripted day;
the free text uses relative times such as "two hours ago").
Unknown fields are rejected, so an answer-like field can never slip into a record.
Vital signs may be empty on purpose (traps 10 and 12) — but a value that is present must be plausible.

Owner: Lea Gur-Arie
"""
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ed_admission import config

def chief_complaints() -> frozenset[str]:
    """The closed list of chief complaints (config/clinical_rules/chief_complaints.yaml)."""
    return frozenset(config.chief_complaints())


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class Consciousness(str, Enum):        # the NEWS2 scale: alert, new confusion, voice, pain, unresponsive
    ALERT = "alert"
    CONFUSION = "confusion"
    VOICE = "voice"
    PAIN = "pain"
    UNRESPONSIVE = "unresponsive"


class FunctionalStatus(str, Enum):
    INDEPENDENT = "independent"
    PARTLY_DEPENDENT = "partly_dependent"
    DEPENDENT = "dependent"


class Patient(Strict):
    age: int = Field(ge=18, le=110)    # adults only
    sex: Sex


class Vitals(Strict):
    """The last set before the admission decision. Any value may be empty."""
    respiratory_rate: int | None = Field(None, ge=4, le=60)          # breaths per minute
    oxygen_saturation: int | None = Field(None, ge=50, le=100)       # percent
    supplemental_oxygen: bool | None = None
    spo2_scale_2: bool = False          # NEWS2 scale 2 — target saturation 88-92%, e.g. hypercapnic COPD
    systolic_bp: int | None = Field(None, ge=40, le=260)             # mmHg
    heart_rate: int | None = Field(None, ge=20, le=250)              # beats per minute
    consciousness: Consciousness | None = None
    temperature: float | None = Field(None, ge=32, le=43)            # degrees Celsius


class Labs(Strict):
    """Closed list of laboratory tests, each in a fixed unit. Any value may be missing."""
    wbc: float | None = Field(None, ge=0, le=200)                    # 10^3 per microliter
    hemoglobin: float | None = Field(None, ge=2, le=25)              # g/dL
    platelets: float | None = Field(None, ge=0, le=2000)             # 10^3 per microliter
    sodium: float | None = Field(None, ge=100, le=180)               # mmol/L
    potassium: float | None = Field(None, ge=1, le=10)               # mmol/L
    creatinine: float | None = Field(None, ge=0.1, le=20)            # mg/dL
    glucose: float | None = Field(None, ge=10, le=2000)              # mg/dL
    crp: float | None = Field(None, ge=0, le=600)                    # mg/L
    troponin: float | None = Field(None, ge=0, le=100000)            # ng/L, high-sensitivity
    lactate: float | None = Field(None, ge=0, le=30)                 # mmol/L
    ph: float | None = Field(None, ge=6.5, le=7.8)
    pco2: float | None = Field(None, ge=10, le=150)                  # mmHg
    inr: float | None = Field(None, ge=0.5, le=15)


class PhysicianRequest(Strict):
    ward: str | None = None             # empty if the physician did not request a ward

    @field_validator("ward")
    @classmethod
    def an_existing_ward(cls, value: str | None) -> str | None:
        if value is not None and value not in config.hospital()["wards"]:
            raise ValueError(f"{value!r} is not a ward in config/hospital.yaml")
        return value


class PatientRecord(Strict):
    case_id: str = Field(pattern=r"^P-\d{3}$")
    patient: Patient
    triage_level: int = Field(ge=1, le=5)                            # 1 = most urgent
    chief_complaint: str                                             # closed list, chosen by the triage nurse
    vitals: Vitals
    labs: Labs = Labs()
    background_conditions: str = ""     # free text, as written in the file — abbreviations included
    medications: list[str] = []
    allergies: list[str] = []
    functional_status: FunctionalStatus | None = None
    physician_request: PhysicianRequest = PhysicianRequest()
    triage_note: str | None = None      # the triage nurse's short note
    physician_note: str = Field(min_length=1)   # the ED physician's summary — Hebrew with English medical terms

    @field_validator("chief_complaint")
    @classmethod
    def from_the_closed_list(cls, value: str) -> str:
        if value not in chief_complaints():
            raise ValueError(f"{value!r} is not in config/clinical_rules/chief_complaints.yaml")
        return value


def load_record(path: Path) -> PatientRecord:
    """Read one record file and check it against the contract."""
    return PatientRecord.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf8")))
