"""Tests for the decision loop: the engine opens a case for every waiting patient and applies its decision.
No agents — the policies here are a first-free-bed decider (tests/simple_deciders.py) and small stubs."""
from pathlib import Path

from simple_deciders import first_free_bed
from ed_admission.environment import (
    BedStatus,
    BedType,
    Decision,
    Event,
    EventType,
    SimulationEngine,
    load_hospital,
    load_scenario,
    to_minutes,
)
from ed_admission.environment.hospital import WaitingPatient

PROJECT = Path(__file__).resolve().parents[1]
HOSPITAL = PROJECT / "config" / "hospital.yaml"
BUSY_DAY = PROJECT / "tests" / "fixtures" / "small_day.yaml"       # the engine tests keep a small fixed day


def make_engine(policy, free: dict[str, list[int]]) -> SimulationEngine:
    """Every bed occupied except `free`; the given policy decides."""
    hospital = load_hospital(HOSPITAL)
    for ward_id, ward in hospital.wards.items():
        for bed in ward.beds:
            if bed.number not in free.get(ward_id, []):
                bed.status, bed.patient_id = BedStatus.OCCUPIED, f"inpatient-{ward_id}-{bed.number}"
    return SimulationEngine(hospital=hospital, clock=to_minutes("08:00"), policy=policy)


def admit(engine, clock: str, patient_id: str) -> None:
    engine.schedule(Event(time=to_minutes(clock), event_type=EventType.ADMISSION_DECISION, patient_id=patient_id))


def discharge(engine, clock: str, ward_id: str, bed_number: int) -> None:
    engine.schedule(Event(time=to_minutes(clock), event_type=EventType.DISCHARGE,
                          ward_id=ward_id, bed_number=bed_number))


def test_placed_at_once_when_a_bed_is_free():
    engine = make_engine(first_free_bed, free={"cardiology": [3]})
    admit(engine, "09:00", "P-001")
    engine.run_to_end()

    [placement] = engine.placements
    assert (placement.ward_id, placement.bed_number, placement.wait_minutes) == ("cardiology", 3, 15)   # approval
    assert engine.hospital.ward("cardiology").bed(3).patient_id == "P-001"
    assert engine.hospital.emergency_queue == []


def test_waits_then_placed_when_cleaning_is_done():
    engine = make_engine(first_free_bed, free={})
    admit(engine, "09:00", "P-001")
    discharge(engine, "10:00", "internal_medicine_b", 4)       # clean at 10:45
    engine.run_to_end()

    [placement] = engine.placements
    assert placement.ward_id == "internal_medicine_b" and placement.bed_number == 4
    assert placement.wait_minutes == 120                       # 09:00 -> clean 10:45 -> approved 11:00


def test_a_freed_bed_goes_to_the_patient_the_decider_chooses():
    """When a bed frees while several patients wait, the decider gets all of them (a bed case) and chooses — the
    environment does not rank the waiting patients for it."""
    def latest_first(engine, event, waiting):
        if engine.hospital.ward("cardiology").bed(5).status != BedStatus.FREE:
            return [Decision.wait(p.patient_id) for p in waiting]
        newest = max(waiting, key=lambda p: p.decided_at)
        return [Decision.place(newest.patient_id, "cardiology", 5)]

    engine = make_engine(latest_first, free={})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:30", "P-002")
    discharge(engine, "10:00", "cardiology", 5)                # one bed, clean at 10:45
    engine.run_to_end()

    assert [p.patient_id for p in engine.placements] == ["P-002"]
    assert [p.patient_id for p in engine.hospital.emergency_queue] == ["P-001"]

def test_first_free_bed_serves_in_decision_order():
    engine = make_engine(first_free_bed, free={})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:30", "P-002")
    discharge(engine, "10:00", "cardiology", 5)
    engine.run_to_end()

    assert [p.patient_id for p in engine.placements] == ["P-001"]


def test_environment_does_not_reveal_patient_needs():
    """The queue and the events carry facts only — no severity, notes or expected answers."""
    allowed = {"patient_id", "decided_at", "awaiting_approval"}
    assert set(WaitingPatient.model_fields) == allowed
    assert not {"severe", "note", "expected"} & set(Event.model_fields)

    engine = load_scenario(BUSY_DAY, HOSPITAL)
    engine.run_to_end()
    for patient in engine.hospital.emergency_queue:
        assert set(patient.model_dump()) == allowed


def test_first_free_bed_never_uses_intensive_care_or_isolation():
    engine = make_engine(first_free_bed, free={"intensive_care": [1, 7], "general_surgery": [13]})
    admit(engine, "09:00", "P-001")
    engine.run_to_end()

    assert engine.placements == []
    assert [p.patient_id for p in engine.hospital.emergency_queue] == ["P-001"]


def test_escalated_patient_leaves_the_queue_and_is_recorded():
    calls = []

    def always_escalate(engine, event, waiting):
        calls.extend(p.patient_id for p in waiting)
        return [Decision.escalate(p.patient_id, "no safe option") for p in waiting]

    engine = make_engine(always_escalate, free={})
    admit(engine, "09:00", "P-001")
    discharge(engine, "10:00", "cardiology", 5)                # a bed frees later — the patient is no longer asked about
    engine.run_to_end()

    assert calls == ["P-001"]
    assert engine.hospital.emergency_queue == [] and engine.placements == []
    [escalation] = engine.escalations
    assert (escalation.patient_id, escalation.wait_minutes, escalation.reason) == ("P-001", 0, "no safe option")


def test_busy_day_with_first_free_bed():
    engine = load_scenario(BUSY_DAY, HOSPITAL)
    engine.policy = first_free_bed
    engine.run_to_end()

    placed = {p.patient_id for p in engine.placements}
    assert placed == {"P-001", "P-002", "P-003", "P-004", "P-005"}
    assert engine.hospital.emergency_queue == []
    beds = [(p.ward_id, p.bed_number) for p in engine.placements]
    assert len(beds) == len(set(beds))                         # no bed given twice
    assert all(p.bed_type in (BedType.REGULAR.value, BedType.OBSERVATION.value) for p in engine.placements)


def test_placement_waits_for_the_shift_managers_approval():
    asked = []

    def counting_first_free_bed(engine, event, waiting):
        asked.extend(p.patient_id for p in waiting)
        return first_free_bed(engine, event, waiting)

    engine = make_engine(counting_first_free_bed, free={"cardiology": [3]})
    admit(engine, "09:00", "P-001")
    engine.advance_to(to_minutes("09:14"))
    bed = engine.hospital.ward("cardiology").bed(3)
    assert bed.status == BedStatus.HELD and engine.placements == []          # held, waiting for approval
    assert engine.hospital.emergency_queue[0].awaiting_approval

    engine.advance_to(to_minutes("09:15"))                                   # approval after 15 minutes
    assert bed.status == BedStatus.OCCUPIED and bed.patient_id == "P-001"
    assert engine.hospital.emergency_queue == [] and asked == ["P-001"]      # asked once, not again while held
