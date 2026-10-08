"""Safety Critic Agent — independent check of the recommendation before a human sees it.

Checks the hard rules, that every claim rests on a quote from the record, and that the required flags were raised.
At most 2 rounds per case. It escalates only through the Coordinator Agent.

Tools: Toolbox("safety_critic") — the record, compute_news2, the lists, every report; read only.
Interfaces: docs/design/agent_interfaces.md

Owner: Ron (Agent)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.agent_outputs import SafetyVerdict
from ed_admission.environment.decision import Decision
from ed_admission.tools.toolbox import Toolbox


def run(toolbox: Toolbox, patient_id: str, decision: Decision) -> SafetyVerdict:
    """The entry point the Coordinator Agent calls before the shift manager, and the one
    scripts/run_agent_metrics.py measures."""
    raise NotImplementedError("Safety Critic Agent: not built yet")
