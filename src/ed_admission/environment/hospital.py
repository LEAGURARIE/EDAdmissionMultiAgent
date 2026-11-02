"""Hospital state: wards, beds and their status, and the Emergency Department queue.

These are plain data models (Pydantic). They hold the state; they do not change it.
Only the simulation engine changes state — see simulation_engine.py.

Owner: Lea Gur-Arie
"""
from enum import Enum

from pydantic import BaseModel, Field


class BedType(str, Enum):
    REGULAR = "regular"
    OBSERVATION = "observation"            # also serves as intermediate care
    ISOLATION = "isolation"                # one room per ward, with observation capability (who may use it: rank_placements)
    INTENSIVE_CARE = "intensive_care"


class BedStatus(str, Enum):
    FREE = "free"
    HELD = "held"                          # temporarily reserved for a patient, expires automatically
    OCCUPIED = "occupied"
    CLEANING = "cleaning"


class WardLevel(str, Enum):
    REGULAR = "regular"                    # regular ward with regular and observation beds
    INTENSIVE_CARE = "intensive_care"


class Bed(BaseModel):
    number: int
    bed_type: BedType
    status: BedStatus = BedStatus.FREE
    patient_id: str | None = None
    held_for: str | None = None            # patient the bed is held for
    hold_expires_at: int | None = None     # simulated minute
    expected_discharge_at: int | None = None  # simulated minute; the ward only knows the day, not the hour
    cleaning_until: int | None = None      # simulated minute the cleaning ends — the ward knows it
    in_report: bool = True                 # False: freed, but the ward has not updated the system — the report still
                                           # shows it occupied; only the ward's note tells (scripted days)


class Ward(BaseModel):
    ward_id: str
    name: str
    level: WardLevel
    beds: list[Bed]
    notes: list[str] = []                  # what only this ward knows (staffing, an uncertain discharge...) — private:
                                           # never in the ward report; only this ward's Department Agent will read it
    note_effects: list[dict] = []          # what the notes mean for the beds (ward_truth.py) — never shown to a decider

    def bed(self, number: int) -> Bed:
        for bed in self.beds:
            if bed.number == number:
                return bed
        raise KeyError(f"{self.ward_id} has no bed {number}")

    def count(self, bed_type: BedType, status: BedStatus) -> int:
        return sum(1 for bed in self.beds if bed.bed_type == bed_type and bed.status == status)

    def total(self, bed_type: BedType) -> int:
        return sum(1 for bed in self.beds if bed.bed_type == bed_type)


class WaitingPatient(BaseModel):
    patient_id: str
    decided_at: int                        # simulated minute of the admission decision
    awaiting_approval: bool = False        # a bed is held for the patient until the shift manager approves


class Hospital(BaseModel):
    wards: dict[str, Ward]
    emergency_queue: list[WaitingPatient] = Field(default_factory=list)

    def ward(self, ward_id: str) -> Ward:
        if ward_id not in self.wards:
            raise KeyError(f"unknown ward: {ward_id}")
        return self.wards[ward_id]
