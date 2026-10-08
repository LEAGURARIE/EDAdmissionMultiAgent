"""The negotiation contract: what the Coordinator Agent asks a ward, and what the ward's Department Agent answers.

The request carries the patient's needs only — never an identifier, never the record (DECISIONS.md: the Department
Agent gets needs only). The answer is one of four, with a reason the Coordinator can weigh:
  accept      a bed now — the bed number
  condition   a bed with a condition — e.g. after cleaning, after the morning round, for a stable patient only;
              `condition` says it in words, `available_in_minutes` when the ward can tell
  decline     no bed — `reason` says why (no bed and nothing expected, or an internal limit such as staffing)

Owner: Lea (integrator)
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class WardRequest(BaseModel):
    """The Coordinator's question to one ward."""
    model_config = ConfigDict(extra="forbid")

    bed_type: str = Field(pattern="^(regular|observation|isolation|intensive_care)$")
    severe: bool                                   # observation or intensive care: the patient cannot wait
    observation: bool
    isolation: bool
    target_ward: str                               # the patient's target ward — this ward or a farther one
    proposed_bed: int | None = None                # a bed the Coordinator saw free in the report, if any


class Answer(str, Enum):
    ACCEPT = "accept"
    CONDITION = "condition"
    DECLINE = "decline"


class WardAnswer(BaseModel):
    """One ward's answer."""
    model_config = ConfigDict(extra="forbid")

    answer: Answer
    bed_number: int | None = None                  # accept / condition: the bed
    reason: str = Field(min_length=1)              # always: why — in words, as the ward would say it
    condition: str | None = None                   # condition: what must happen first
    available_in_minutes: int | None = None        # condition: when, if the ward can tell
