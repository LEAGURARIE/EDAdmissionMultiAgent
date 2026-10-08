"""Supervision Agent — decides which bed type the patient needs: an observation bed, the isolation room, or both.

Reads the record (structured fields and free text) against config/clinical_rules/observation_conditions.yaml and
isolation_conditions.yaml, with a quote for every finding. Both needs together -> the isolation room.

The Coordinator Agent comes back to it when no ward on the ladder offers a bed that meets the need (e.g. only a
regular bed for an observation patient). It then decides: the isolation room as a last resort (with the shift
manager's approval), or escalate. It never lowers the need because beds are missing.

Tools: Toolbox("supervision") — the record, compute_news2, the observation and isolation lists.
Interfaces: docs/design/agent_interfaces.md

Owner: Galit (Model)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.agent_outputs import BackToSupervisionAnswer, Offer, SupervisionOutput
from ed_admission.tools.toolbox import Toolbox


def run(toolbox: Toolbox, patient_id: str) -> SupervisionOutput:
    """The entry point the Coordinator Agent calls, and the one scripts/run_agent_metrics.py measures."""
    raise NotImplementedError("Supervision Agent: not built yet")


def run_back(toolbox: Toolbox, patient_id: str, offers: list[Offer]) -> BackToSupervisionAnswer:
    """Back to Supervision: no ward on the ladder offered a bed that meets the need. `offers` holds every ward's
    answer the Coordinator Agent got. Never lowers the need: the isolation room as a last resort, or escalate."""
    raise NotImplementedError("Supervision Agent, back to Supervision: not built yet")
