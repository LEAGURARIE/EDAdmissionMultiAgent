"""Patient Agent — reads the patient's record once and answers everything about the patient.

It is the only agent that sees the record. It returns a PatientAssessment (contracts/agent_outputs.py): the target
ward, whether the physician requested it, the level of care, the bed type (observation, isolation, intensive care),
the flags, a judgment note for the Coordinator, and every finding with the exact words of the record that back it.
It never sees a ward: the wards are the Department Agents' and the Coordinator's.

Tools: Toolbox("patient") — read_patient_record, compute_news2, clinical_lists.
Interfaces: docs/design/agent_interfaces.md

Owner: Lea Gur-Arie
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.tools.toolbox import Toolbox


def run(toolbox: Toolbox, patient_id: str) -> PatientAssessment:
    """The entry point the Coordinator's assess_patient tool calls, and the one scripts/run_agent_metrics.py measures."""
    raise NotImplementedError("Patient Agent: not built yet")
