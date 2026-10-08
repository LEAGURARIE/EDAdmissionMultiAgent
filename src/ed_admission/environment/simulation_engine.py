"""The simulation engine: the simulated clock, and the only code that changes the hospital's state.

Two ways to run it:
  scripted day  the clock moves from event to event (discharges, cleaning, admission decisions, approvals);
                after each event that may free a bed, the decider is asked about the waiting patients
  case mode     one admission decision at one moment (a ward-state template); the decider answers once,
                nothing is applied and the clock does not move — see decide_case

Nobody changes state directly: deciders return decisions, the engine applies them, and every bed status change
must be one of ALLOWED_TRANSITIONS. The agents' tools (tools/) call the actions at the bottom of this file.

The file is in sections: scheduling · event handlers · decision loop · case mode · actions · helpers.

Owner: integrator
"""
import heapq
import math
import time
from dataclasses import dataclass, field

from ed_admission import config
from ed_admission.environment.decision import Action, Decision, Escalation, Placement, Policy
from ed_admission.environment.events import MINUTES_PER_DAY, Event, EventType, to_clock
from ed_admission.environment.hospital import Bed, BedStatus, Hospital, WaitingPatient
from ed_admission.environment.ward_report import ward_report

# The only bed status changes allowed. Anything else raises InvalidTransition.
ALLOWED_TRANSITIONS = {
    (BedStatus.FREE, BedStatus.HELD),          # held for a patient
    (BedStatus.HELD, BedStatus.FREE),          # hold expired or released
    (BedStatus.HELD, BedStatus.OCCUPIED),      # placement approved by the shift manager
    (BedStatus.OCCUPIED, BedStatus.CLEANING),  # discharge
    (BedStatus.CLEANING, BedStatus.FREE),      # cleaning done
}

# Events after which a bed may be free for someone in the queue (or an approval fell through).
DECISION_EVENTS = {EventType.ADMISSION_DECISION, EventType.CLEANING_DONE, EventType.HOLD_EXPIRED,
                   EventType.APPROVAL_DONE}


class InvalidTransition(Exception):
    """Raised when an action would move a bed to a status it may not move to."""


@dataclass
class LogEntry:
    time: int
    message: str

    def __str__(self) -> str:
        return f"{to_clock(self.time)}  {self.message}"


