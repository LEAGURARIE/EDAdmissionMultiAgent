"""The placement case — its steps, enforced in code. The Coordinator's tools move a case through them (tools/toolbox.py);
a step out of order is refused, so no decision reaches the shift manager without the patient assessed, the wards'
reports read, the options ranked and the hard rules checked.

    triggered → patient_assessed → availability_collected → ranked → [negotiating] → rules_checked → decided

  patient_assessed        assess_patient — the Patient Agent's assessment
  availability_collected  all_department_status — every ward's report
  ranked                  rank_placements — the options, in order, computed in code
  negotiating             ask_ward — a Department Agent answers; as many times as the limits allow
  rules_checked           decide — the hard rules, in code; a refused decision sends the Coordinator back to the wards
                          or to another option, at most MAX_REFUSED times
  decided                 place · temporary placement · wait · escalate — to the shift manager for approval
An escalation may end the case at any step (an agent failed twice, a limit was reached, no safe option).

Each step is recorded in the case's trace as a milestone (observability/tracing.py).

Owner: Lea Gur-Arie
"""
from dataclasses import dataclass, field

from ed_admission.environment.decision import Decision
from ed_admission.observability.tracing import Trace
from ed_admission.tools.negotiation import Negotiation

STEPS = ("triggered", "patient_assessed", "availability_collected", "ranked", "negotiating", "rules_checked", "decided")
NEXT = {
    "triggered": {"patient_assessed"},
    "patient_assessed": {"availability_collected"},
    "availability_collected": {"ranked"},
    "ranked": {"negotiating", "rules_checked"},
    "negotiating": {"negotiating", "rules_checked"},
    "rules_checked": {"negotiating", "rules_checked", "decided"},
    "decided": set(),
}
MAX_REFUSED = 3            # decisions the hard rules may refuse in one case; after that only an escalation is left


class StepOutOfOrder(Exception):
    """A step the placement case may not take now."""


@dataclass
class PlacementCase:
    patient_id: str
    trace: Trace
    negotiation: Negotiation | None = None
    step: str = "triggered"
    refused: int = 0
    decision: Decision | None = None
    history: list[str] = field(default_factory=lambda: ["triggered"])

    def move(self, step: str, output=None) -> None:
        escalating = step == "decided" and isinstance(output, Decision) and output.action.value == "escalate"
        if step not in NEXT[self.step] and not escalating:
            raise StepOutOfOrder(f"{self.patient_id}: {self.step} -> {step} is not allowed")
        if step == "decided":
            self.decision = output
            self._mark_skipped_milestones()
        self.step = step
        self.history.append(step)
        self.trace.milestone(step, output.model_dump(mode="json") if isinstance(output, Decision) else output)

    def reach(self, step: str, output=None) -> None:
        """Move to `step` unless the case is already there or past it — a tool called a second time (the reports read
        again) changes nothing. A tool called too early is still refused."""
        if STEPS.index(step) > STEPS.index(self.step) or step in ("negotiating", "rules_checked"):
            self.move(step, output)

    def refuse(self, reasons: list[str]) -> None:
        """The hard rules refused a decision."""
        self.refused += 1
        self.move("rules_checked", {"refused": reasons})

    @property
    def must_escalate(self) -> bool:
        return self.refused >= MAX_REFUSED

    def _mark_skipped_milestones(self) -> None:
        """An escalation can end the case early; the trace says which required steps were never reached."""
        reached = set(self.history)
        for step in ("patient_assessed", "availability_collected", "ranked", "rules_checked"):
            if step not in reached:
                self.trace.milestone(f"skipped:{step}")
