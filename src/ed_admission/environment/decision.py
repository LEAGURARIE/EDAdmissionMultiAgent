"""The contract between the simulation engine and whoever decides placements.

At every trigger (admission decision, bed freed) the engine hands the decider the trigger and the list of
waiting patients — unranked. The decider only reads the hospital and returns one Decision per patient, in
the order it wants them applied; the engine applies them. The same contract serves the baselines and,
later, the agents.

A decision is one of four actions:
  place            a bed in the target ward (ladder level 1)
  temporary_place  a bed further down the target ward's ladder, "awaiting transfer" to the target ward
  wait             no suitable bed now — wait, usually for a known bed (in cleaning, expected to free today, or one
                   the ward said will free soon)
  escalate         no safe option — hand the patient to the shift manager

Every placement goes to the shift manager for approval, so approval is not written in the decision.
The ladder level is never written by the decider — it is computed from the ladder, so it cannot be wrong.
Every decision names the patient's target ward as the decider understood it (`target_ward`), whatever the action —
so the admissions log shows where each patient was meant to go. Only a decider that never reads the patient
("first free bed") leaves it empty.

Owner: integrator
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ed_admission import config


class Action(str, Enum):
    PLACE = "place"
    TEMPORARY_PLACE = "temporary_place"
    WAIT = "wait"
    ESCALATE = "escalate"


class Flag(str, Enum):
    """Things the decider points out to the human, from a closed list."""
    PROMPT_INJECTION = "prompt_injection"            # text in the record tries to change the system's behaviour
    MISSING_VITAL_SIGN = "missing_vital_sign"        # a vital sign is empty and not found in the text
    FIELD_TEXT_CONFLICT = "field_text_conflict"      # a structured field and the text disagree
    SPECIALTY_CONFLICT = "specialty_conflict"        # the specialty the physician wrote does not fit the case
    SEVERITY_JUDGMENT_NOTE = "severity_judgment_note"  # the level is kept, but the record argues for less
                                                       # (a limit on treatment, confusion that is not new)


class WaitFor(BaseModel):
    """The bed the patient waits for."""
    model_config = ConfigDict(extra="forbid")
    ward_id: str
    bed_number: int | None = None                    # unknown when waiting for a discharge
    because: Literal["cleaning", "discharge_today", "ward_note"]
    # cleaning          a bed in cleaning, shown in the ward report
    # discharge_today   a discharge expected today, shown in the ward report
    # ward_note         a bed that frees soon by what the ward said in the negotiation — not in the report


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: str
    action: Action
    target_ward: str | None = None                   # the patient's target ward (ladder level 1), every action
    ward_id: str | None = None                       # place / temporary_place
    bed_number: int | None = None                    # place / temporary_place
    awaiting_transfer_to: str | None = None          # temporary_place: the target ward
    wait_for: WaitFor | None = None                  # wait: the bed waited for, when there is one in view
    physician_approval_for_icu: bool = False         # NEWS2 7+ without a physician's request
    flags: list[Flag] = []
    reason: str = ""
    handling_minutes: int = 0                        # simulated time a code decider stands for (the simulated
                                                     # coordinator's phone calls); the engine adds the real, measured
                                                     # time of the decision on top — see SimulationEngine._run_policy

    @model_validator(mode="after")
    def fields_match_the_action(self) -> "Decision":
        placing = self.action in (Action.PLACE, Action.TEMPORARY_PLACE)
        if placing and (self.ward_id is None or self.bed_number is None):
            raise ValueError(f"{self.action.value} needs ward_id and bed_number")
        if not placing and (self.ward_id is not None or self.bed_number is not None):
            raise ValueError(f"{self.action.value} must not name a bed")
        if (self.action == Action.TEMPORARY_PLACE) != (self.awaiting_transfer_to is not None):
            raise ValueError("awaiting_transfer_to goes with temporary_place, and only with it")
        if self.wait_for is not None and self.action != Action.WAIT:
            raise ValueError("wait_for goes only with wait")
        if self.action == Action.TEMPORARY_PLACE and self.ladder_level is None:
            raise ValueError(f"{self.ward_id} is not on the ladder of {self.awaiting_transfer_to}")
        if self.action == Action.TEMPORARY_PLACE and self.ladder_level == 1:
            raise ValueError("a bed in the target ward itself is a place, not a temporary_place")
        if self.target_ward is not None:
            if self.target_ward not in config.ladders():
                raise ValueError(f"{self.target_ward} is not a ward")
            if self.action == Action.PLACE and self.ward_id != self.target_ward:
                raise ValueError("a place is a bed in the target ward; elsewhere it is a temporary_place")
            if self.action == Action.TEMPORARY_PLACE and self.awaiting_transfer_to != self.target_ward:
                raise ValueError("a temporary placement awaits transfer to the target ward")
        return self

    @property
    def ladder_level(self) -> int | None:
        """1 for a place; for a temporary placement, the level of the ward on the target ward's ladder."""
        if self.action == Action.PLACE:
            return 1
        if self.action == Action.TEMPORARY_PLACE:
            return config.ladder_level(self.awaiting_transfer_to, self.ward_id)
        return None

    @property
    def needs_typed_reason(self) -> bool:
        """Level 3 or farther: the shift manager must write a reason, not only approve."""
        from_level = config.placement_rules()["approval"]["typed_reason_from_ladder_level"]
        return self.ladder_level is not None and self.ladder_level >= from_level

    # Short constructors for the common cases
    @classmethod
    def place(cls, patient_id: str, ward_id: str, bed_number: int, reason: str = "") -> "Decision":
        return cls(patient_id=patient_id, action=Action.PLACE, ward_id=ward_id, bed_number=bed_number, reason=reason)

    @classmethod
    def temporary_place(cls, patient_id: str, ward_id: str, bed_number: int, awaiting_transfer_to: str,
                        reason: str = "") -> "Decision":
        return cls(patient_id=patient_id, action=Action.TEMPORARY_PLACE, ward_id=ward_id, bed_number=bed_number,
                   awaiting_transfer_to=awaiting_transfer_to, reason=reason)

    @classmethod
    def wait(cls, patient_id: str, reason: str = "", *, ward_id: str | None = None, because: str | None = None,
             bed_number: int | None = None) -> "Decision":
        wait_for = WaitFor(ward_id=ward_id, bed_number=bed_number, because=because) if ward_id else None
        return cls(patient_id=patient_id, action=Action.WAIT, wait_for=wait_for, reason=reason)

    @classmethod
    def escalate(cls, patient_id: str, reason: str = "", flags: list[Flag] | None = None) -> "Decision":
        return cls(patient_id=patient_id, action=Action.ESCALATE, reason=reason, flags=flags or [])


