"""Coordinator Agent — manages the placement case from trigger to recommendation.

Integrates the whole case: the level of care and judgment notes from the Severity Agent, the bed type from the
Supervision Agent, the clinical problem, target ward and physician's request from the Clinical Problem Agent, and
the wards' reports, including the shift and the weekend — and decides from all of them together which ward and bed
fit. A discharge the report shows is worth waiting for only in a weekday morning shift, unless the ward says otherwise.

Sees every ward's report (free beds, cleaning, discharges today — never the wards' private notes) and uses it,
with the patient-side agents' findings and rank_placements, to choose where to ask. The flow:
  1. Proposes a ward and bed to that ward's Department Agent.
  2. The Department Agent has the final word on its ward: accepts, declines with a reason, or offers a condition.
     A decline sends the Coordinator to the next option on the ladder ("ping-pong").
  3. If no ward on the ladder offers a bed that meets the need, the Coordinator never lowers the need itself:
     it returns to the Supervision Agent with the offers it got (isolation room as a last resort, or escalate).
  4. Accepted -> hold the bed -> Safety Critic -> the shift manager's approval.
The Safety Critic escalates only through the Coordinator.
Never accepts an offer below the need, never relies on second-hand information about another ward (asks that ward),
and gives the exact needs when it asks again. At most 3 requests per ward and 8 per case, then escalate
(config/clinical_rules/placement_rules.yaml, negotiation).
An agent that fails gets one retry; then the case is escalated — never a guess. Each case has a budget.

Tools: Toolbox("coordinator") — every report, rank_placements, ask_ward, hold, escalate.
Interfaces: docs/design/agent_interfaces.md

Owner: Lea (Product)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import WaitingPatient


def run(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    """The whole system as one decider: calls the other agents' `run` (and Supervision's `run_back`), ranks with
    rank_placements, negotiates, and returns one decision per patient. Hands every case's trace to the
    measurement with observability.tracing.collect(trace). Measured with
    scripts/run_metrics.py --decider ed_admission.agents.coordinator_agent:run"""
    raise NotImplementedError("Coordinator Agent: not built yet")
