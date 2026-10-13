"""escalate_to_human — hand the placement case to the shift manager, with a reason (and the flags raised).

Returns the escalate decision; in a scripted day the engine takes the patient out of the queue and records it.

Owner: Lea Gur-Arie
"""
from ed_admission.environment.decision import Decision, Flag


def escalate_to_human(patient_id: str, reason: str, flags: list[Flag] | None = None) -> Decision:
    if not reason:
        raise ValueError("an escalation needs a reason")
    return Decision.escalate(patient_id, reason, flags)
