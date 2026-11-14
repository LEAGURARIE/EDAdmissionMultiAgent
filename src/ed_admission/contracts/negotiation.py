"""The negotiation contract: what the Coordinator asks a ward, and what the ward's Department Agent answers.

The request carries the patient's needs only — never an identifier, never the record (DECISIONS.md: the Department
Agent gets needs only). The answer is one of four, with a reason the Coordinator can weigh:
  accept      a bed now — the bed number
  condition   a bed with a condition — e.g. after cleaning, after the morning round, for a stable patient only;
              `condition` says it in words, `available_in_minutes` when the ward can tell; a condition about the
              patient is a closed question (`asks`), which code passes to the Patient Agent
  decline     no bed — `reason` says why (no bed and nothing expected, or an internal limit such as staffing)
Free text is limited in length; accept and condition always name the bed, and where the ward knows it is free from
(`source`, Lea 2026-11-08): `report` — the bed is free, in cleaning or expected to be discharged in the ward's report ·
`note` — only the staff's notes say so. Code checks it against the report and the notes (tools/negotiation.py,
bed_source_failure); a bed from a note is marked for the shift manager — the system has not caught up with it yet.

Owner: Lea Gur-Arie
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ed_admission import config

LIMIT = config.text_limits()             # the longest free text of each handoff field (config/agents.yaml)


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
    """One ward's answer. Routing reads `answer` and `asks` only; the words are shown, never routed on."""
    model_config = ConfigDict(extra="forbid")

    answer: Answer
    bed_number: int | None = None                  # accept / condition: the bed
    reason: str = Field(min_length=1, max_length=LIMIT["reason"])   # always: why, as the ward would say it
    condition: str | None = Field(None, max_length=LIMIT["condition"])  # condition: what must happen first
    available_in_minutes: int | None = Field(None, ge=0, le=1440)   # condition: when, if the ward can tell
    asks: Literal["stable"] | None = None          # condition about the patient: a closed question, never free text
    source: Literal["report", "note"] | None = None  # accept / condition: where the ward knows the bed is free

    @model_validator(mode="after")
    def a_bed_when_given_and_a_question_only_on_a_condition(self) -> "WardAnswer":
        if self.answer in (Answer.ACCEPT, Answer.CONDITION) and self.bed_number is None:
            raise ValueError(f"{self.answer.value}: name the bed")
        if (self.bed_number is None) != (self.source is None):
            raise ValueError("a bed comes with its source (report or note), and a source only with a bed")
        if self.asks and self.answer != Answer.CONDITION:
            raise ValueError("a question about the patient comes with a condition")
        return self
