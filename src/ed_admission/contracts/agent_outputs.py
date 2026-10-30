"""What the Patient Agent returns — the contract between it and the Coordinator, and what is measured on its own.

The Patient Agent may return more (extra fields are allowed). The fields below are the ones the Coordinator relies
on, the decide tool checks the decision against (tools/decide.py), and evaluation/agent_metrics.py compares with the
expected answers. The Department Agent needs no contract here: it answers a WardRequest with a WardAnswer
(contracts/negotiation.py). The Coordinator returns a Decision (environment/decision.py).

Owner: Lea Gur-Arie
"""
from pydantic import BaseModel, ConfigDict

from ed_admission.environment.decision import Flag


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class Finding(BaseModel):
    """One thing the Patient Agent states about the patient, and the exact words of the record that back it."""
    claim: str
    quote: str
    grounded: bool | None = None         # set by the assess_patient tool: the quote really appears in the record


class PatientAssessment(AgentOutput):
    """What the Patient Agent returns: everything the Coordinator needs to know about the patient — it never reads
    the record itself. One agent reads the record once and answers all of it."""
    target_ward: str                     # the ward the patient is admitted to (level 1 of the ladder) — or
                                         # "internal_medicine" when the record names neither A nor B: the
                                         # Coordinator chooses by occupancy (tools/rank_placements.py)
    binding_request: bool = False        # the physician requested this ward: never changed
    severe: bool                         # needs observation or intensive care: never waits
    intensive_care: bool
    observation: bool
    isolation: bool
    flags: list[Flag] = []               # prompt_injection, missing_vital_sign, severity_judgment_note
    note: str | None = None              # the judgment note: the level is kept, but the record argues for less
    findings: list[Finding] = []

    @property
    def bed_type(self) -> str:
        """The bed the patient needs: intensive care; isolation (also with observation); observation; regular."""
        if self.intensive_care:
            return "intensive_care"
        if self.isolation:
            return "isolation"
        return "observation" if self.observation or self.severe else "regular"

    @property
    def target(self) -> str:
        """The target ward the bed search and the hard rules use: intensive care whenever the patient needs intensive
        care — the physician's request or NEWS2 7 or more (a system rule, in code for both systems, 2026-10-15) —
        else target_ward as assessed."""
        return "intensive_care" if self.intensive_care else self.target_ward

    @property
    def never_waits(self) -> bool:
        return self.severe or self.observation or self.intensive_care
