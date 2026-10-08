"""The negotiation channel: the Coordinator Agent asks a ward, the ward's Department Agent answers ("ping-pong").

One Negotiation per placement case. It
  - passes the request to the ward's responder with only that ward's view (its report and its private notes);
  - counts requests and enforces the limits (config/clinical_rules/placement_rules.yaml, negotiation):
    3 per ward, 8 per case — past them it raises LimitReached, and the Coordinator escalates;
  - (the time the requests take is not set here: in a scripted day the engine measures the whole decision);
  - records every request and answer for the trace.

A responder is any callable (DepartmentView, WardRequest) -> WardAnswer: the Department Agent, or, for tests and
for the "simulated coordinator", `truthful_ward`, which answers from the ward's true state.

Owner: Lea (integrator)
"""
from dataclasses import dataclass, field
from typing import Callable

from ed_admission import config
from ed_admission.contracts.negotiation import Answer, WardAnswer, WardRequest
from ed_admission.environment.hospital import BedStatus, BedType
from ed_admission.environment.ward_truth import true_state


@dataclass
class DepartmentView:
    """What one ward's Department Agent sees: its own report and its own private notes — nothing about the patient
    beyond the request, and nothing about other wards."""
    ward_id: str
    report: dict
    notes: list[str]


Responder = Callable[[DepartmentView, WardRequest], WardAnswer]


class LimitReached(Exception):
    """Too many requests — to one ward, or in the whole case. The Coordinator escalates."""


@dataclass
class Negotiation:
    engine: object
    responders: dict[str, Responder]               # ward id -> its Department Agent (or a code responder)
    exchanges: list[dict] = field(default_factory=list)

    def view(self, ward_id: str) -> DepartmentView:
        return DepartmentView(ward_id, self.engine.department_status(ward_id), self.engine.ward_notes(ward_id))

    def ask(self, ward_id: str, request: WardRequest) -> WardAnswer:
        limits = config.placement_rules()["negotiation"]
        to_ward = sum(1 for e in self.exchanges if e["ward"] == ward_id)
        if to_ward >= limits["max_requests_per_ward"]:
            raise LimitReached(f"{limits['max_requests_per_ward']} requests to {ward_id} already")
        if len(self.exchanges) >= limits["max_requests_per_case"]:
            raise LimitReached(f"{limits['max_requests_per_case']} requests in this case already")
        answer = self.responders[ward_id](self.view(ward_id), request)
        self.exchanges.append({"ward": ward_id, "request": request.model_dump(), "answer": answer.model_dump(mode="json")})
        return answer


def truthful_ward(engine) -> Responder:
    """A code responder that answers from the ward's true state (environment/ward_truth.py) — the answer a ward gives
    on the phone. For tests and for the code baselines; the Department Agent reads the notes' text instead."""
    def respond(view: DepartmentView, request: WardRequest) -> WardAnswer:
        real = true_state(engine, patient_target=request.target_ward)
        beds = [b for b in real.hospital.ward(view.ward_id).beds
                if b.bed_type == BedType(request.bed_type) and b.status == BedStatus.FREE]
        if beds:
            chosen = next((b for b in beds if b.number == request.proposed_bed), beds[0])
            return WardAnswer(answer=Answer.ACCEPT, bed_number=chosen.number, reason="a bed is free")
        soon = [b for b in real.hospital.ward(view.ward_id).beds
                if b.bed_type == BedType(request.bed_type) and b.status == BedStatus.CLEANING]
        if soon and not request.severe:
            return WardAnswer(answer=Answer.CONDITION, bed_number=soon[0].number, reason="a bed is being cleaned",
                              condition="after cleaning",
                              available_in_minutes=max(0, soon[0].cleaning_until - engine.clock))
        return WardAnswer(answer=Answer.DECLINE, reason=f"no {request.bed_type} bed this ward can give now")
    return respond
