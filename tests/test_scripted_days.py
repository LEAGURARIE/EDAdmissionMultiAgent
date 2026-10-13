"""The three scripted days (data/scenarios/): they load, run to the end with a decider, keep their ward notes
private and timed, hide a bed the ward has not updated, and move to the next day after midnight."""
from pathlib import Path

import pytest

from simple_deciders import first_free_bed
from ed_admission.contracts.patient_record import load_record
from ed_admission.environment import BedStatus, load_scenario
from ed_admission.environment.events import to_minutes

PROJECT = Path(__file__).resolve().parents[1]
DAYS = {name: PROJECT / "data" / "scenarios" / f"{name}.yaml" for name in ("busy_day", "evening_night", "weekend")}


@pytest.mark.parametrize("name", DAYS)
def test_the_day_runs_to_the_end(name):
    engine = load_scenario(DAYS[name])
    engine.policy = first_free_bed
    engine.run_to_end()
    beds = [(p.ward_id, p.bed_number) for p in engine.placements]
    assert len(beds) == len(set(beds))                         # no bed given twice
    for trigger in engine.triggers:
        load_record(PROJECT / "data" / "records" / f"{trigger.patient_id}.yaml")   # every patient has a record


def test_patient_counts():
    counts = {}
    for name, path in DAYS.items():
        engine = load_scenario(path)
        engine.run_to_end()                                     # nobody decides: every patient only joins the queue
        counts[name] = len(engine.triggers)
    assert counts == {"busy_day": 15, "evening_night": 12, "weekend": 12}


def test_a_note_starts_and_ends_and_stays_out_of_the_report():
    engine = load_scenario(DAYS["busy_day"])
    engine.advance_to(to_minutes("14:05"))
    [note] = engine.ward_notes("cardiology")
    assert note not in str(engine.department_status("cardiology"))
    engine.advance_to(to_minutes("20:00"))
    assert engine.ward_notes("cardiology") == []


def test_a_bed_not_updated_is_free_but_not_in_the_report():
    engine = load_scenario(DAYS["evening_night"])
    engine.advance_to(to_minutes("20:50"))
    bed = engine.hospital.ward("general_surgery").bed(4)
    assert bed.status == BedStatus.FREE and not bed.in_report
    report = engine.department_status("general_surgery")
    assert {"bed": 4, "bed_type": "regular"} not in report["free_beds"]
    assert engine.ward_notes("general_surgery")                 # only the ward's note tells


def test_the_day_moves_on_after_midnight():
    engine = load_scenario(DAYS["weekend"])
    assert engine.department_status("cardiology")["day"] == "Friday"
    engine.advance_to(to_minutes("01:00 +1d"))
    assert engine.department_status("cardiology")["day"] == "Saturday"
