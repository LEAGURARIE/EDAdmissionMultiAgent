"""Tests for the simulated hospital and the simulation engine. No database, no Docker, no agents."""
from pathlib import Path

import pytest

from ed_admission.environment import (
    BedStatus,
    BedType,
    Event,
    EventType,
    InvalidTransition,
    SimulationEngine,
    load_hospital,
    load_scenario,
    to_minutes,
)

PROJECT = Path(__file__).resolve().parents[1]
HOSPITAL = PROJECT / "config" / "hospital.yaml"
BUSY_DAY = PROJECT / "tests" / "fixtures" / "small_day.yaml"       # the engine tests keep a small fixed day


def make_engine() -> SimulationEngine:
    """All beds free at 08:00, cleaning 45 minutes, holds 30 minutes."""
    return SimulationEngine(hospital=load_hospital(HOSPITAL), clock=to_minutes("08:00"))


def test_layout_matches_config():
    hospital = load_hospital(HOSPITAL)
    assert set(hospital.wards) == {"internal_medicine_a", "internal_medicine_b", "cardiology",
                                   "general_surgery", "intensive_care"}
    ward = hospital.ward("internal_medicine_a")
    assert ward.total(BedType.REGULAR) == 10
    assert ward.total(BedType.OBSERVATION) == 2
    assert ward.bed(11).bed_type == BedType.OBSERVATION
    assert ward.total(BedType.ISOLATION) == 1 and ward.bed(13).bed_type == BedType.ISOLATION
    intensive_care = hospital.ward("intensive_care")
    assert intensive_care.total(BedType.INTENSIVE_CARE) == 6
    assert intensive_care.total(BedType.ISOLATION) == 1 and intensive_care.bed(7).bed_type == BedType.ISOLATION


def test_discharge_then_cleaning_then_free():
    engine = make_engine()
    bed = engine.hospital.ward("cardiology").bed(3)
    bed.status, bed.patient_id = BedStatus.OCCUPIED, "someone"

    engine.schedule(Event(time=to_minutes("10:00"), event_type=EventType.DISCHARGE, ward_id="cardiology", bed_number=3))
    engine.advance_to(to_minutes("10:00"))
    assert bed.status == BedStatus.CLEANING and bed.patient_id is None

    engine.advance_to(to_minutes("10:44"))
    assert bed.status == BedStatus.CLEANING          # 44 minutes: still cleaning
    engine.advance_to(to_minutes("10:45"))
    assert bed.status == BedStatus.FREE              # 45 minutes: clean


def test_hold_expires_automatically():
    engine = make_engine()
    engine.hold_bed("internal_medicine_a", 11, "P-001")
    bed = engine.hospital.ward("internal_medicine_a").bed(11)
    assert bed.status == BedStatus.HELD
    engine.advance_to(to_minutes("08:30"))
    assert bed.status == BedStatus.FREE and bed.held_for is None


def test_assign_after_hold_removes_patient_from_queue():
    engine = make_engine()
    engine.schedule(Event(time=to_minutes("08:00"), event_type=EventType.ADMISSION_DECISION, patient_id="P-001"))
    engine.advance_to(to_minutes("08:00"))
    assert [p.patient_id for p in engine.hospital.emergency_queue] == ["P-001"]
    assert len(engine.triggers) == 1

    engine.hold_bed("internal_medicine_a", 11, "P-001")
    engine.assign_bed("internal_medicine_a", 11, "P-001")
    bed = engine.hospital.ward("internal_medicine_a").bed(11)
    assert bed.status == BedStatus.OCCUPIED and bed.patient_id == "P-001"
    assert engine.hospital.emergency_queue == []

    engine.advance_to(to_minutes("09:00"))           # the old hold-expiry event must not free the bed
    assert bed.status == BedStatus.OCCUPIED


def test_forbidden_transitions_are_rejected():
    engine = make_engine()
    with pytest.raises(InvalidTransition):
        engine.assign_bed("cardiology", 1, "P-001")   # not held -> cannot place

    bed = engine.hospital.ward("cardiology").bed(2)
    bed.status = BedStatus.CLEANING
    with pytest.raises(InvalidTransition):
        engine.hold_bed("cardiology", 2, "P-001")     # cannot hold a bed that is being cleaned


def test_department_status_shows_discharges_today_without_the_hour():
    engine = load_scenario(BUSY_DAY, HOSPITAL)
    status = engine.department_status("internal_medicine_b")
    assert status["observation"]["free"] == 0
    assert {"bed": 11, "bed_type": "observation", "likely_within_hour": True} in status["expected_discharges_today"]
    assert all(set(item) == {"bed", "bed_type", "likely_within_hour"}            # never the hour itself
               for item in status["expected_discharges_today"])


def test_department_status_lists_free_beds_by_number():
    engine = load_scenario(BUSY_DAY, HOSPITAL)
    status = engine.department_status("general_surgery")
    assert status["free_beds"] == [{"bed": 2, "bed_type": "regular"}, {"bed": 9, "bed_type": "regular"},
                                   {"bed": 13, "bed_type": "isolation"}]


def test_department_status_shows_when_a_cleaning_bed_will_be_ready():
    engine = load_scenario(BUSY_DAY, HOSPITAL)
    engine.advance_to(to_minutes("09:40"))                      # general surgery bed 3 discharged at 09:30
    status = engine.department_status("general_surgery")
    assert status["cleaning_beds"] == [{"bed": 3, "bed_type": "regular", "ready_about": "10:15"}]
    engine.advance_to(to_minutes("10:15"))
    assert engine.department_status("general_surgery")["cleaning_beds"] == []


def test_department_status_hides_who_holds_a_bed():
    engine = make_engine()
    engine.hold_bed("cardiology", 4, "P-001")
    status = engine.department_status("cardiology")
    assert status["regular"]["held"] == 1
    assert {"bed": 4, "bed_type": "regular"} not in status["free_beds"]
    assert "P-001" not in str(status) and "08:30" not in str(status)   # not for whom, not until when


def test_busy_day_story():
    engine = load_scenario(BUSY_DAY, HOSPITAL)

    engine.advance_to(to_minutes("16:40"))
    observation_free = sum(w.count(BedType.OBSERVATION, BedStatus.FREE) for w in engine.hospital.wards.values())
    assert observation_free == 1                     # two patients need observation, only one bed is free
    assert [p.patient_id for p in engine.hospital.emergency_queue] == ["P-001", "P-002", "P-003"]

    engine.advance_to(to_minutes("17:50"))
    isolation_free = [(w.ward_id, b.number) for w in engine.hospital.wards.values()
                      for b in w.beds if b.bed_type == BedType.ISOLATION and b.status == BedStatus.FREE]
    assert isolation_free == [("general_surgery", 13)]   # the only free isolation room is far on the ladder

    engine.run_to_end()
    assert len(engine.triggers) == 5
    assert engine.hospital.ward("internal_medicine_b").bed(12).status == BedStatus.FREE
