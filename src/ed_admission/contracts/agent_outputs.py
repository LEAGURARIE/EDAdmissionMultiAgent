"""What each patient-side agent and the Safety Critic Agent return — the part that is measured on its own — and what
the Coordinator Agent passes back to the Supervision Agent when no ward offers a bed that meets the need.

Each agent may return more (its reasons, its quotes, a note for the Coordinator Agent): extra fields are allowed.
The fields below are the ones evaluation/agent_metrics.py compares with the expected answers, so every owner can
measure their agent alone, before the whole system works. The Department Agent needs no new contract: it answers a
WardRequest with a WardAnswer (contracts/negotiation.py).

Owner: Lea (integrator)
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ed_admission.contracts.negotiation import WardAnswer
from ed_admission.environment.decision import Flag


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class ClinicalProblemOutput(AgentOutput):
    target_ward: str                     # the ward the patient is admitted to (level 1 of the ladder)
    flags: list[Flag] = []               # e.g. prompt_injection


class SeverityOutput(AgentOutput):
    severe: bool                         # needs observation or intensive care: never waits
    intensive_care: bool
    flags: list[Flag] = []               # e.g. missing_vital_sign, severity_judgment_note


class SupervisionOutput(AgentOutput):
    observation: bool
    isolation: bool


class SafetyVerdict(AgentOutput):
    approve: bool                        # False: the decision goes back to the Coordinator Agent
    reasons: list[str] = []


class Offer(AgentOutput):
    """One ward's answer, as the Coordinator Agent passes it back to the Supervision Agent."""
    ward_id: str
    answer: WardAnswer


class BackToSupervisionAnswer(AgentOutput):
    """Back to Supervision: what to do when no ward on the ladder offered a bed that meets the need."""
    decision: Literal["isolation_room_last_resort", "escalate"]   # last resort: with the shift manager's approval
    reason: str
