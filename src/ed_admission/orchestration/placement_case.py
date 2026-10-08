"""The placement case — its steps, enforced in code. The Coordinator Agent moves a case through them; a step out of
order is refused, so no recommendation reaches the shift manager without the patient assessed, the wards asked, the
options ranked and the Safety Critic's review.

    triggered → patient_assessed → availability_collected → ranked → [negotiating ⇄ back_to_supervision]
              → critic_reviewed → decided

  negotiating          the Coordinator asks Department Agents (tools/negotiation.py); a decline → the next option
  back_to_supervision  no ward on the ladder offers a bed that meets the need → the Supervision Agent decides:
                       isolation room as a last resort, or escalate
  decided              place · temporary placement · wait · escalate — to the shift manager for approval
Any step may go straight to `decided` with an escalation (an agent failed twice, a limit was reached).

Each step is recorded in the case's trace as a milestone (observability/tracing.py).

Owner: Lea (integrator)
"""
from dataclasses import dataclass, field

from ed_admission.environment.decision import Decision
from ed_admission.observability.tracing import Trace
from ed_admission.tools.negotiation import Negotiation

STEPS = ("triggered", "patient_assessed", "availability_collected", "ranked", "negotiating", "back_to_supervision",
         "critic_reviewed", "decided")
NEXT = {
    "triggered": {"patient_assessed"},
    "patient_assessed": {"availability_collected"},
    "availability_collected": {"ranked"},
    "ranked": {"negotiating", "critic_reviewed"},
    "negotiating": {"negotiating", "back_to_supervision", "critic_reviewed"},
    "back_to_supervision": {"negotiating", "critic_reviewed"},
    "critic_reviewed": {"negotiating", "critic_reviewed", "decided"},      # the critic may send it back, at most twice
    "decided": set(),
}
MAX_CRITIC_ROUNDS = 2


class StepOutOfOrder(Exception):
    """A step the placement case may not take now."""


@dataclass
class PlacementCase:
    patient_id: str
    trace: Trace
    negotiation: Negotiation | None = None
    step: str = "triggered"
    critic_rounds: int = 0
    decision: Decision | None = None
    history: list[str] = field(default_factory=lambda: ["triggered"])

    def move(self, step: str, output=None) -> None:
        escalating = step == "decided" and isinstance(output, Decision) and output.action.value == "escalate"
        if step not in NEXT[self.step] and not escalating:
            raise StepOutOfOrder(f"{self.patient_id}: {self.step} -> {step} is not allowed")
        if step == "critic_reviewed":
            self.critic_rounds += 1
            if self.critic_rounds > MAX_CRITIC_ROUNDS:
                raise StepOutOfOrder(f"{self.patient_id}: more than {MAX_CRITIC_ROUNDS} Safety Critic rounds")
        if step == "decided":
            self.decision = output
            self._mark_skipped_milestones()
        self.step = step
        self.history.append(step)
        self.trace.milestone(step, output.model_dump(mode="json") if isinstance(output, Decision) else output)

    def _mark_skipped_milestones(self) -> None:
        """An escalation can end the case early; the trace says which required steps were never reached."""
        reached = set(self.history)
        for step in ("patient_assessed", "availability_collected", "ranked", "critic_reviewed"):
            if step not in reached:
                self.trace.milestone(f"skipped:{step}")