@dataclass
class SimulationEngine:
    hospital: Hospital
    clock: int                                  # current simulated minute
    day: str | None = None                      # day of the week, e.g. "Saturday" — shown in the ward reports
    policy: Policy | None = None                # the decider; None = nobody decides (the queue only grows)

    # Times, from config/hospital.yaml
    cleaning_minutes: int = field(default_factory=lambda: config.operations()["cleaning_minutes"])
    hold_minutes: int = field(default_factory=lambda: config.operations()["hold_minutes"])
    approval_minutes: int = field(default_factory=lambda: config.operations()["approval_minutes"])

    # What happened — the raw material for the metrics and for the printed log
    log: list[LogEntry] = field(default_factory=list)
    triggers: list[Event] = field(default_factory=list)          # every admission decision, in order
    placements: list[Placement] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)
    holds: list[dict] = field(default_factory=list)              # every hold, with the ward's note effects then

    # Internal
    _awaiting_approval: dict[str, Decision] = field(default_factory=dict)   # patient id -> decision being approved
    _queue: list = field(default_factory=list)                              # (time, sequence, event) heap
    _sequence: int = 0                                                      # keeps same-minute events in order
    _notes_effects: dict = field(default_factory=dict)                      # (ward, note text) -> its effects
    _deciding_minutes: dict = field(default_factory=dict)                   # patient id -> decider's minutes

    # ------------------------------------------------------------------ scheduling

    def schedule(self, event: Event) -> None:
        heapq.heappush(self._queue, (event.time, self._sequence, event))
        self._sequence += 1

    def next_event_time(self) -> int | None:
        return self._queue[0][0] if self._queue else None

    def advance_to(self, minute: int) -> list[Event]:
        """Move the clock to `minute`, applying every event due by then. Returns the events applied."""
        applied = []
        while self._queue and self._queue[0][0] <= minute:
            event_time, _, event = heapq.heappop(self._queue)
            self.clock = event_time
            self._apply(event)
            self._run_policy(event)
            applied.append(event)
        self.clock = max(self.clock, minute)
        return applied

    def run_to_end(self) -> None:
        """Jump from event to event until nothing is left."""
        while self._queue:
            self.advance_to(self.next_event_time())

    # ------------------------------------------------------------------ event handlers

    def _apply(self, event: Event) -> None:
        handlers = {
            EventType.DISCHARGE: self._on_discharge,
            EventType.CLEANING_DONE: self._on_cleaning_done,
            EventType.ADMISSION_DECISION: self._on_admission_decision,
            EventType.HOLD_EXPIRED: self._on_hold_expired,
            EventType.APPROVAL_DONE: self._on_approval_done,
            EventType.WARD_NOTE: self._on_ward_note,
            EventType.WARD_NOTE_END: self._on_ward_note_end,
        }
        handlers[event.event_type](event)

    def _on_discharge(self, event: Event) -> None:
        bed = self._bed(event.ward_id, event.bed_number)
        patient = bed.patient_id
        self._move(bed, BedStatus.CLEANING)
        bed.patient_id = None
        bed.expected_discharge_at = None
        bed.cleaning_until = self.clock + self.cleaning_minutes
        bed.in_report = event.in_report
        unreported = "" if event.in_report else " — not updated in the system"
        self._write(f"discharge   {event.ward_id} bed {bed.number} ({patient}) -> cleaning{unreported}")
        self.schedule(Event(time=bed.cleaning_until, event_type=EventType.CLEANING_DONE,
                            ward_id=event.ward_id, bed_number=bed.number))

    def _on_cleaning_done(self, event: Event) -> None:
        bed = self._bed(event.ward_id, event.bed_number)
        self._move(bed, BedStatus.FREE)
        bed.cleaning_until = None
        self._write(f"cleaned     {event.ward_id} bed {bed.number} -> free")

    def _on_admission_decision(self, event: Event) -> None:
        self.hospital.emergency_queue.append(WaitingPatient(patient_id=event.patient_id, decided_at=self.clock))
        self.triggers.append(event)
        self._write(f"ADMISSION   {event.patient_id} joins the Emergency Department queue — TRIGGER")

    def _on_ward_note(self, event: Event) -> None:
        ward = self.hospital.ward(event.ward_id)
        ward.notes.append(event.text)
        ward.note_effects.extend(event.effects)
        self._notes_effects[(event.ward_id, event.text)] = list(event.effects)
        self._write(f"note        {event.ward_id}: private note starts")

    def _on_ward_note_end(self, event: Event) -> None:
        ward = self.hospital.ward(event.ward_id)
        ward.notes.remove(event.text)
        for effect in self._notes_effects.pop((event.ward_id, event.text), []):
            ward.note_effects.remove(effect)
        self._write(f"note        {event.ward_id}: private note ends")

    def _on_hold_expired(self, event: Event) -> None:
        bed = self._bed(event.ward_id, event.bed_number)
        still_this_hold = (bed.status == BedStatus.HELD and bed.held_for == event.patient_id
                           and bed.hold_expires_at == event.time)
        if not still_this_hold:
            return                                  # the hold already became a placement, or was released
        self._move(bed, BedStatus.FREE)
        bed.held_for = None
        bed.hold_expires_at = None
        self._write(f"hold expired {event.ward_id} bed {bed.number} ({event.patient_id}) -> free")

    def _on_approval_done(self, event: Event) -> None:
        """The shift manager's approval arrives: the held bed becomes the patient's. If the hold was lost
        in the meantime, the patient goes back to waiting and the decider is asked again."""
        decision = self._awaiting_approval.pop(event.patient_id)
        patient = next(p for p in self.hospital.emergency_queue if p.patient_id == event.patient_id)
        patient.awaiting_approval = False
        bed = self._bed(event.ward_id, event.bed_number)
        if bed.status != BedStatus.HELD or bed.held_for != event.patient_id:
            self._write(f"approval    {event.patient_id}: {event.ward_id} bed {event.bed_number} "
                        f"no longer held — back to waiting")
            return
        self.assign_bed(event.ward_id, event.bed_number, event.patient_id)
        self.placements.append(Placement(
            patient_id=patient.patient_id, decided_at=patient.decided_at, placed_at=self.clock,
            ward_id=event.ward_id, bed_number=event.bed_number, bed_type=bed.bed_type.value,
            awaiting_transfer_to=decision.awaiting_transfer_to, reason=decision.reason,
            deciding_minutes=self._deciding_minutes.pop(patient.patient_id, 0)))

    # ------------------------------------------------------------------ decision loop (scripted day)

    def _run_policy(self, event: Event) -> None:
        """Hand the trigger and the waiting patients to the decider, then apply its decisions in its order.
        The environment does not rank the queue — who goes first is the decider's choice."""
        waiting = [p for p in self.hospital.emergency_queue if not p.awaiting_approval]
        if self.policy is None or not waiting or event.event_type not in DECISION_EVENTS:
            return
        if event.event_type != EventType.ADMISSION_DECISION:
            self._write(f"TRIGGER     bed freed — {len(waiting)} waiting")

        # The decider's real time counts: measured, rounded up to whole simulated minutes (40 seconds -> 1 minute,
        # 3 minutes -> 3), so a slow decider pays for it in the patients' time to a bed. Under a second is instant
        # (plain code).
        started = time.perf_counter()
        decisions = self.policy(self, event, waiting)
        seconds = time.perf_counter() - started
        measured = math.ceil(seconds / 60) if seconds >= 1 else 0
        by_id = {p.patient_id: p for p in waiting}
        for decision in decisions:
            if decision.patient_id not in by_id:
                raise ValueError(f"decision for {decision.patient_id}, who is not waiting")
            patient = by_id[decision.patient_id]

            if decision.action in (Action.PLACE, Action.TEMPORARY_PLACE):
                # Hold the bed now; the shift manager's approval arrives approval_minutes later. In the simulation
                # the shift manager always approves (no approval screen yet). The hold outlasts the approval.
                # Time the decider spent first (e.g. phone calls) comes before the approval; the hold covers it.
                deciding = measured + decision.handling_minutes
                self.hold_bed(decision.ward_id, decision.bed_number, patient.patient_id, extra_minutes=deciding)
                patient.awaiting_approval = True
                self._awaiting_approval[patient.patient_id] = decision
                self._deciding_minutes[patient.patient_id] = deciding
                self.schedule(Event(time=self.clock + deciding + self.approval_minutes,
                                    event_type=EventType.APPROVAL_DONE,
                                    ward_id=decision.ward_id, bed_number=decision.bed_number,
                                    patient_id=patient.patient_id))
            elif decision.action == Action.ESCALATE:
                # The shift manager takes over: the patient leaves the queue and the simulation stops following them.
                self.hospital.emergency_queue.remove(patient)
                self.escalations.append(Escalation(
                    patient_id=patient.patient_id, decided_at=patient.decided_at,
                    escalated_at=self.clock + measured + decision.handling_minutes,
                    reason=decision.reason, flags=[f.value for f in decision.flags]))
                self._write(f"ESCALATED   {patient.patient_id} -> shift manager, leaves the queue: {decision.reason}")
            # Action.WAIT: nothing to apply — the patient stays in the queue and is asked about again at the next trigger.

    # ------------------------------------------------------------------ case mode

    def decide_case(self, patient_id: str, policy: Policy) -> Decision:
        """One admission decision at the current moment, one answer from the decider — nothing is applied and
        the clock does not move. Whatever the decider does inside (e.g. up to two Safety Critic Agent rounds)
        ends in one decision: place, temporary placement, wait, or escalate to the shift manager."""
        event = Event(time=self.clock, event_type=EventType.ADMISSION_DECISION, patient_id=patient_id)
        patient = WaitingPatient(patient_id=patient_id, decided_at=self.clock)
        self.hospital.emergency_queue.append(patient)
        self.triggers.append(event)
        self._write(f"ADMISSION   {patient_id} — case mode, one decision")
        decisions = [d for d in policy(self, event, [patient]) if d.patient_id == patient_id]
        if len(decisions) != 1:
            raise ValueError(f"case mode expects exactly one decision for {patient_id}, got {len(decisions)}")
        return decisions[0]

    def decide_cases(self, patients: list[tuple[str, int]], policy: Policy) -> list[Decision]:
        """Case mode for several patients who wait at the same moment — the paired cases. `patients` holds each
        patient's id and how many minutes before now the admission was decided. One decision per patient; nothing
        is applied and the clock does not move. Two decisions may never use the same bed."""
        waiting = [WaitingPatient(patient_id=pid, decided_at=self.clock - before) for pid, before in patients]
        for patient in waiting:
            self.hospital.emergency_queue.append(patient)
            self.triggers.append(Event(time=self.clock, event_type=EventType.ADMISSION_DECISION,
                                       patient_id=patient.patient_id))
        self._write(f"ADMISSION   {', '.join(pid for pid, _ in patients)} — case mode, one decision each")
        event = self.triggers[-1]
        decisions = {d.patient_id: d for d in policy(self, event, waiting)}
        if sorted(decisions) != sorted(pid for pid, _ in patients):
            raise ValueError(f"case mode expects one decision for each of {[pid for pid, _ in patients]}")
        beds = [(d.ward_id, d.bed_number) for d in decisions.values() if d.bed_number is not None]
        if len(beds) != len(set(beds)):
            raise ValueError(f"two decisions use the same bed: {beds}")
        return [decisions[pid] for pid, _ in patients]

    # ------------------------------------------------------------------ actions (the tools will call these)

    def department_status(self, ward_id: str) -> dict:
        """The ward's report — see ward_report.py for what it holds and what it never holds."""
        return ward_report(self.hospital.ward(ward_id), self.clock, self.today())

    def today(self) -> str | None:
        """The day of the week now — a scripted day that passes midnight moves on to the next day."""
        if self.day is None:
            return None
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        return days[(days.index(self.day) + self.clock // MINUTES_PER_DAY) % 7]

    def ward_notes(self, ward_id: str) -> list[str]:
        """What only this ward knows. For that ward's Department Agent alone — never part of the ward report."""
        return list(self.hospital.ward(ward_id).notes)

    def hold_bed(self, ward_id: str, bed_number: int, patient_id: str, extra_minutes: int = 0) -> None:
        bed = self._bed(ward_id, bed_number)
        self._move(bed, BedStatus.HELD)
        bed.held_for = patient_id
        bed.hold_expires_at = self.clock + extra_minutes + self.hold_minutes
        self.holds.append({"time": self.clock, "ward_id": ward_id, "bed_number": bed_number,
                           "bed_type": bed.bed_type.value, "patient_id": patient_id,
                           "note_effects": [dict(e) for e in self.hospital.ward(ward_id).note_effects]})
        self._write(f"held        {ward_id} bed {bed_number} for {patient_id} until {to_clock(bed.hold_expires_at)}")
        self.schedule(Event(time=bed.hold_expires_at, event_type=EventType.HOLD_EXPIRED,
                            ward_id=ward_id, bed_number=bed_number, patient_id=patient_id))

    def release_hold(self, ward_id: str, bed_number: int) -> None:
        bed = self._bed(ward_id, bed_number)
        self._move(bed, BedStatus.FREE)
        self._write(f"released    {ward_id} bed {bed_number} (was held for {bed.held_for})")
        bed.held_for = None
        bed.hold_expires_at = None

    def assign_bed(self, ward_id: str, bed_number: int, patient_id: str) -> None:
        """Final placement, after the shift manager's approval. The bed must be held for this patient."""
        bed = self._bed(ward_id, bed_number)
        if bed.status != BedStatus.HELD or bed.held_for != patient_id:
            raise InvalidTransition(f"{ward_id} bed {bed_number} is not held for {patient_id}")
        self._move(bed, BedStatus.OCCUPIED)
        bed.patient_id = patient_id
        bed.in_report = True
        bed.held_for = None
        bed.hold_expires_at = None
        self.hospital.emergency_queue = [p for p in self.hospital.emergency_queue if p.patient_id != patient_id]
        self._write(f"PLACED      {patient_id} -> {ward_id} bed {bed_number}")

    # ------------------------------------------------------------------ helpers

    def _bed(self, ward_id: str, bed_number: int) -> Bed:
        return self.hospital.ward(ward_id).bed(bed_number)

    def _move(self, bed: Bed, new_status: BedStatus) -> None:
        if (bed.status, new_status) not in ALLOWED_TRANSITIONS:
            raise InvalidTransition(f"bed {bed.number}: {bed.status.value} -> {new_status.value} is not allowed")
        bed.status = new_status

    def _write(self, message: str) -> None:
        self.log.append(LogEntry(self.clock, message))
