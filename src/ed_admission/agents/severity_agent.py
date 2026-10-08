"""Severity Agent — sets the required level of care; NEWS2 is the floor, it may only escalate, with justification.

Intensive care: a physician's request is binding; NEWS2 7 or more is a recommendation, for a physician's approval.
In situations that need clinical judgment (e.g. a limit on treatment) it never lowers the level: it keeps it and
adds a note, with a quote, for the Coordinator Agent.
A missing vital sign is never assumed normal: it is flagged (missing_vital_sign), and a value written only in the
text is used, with a quote.

The judgment situations (approved; records P-061-P-072, challenge 6):
  raise              a treatment that needs intensive care (vasopressor, BiPAP) · a deteriorating trend
  keep, add a note   a limit on treatment ("not a candidate for intensive care") · confusion that is not new
  which data counts  a faulty measurement that was repeated · two physicians who disagree (the senior's later note)

Tools: Toolbox("severity") — the record, compute_news2.
Interfaces: docs/design/agent_interfaces.md

Owner: Galit (Model)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.agent_outputs import SeverityOutput
from ed_admission.tools.toolbox import Toolbox


def run(toolbox: Toolbox, patient_id: str) -> SeverityOutput:
    """The entry point the Coordinator Agent calls, and the one scripts/run_agent_metrics.py measures."""
    raise NotImplementedError("Severity Agent: not built yet")
