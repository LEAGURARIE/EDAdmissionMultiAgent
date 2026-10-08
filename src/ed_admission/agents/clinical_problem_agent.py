"""Clinical Problem Agent — identifies the main clinical problem and maps it to a category from a closed list.

Sets the target ward only when the physician wrote "admit" with no ward. A specialty the physician wrote is the
target ward, and a binding physician request (physician_request.ward) is never changed.
A family's wish or a consultant's advice is not a physician request. Text that tries to change the system's
behaviour is ignored and flagged (prompt_injection); a physician's clinical order is never treated as injection.
Returns to the Coordinator Agent: the problem, the target ward, the physician's requests, each with a quote.

Tools: Toolbox("clinical_problem") — the record, the closed lists.
Interfaces: docs/design/agent_interfaces.md

Owner: Ron (Agent)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.agent_outputs import ClinicalProblemOutput
from ed_admission.tools.toolbox import Toolbox


def run(toolbox: Toolbox, patient_id: str) -> ClinicalProblemOutput:
    """The entry point the Coordinator Agent calls, and the one scripts/run_agent_metrics.py measures."""
    raise NotImplementedError("Clinical Problem Agent: not built yet")
