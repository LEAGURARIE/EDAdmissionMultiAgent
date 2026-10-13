"""Coordinator Agent — integrates the whole event and decides where the patient goes.

It sees what neither of the two other agents sees — every ward's report, and both agents' answers — and it never reads
the record itself: what it knows about the patient is the Patient Agent's assessment. It asks the wards, weighs what
their Department Agents answer, and decides: place, temporary placement, wait, or escalate. The code keeps the limits
whatever it decides — the ranking (rank_placements), the hard rules (decide), the limits on requests to the wards.

Tools: Toolbox("coordinator") — assess_patient, all_department_status, rank_placements, ask_ward, hold_bed,
release_hold, escalate_to_human, decide.
Interfaces: docs/design/agent_interfaces.md

Owner: Lea Gur-Arie
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import WaitingPatient


def run(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    """The whole system as one decider: one decision per waiting patient. Measured with
    scripts/run_metrics.py --decider ed_admission.agents.coordinator_agent:run"""
    raise NotImplementedError("Coordinator Agent: not built yet")
