"""Every scripted day agrees with itself and with the hospital (environment/scenario_check.py) — and the check
really finds the contradictions it is meant to find."""
from pathlib import Path

import pytest

from ed_admission.environment.scenario_check import check_scenario

PROJECT = Path(__file__).resolve().parents[1]
DAYS = sorted((PROJECT / "data" / "scenarios").glob("*.yaml")) + [PROJECT / "tests" / "fixtures" / "small_day.yaml"]


@pytest.mark.parametrize("day", DAYS, ids=lambda p: p.stem)
def test_every_day_is_consistent(day):
    assert check_scenario(day) == []


def write(tmp_path, events: str, free: str = "internal_medicine_a: [2]") -> Path:
    day = tmp_path / "day.yaml"
    day.write_text(f'name: t\nday: Monday\nstart_time: "08:00"\nfree_at_start:\n  {free}\nevents:\n{events}',
                   encoding="utf8")
    return day


@pytest.mark.parametrize("events, words", [
    ('  - {time: "09:00", type: discharge, ward: internal_medicine_a, bed: 2}\n', "free at the start"),
    ('  - {time: "09:00", type: discharge, ward: cardiology, bed: 4}\n'
     '  - {time: "11:00", type: discharge, ward: cardiology, bed: 4}\n', "already discharged"),
    ('  - {time: "09:00", type: discharge, ward: cardiology, bed: 40}\n', "does not exist"),
    ('  - {time: "09:00", type: discharge, ward: cardiology, bed: 4, in_report: false}\n', "no note of cardiology"),
    ('  - {time: "09:00", type: ward_note, ward: cardiology, text: "מיטה 25 פנויה"}\n', "no bed 25"),
    ('  - {time: "09:00", type: ward_note_end, ward: cardiology, text: "x"}\n', "never started"),
    ('  - {time: "09:00", type: admission_decision, patient: P-999}\n', "no record"),
    ('  - {time: "09:00", type: admission_decision, patient: P-001}\n'
     '  - {time: "10:00", type: admission_decision, patient: P-001}\n', "admitted twice"),
    ('  - {time: "09:00", type: discharge, ward: oncology, bed: 1}\n', "no ward oncology"),
])
def test_the_check_finds_each_contradiction(tmp_path, events, words):
    assert any(words in problem for problem in check_scenario(write(tmp_path, events)))


def test_a_bed_freed_without_update_and_told_by_a_note_is_fine(tmp_path):
    events = ('  - {time: "09:00", type: discharge, ward: cardiology, bed: 4, in_report: false}\n'
              '  - {time: "09:20", type: ward_note, ward: cardiology, text: "מיטה 4 התפנתה, עוד לא עודכנה"}\n')
    assert check_scenario(write(tmp_path, events)) == []