@dataclass
class Placement:
    """One completed placement in a scripted day — the raw material for the time metrics."""
    patient_id: str
    decided_at: int                  # simulated minute of the admission decision
    placed_at: int                   # simulated minute the bed was assigned
    ward_id: str
    bed_number: int
    bed_type: str
    awaiting_transfer_to: str | None = None
    reason: str = ""
    deciding_minutes: int = 0        # how long the decider took: measured time, rounded up, + its handling_minutes
    target_ward: str | None = None   # as the decider understood it

    @property
    def wait_minutes(self) -> int:
        return self.placed_at - self.decided_at


@dataclass
class Escalation:
    """A patient handed to the shift manager in a scripted day. The patient leaves the simulation's queue:
    from here on the shift manager handles the patient, and the simulation no longer follows them."""
    patient_id: str
    decided_at: int                  # simulated minute of the admission decision
    escalated_at: int                # simulated minute of the escalation — after the decider's time
    reason: str = ""
    flags: list[str] = field(default_factory=list)
    target_ward: str | None = None   # as the decider understood it

    @property
    def wait_minutes(self) -> int:
        return self.escalated_at - self.decided_at


# A decider gets the engine (to read the hospital), the trigger, and the waiting patients (unranked), and returns
# its decisions in the order to apply them. It must not change state — only the engine does.
Policy = Callable[["SimulationEngine", "Event", list["WaitingPatient"]], list[Decision]]  # noqa: F821
