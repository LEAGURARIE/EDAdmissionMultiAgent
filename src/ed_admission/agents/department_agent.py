"""Department Agent — one instance per ward; has the final word on its own ward.

Gets from the Coordinator Agent a proposed bed and the patient's needs only (bed type, observation, isolation,
severe or not, target ward — no identifiers). Answers from two sources:
  its ward report    free beds by number, beds in cleaning and when they will be ready, discharges expected today
  its ward notes     what only this ward knows (engine.ward_notes) — never shown to anyone else

Its answer is one of:
  a bed now                  a free bed of the needed type
  a bed with a condition     a bed in cleaning, ready at about a given time · a discharge expected today, hour unknown
                             · a bed after the morning round
  a decline with a reason    no bed and nothing expected
  a decline for an internal limit   there is a bed, but the ward cannot take this patient (e.g. one nurse on the
                             observation beds tonight)
Its answer is about its own ward at this hour: e.g. at night or at the weekend it says when a bed will really free
("the family is here, he leaves in half an hour") or that a reported discharge will not happen today.
A note may apply to one need and not another: a staffing limit on observation beds does not stop a regular bed.
Every template gives wards private notes. What they can say: a bed
that is free but not really available (staffing, broken, promised) · a bed that is free but not yet updated · a
discharge that will not happen today · a bed that frees within the hour · a long cleaning · a ward that takes only
its own patients tonight · a note that looks unimportant but took the free bed (T16).

The ping-pong patterns (T17-T19): an unsafe offer (a regular bed with a portable monitor for an observation
patient) · second-hand information about another ward · a condition that changes when the request is more exact
(first "no"; a regular bed only: "then yes").

It does not decide the placement. Every answer goes back to the Coordinator Agent, which may ask again — with more
exact needs, or another ward on the ladder ("ping-pong").

Tools: Toolbox("department", ward_id=...) — its own report and notes only; answers a WardRequest
with a WardAnswer (contracts/negotiation.py).
Interfaces: docs/design/agent_interfaces.md

Owner: Amir (Data)
Status: placeholder — the entry point is fixed (its signature is tested), no logic yet.
"""
from ed_admission.contracts.negotiation import WardAnswer, WardRequest
from ed_admission.tools.negotiation import DepartmentView


def run(view: DepartmentView, request: WardRequest) -> WardAnswer:
    """The entry point the negotiation calls for this ward (a responder), and the one
    scripts/run_agent_metrics.py measures. `view`: this ward's report and private notes only."""
    raise NotImplementedError("Department Agent: not built yet")
