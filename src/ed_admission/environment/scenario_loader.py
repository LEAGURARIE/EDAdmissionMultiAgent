"""Builds the hospital (config/hospital.yaml) and loads a scripted day (data/scenarios/*.yaml) into a
ready-to-run simulation engine.

Owner: integrator
"""
from pathlib import Path

import yaml

from ed_admission import config
from ed_admission.environment.events import Event, EventType, to_minutes
from ed_admission.environment.hospital import Bed, BedStatus, BedType, Hospital, Ward, WardLevel
from ed_admission.environment.simulation_engine import SimulationEngine


def load_hospital(hospital_path: Path | None = None) -> Hospital:
    """Build every ward, all beds free. Beds are numbered in order: regular, observation, isolation room."""
    layout = yaml.safe_load(Path(hospital_path).read_text(encoding="utf8")) if hospital_path else config.hospital()
    wards = {}
    for ward_id, spec in layout["wards"].items():
        beds, number = [], 1
        for bed_type_name, count in spec["beds"].items():
            for _ in range(count):
                beds.append(Bed(number=number, bed_type=BedType(bed_type_name)))
                number += 1
        wards[ward_id] = Ward(ward_id=ward_id, name=spec["name"], level=WardLevel(spec["level"]), beds=beds)
    return Hospital(wards=wards)


def load_scenario(scenario_path: Path, hospital_path: Path | None = None) -> SimulationEngine:
    """A scripted day: the starting beds, then a timed list of discharges, admission decisions and ward notes.
    A discharge with in_report: false frees the bed without updating the system: the report keeps showing it
    occupied, and the ward does not announce it in advance. Times past midnight are written like 01:45 +1d."""
    scenario = yaml.safe_load(Path(scenario_path).read_text(encoding="utf8"))
    hospital = load_hospital(hospital_path)

    # Starting state: every bed occupied by an existing inpatient, except the beds listed as free.
    free_at_start = scenario.get("free_at_start", {})
    for ward_id, ward in hospital.wards.items():
        free_numbers = set(free_at_start.get(ward_id, []))
        for bed in ward.beds:
            if bed.number in free_numbers:
                bed.status = BedStatus.FREE
            else:
                bed.status = BedStatus.OCCUPIED
                bed.patient_id = f"inpatient-{ward_id}-{bed.number}"

    engine = SimulationEngine(
        hospital=hospital,
        clock=to_minutes(scenario["start_time"]),
        day=scenario.get("day"),
    )

    for item in scenario["events"]:
        event = Event(
            time=to_minutes(item["time"]),
            event_type=EventType(item["type"]),
            ward_id=item.get("ward"),
            bed_number=item.get("bed"),
            patient_id=item.get("patient"),
            text=item.get("text"),
            in_report=item.get("in_report", True),
            effects=item.get("effects", []),
        )
        engine.schedule(event)
        # The ward knows its planned discharges in advance; the actual time may be later.
        if event.event_type == EventType.DISCHARGE and event.in_report:
            expected = item.get("expected_time", item["time"])
            hospital.ward(event.ward_id).bed(event.bed_number).expected_discharge_at = to_minutes(expected)

    return engine
