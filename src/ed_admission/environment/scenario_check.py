"""Checks that a scripted day does not contradict itself — before any decider runs it.

A day is a script: the beds free at the start, then discharges, admission decisions and ward notes in time order.
The script must agree with the hospital it runs in, whatever the decider does:
  - every ward and bed it names exists;
  - a discharge frees a bed an inpatient occupies then: never a bed free at the start, never the same bed twice
    (after its discharge a bed may hold a patient the decider placed — a script must never discharge them);
  - a bed freed without updating the system (in_report: false) is told by a note of that ward, at or after the
    discharge — otherwise nobody could ever know it is free;
  - a note that names a bed names a bed of its ward; a note that ends was started, in the same ward, before;
  - every admitted patient has a record, and is admitted once.
check_scenario returns the problems found, in words; an empty list means the day is consistent.
tests/test_scenario_check.py runs it on every day in data/scenarios/.

Owner: Lea Gur-Arie
"""
import re
from pathlib import Path

import yaml

from ed_admission import config
from ed_admission.environment.events import to_minutes

BED_IN_TEXT = re.compile(r"(?:מיטה|מיטת השגחה|מיטת בידוד)\s*(\d+)")     # "bed 4", as the notes write it in Hebrew


def check_scenario(path: Path) -> list[str]:
    day = yaml.safe_load(Path(path).read_text(encoding="utf8"))
    wards = config.hospital()["wards"]
    records = {p.stem for p in (config.PROJECT / "data" / "records").glob("P-*.yaml")}
    problems = []

    def bed_exists(ward: str, bed: int) -> bool:
        return ward in wards and 1 <= bed <= sum(wards[ward]["beds"].values())

    free = set()
    for ward, beds in day.get("free_at_start", {}).items():
        for bed in beds:
            if not bed_exists(ward, bed):
                problems.append(f"free_at_start: {ward} bed {bed} does not exist")
            free.add((ward, bed))

    discharged, admitted, notes_open = {}, set(), set()
    events = sorted(day["events"], key=lambda e: to_minutes(e["time"]))
    for event in events:
        kind, ward, when = event["type"], event.get("ward"), event["time"]
        if ward is not None and ward not in wards:
            problems.append(f"{when} {kind}: no ward {ward}")
            continue
        if kind == "discharge":
            bed = (ward, event["bed"])
            if not bed_exists(*bed):
                problems.append(f"{when} discharge: {ward} bed {event['bed']} does not exist")
            elif bed in free:
                problems.append(f"{when} discharge: {ward} bed {event['bed']} was free at the start — no inpatient to discharge")
            elif bed in discharged:
                problems.append(f"{when} discharge: {ward} bed {event['bed']} was already discharged at {discharged[bed]} — "
                                f"it may hold a patient the decider placed")
            discharged[bed] = when
            if event.get("in_report") is False:
                told = [n for n in events if n["type"] == "ward_note" and n.get("ward") == ward
                        and to_minutes(n["time"]) >= to_minutes(when)]
                if not told:
                    problems.append(f"{when} discharge: {ward} bed {event['bed']} is freed without updating the system, "
                                    f"and no note of {ward} tells it")
        elif kind == "ward_note":
            notes_open.add((ward, event["text"]))
            for number in BED_IN_TEXT.findall(event["text"]):
                if not bed_exists(ward, int(number)):
                    problems.append(f"{when} ward_note: {ward} has no bed {number}, which the note names")
        elif kind == "ward_note_end":
            if (ward, event["text"]) not in notes_open:
                problems.append(f"{when} ward_note_end: {ward} ends a note that was never started")
            notes_open.discard((ward, event["text"]))
        elif kind == "admission_decision":
            patient = event["patient"]
            if patient not in records:
                problems.append(f"{when} admission: no record {patient}")
            if patient in admitted:
                problems.append(f"{when} admission: {patient} is admitted twice")
            admitted.add(patient)
    return problems
