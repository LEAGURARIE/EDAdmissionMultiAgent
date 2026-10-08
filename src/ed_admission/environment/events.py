"""Events that move the simulated hospital.

Simulated time is counted in minutes from midnight of the first day (480 = 08:00; 1920 = 08:00 the next day).

Owner: integrator
"""
from enum import Enum

from pydantic import BaseModel

MINUTES_PER_DAY = 24 * 60


class EventType(str, Enum):
    DISCHARGE = "discharge"                    # occupied -> cleaning
    CLEANING_DONE = "cleaning_done"            # cleaning -> free (scheduled by the engine)
    ADMISSION_DECISION = "admission_decision"  # patient joins the Emergency Department queue — main trigger
    HOLD_EXPIRED = "hold_expired"              # held -> free if still held (scheduled by the engine)
    APPROVAL_DONE = "approval_done"            # the shift manager approved: held -> occupied (scheduled by the engine)
    WARD_NOTE = "ward_note"                    # a ward's private note starts (scripted days)
    WARD_NOTE_END = "ward_note_end"            # ... and ends


class Event(BaseModel):
    time: int
    event_type: EventType
    ward_id: str | None = None
    bed_number: int | None = None
    patient_id: str | None = None
    text: str | None = None                    # ward_note / ward_note_end: the note
    effects: list[dict] = []                   # ward_note: what the note means for the beds (ward_truth.py)
    in_report: bool = True                     # discharge: False = the ward has not updated the system yet


def to_minutes(clock: str) -> int:
    """'16:25' -> 985 · '01:45 +1d' -> 1545 (the next day, for days that pass midnight)."""
    clock, _, plus = clock.partition(" +")
    hours, minutes = clock.split(":")
    days = int(plus.removesuffix("d")) if plus else 0
    return days * MINUTES_PER_DAY + int(hours) * 60 + int(minutes)


def to_clock(minute: int) -> str:
    """985 -> '16:25'. Past midnight adds '+1d'."""
    day, rest = divmod(minute, MINUTES_PER_DAY)
    text = f"{rest // 60:02d}:{rest % 60:02d}"
    return text if day == 0 else f"{text} +{day}d"
