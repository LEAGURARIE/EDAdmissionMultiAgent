"""The configuration files agree with each other and are actually used by the code.

config/hospital.yaml    what exists: wards and beds, and how long cleaning, holds and approvals take
config/clinical_rules/  what is right to do: ladders, complaint -> ward, NEWS2 levels, placement rules
"""
from pathlib import Path

import yaml

from ed_admission.environment import load_hospital, to_minutes
from ed_admission.environment.simulation_engine import SimulationEngine
from ed_admission.tools import news2

PROJECT = Path(__file__).resolve().parents[1]
HOSPITAL_FILE = PROJECT / "config" / "hospital.yaml"
RULES = PROJECT / "config" / "clinical_rules"


def read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf8"))


WARDS = set(read(HOSPITAL_FILE)["wards"])


def test_every_config_file_parses():
    for path in [HOSPITAL_FILE, *RULES.glob("*.yaml")]:
        assert read(path), path.name


def test_chief_complaints_point_to_existing_wards():
    for complaint, wards in read(RULES / "chief_complaints.yaml")["complaints"].items():
        assert wards and set(wards) <= WARDS, complaint


def test_every_ward_has_a_ladder_of_existing_wards_starting_with_itself():
    ladders = read(RULES / "proximity_ladder.yaml")["ladders"]
    assert set(ladders) == WARDS
    for ward_id, levels in ladders.items():
        assert levels[0] == [ward_id]
        assert {w for level in levels for w in level} <= WARDS


def test_engine_reads_cleaning_and_hold_times_from_config():
    times = read(HOSPITAL_FILE)["operations"]
    engine = SimulationEngine(hospital=load_hospital(HOSPITAL_FILE), clock=to_minutes("08:00"))
    assert (engine.cleaning_minutes, engine.hold_minutes) == (times["cleaning_minutes"], times["hold_minutes"])
    assert times["approval_minutes"] < times["hold_minutes"]      # a hold must outlast the approval


def test_news2_reads_its_thresholds_from_config():
    assert news2.thresholds() == read(RULES / "news2_levels.yaml")


def test_placement_rules_match_the_decisions():
    rules = read(RULES / "placement_rules.yaml")
    assert rules["waiting"] == {"non_severe_max_minutes": 60, "severe_max_minutes": 0,
                                "discharge_in_report_only_in": "weekday_morning_shift"}
    assert rules["shifts"] == {"morning": "07:00", "evening": "15:00", "night": "23:00"}
    assert rules["approval"] == {"approver": "shift_manager", "typed_reason_from_ladder_level": 3}
    assert rules["beds"]["non_severe_in_observation_bed"] is False
    assert rules["intensive_care"]["when_full"] == "escalate"
    assert rules["competing_for_one_bed"] == ["needs_the_bed_type_first", "severe_first", "earlier_decision_first"]
    assert rules["negotiation"] == {"max_requests_per_ward": 3, "max_requests_per_case": 8,
                                    "when_limit_reached": "escalate"}


def test_structured_signs_point_to_observation_conditions():
    """Every structured sign is an observation condition; together with the unreadable ones and the NEWS2
    conditions (4: oxygen via 5, 15-16: NEWS2) they cover the whole list."""
    signs = read(RULES / "structured_signs.yaml")
    conditions = {c["id"] for c in read(RULES / "observation_conditions.yaml")["conditions"]}
    used = {s["condition"] for s in signs["observation_signs"]}
    assert used <= conditions and set(signs["not_readable"]) <= conditions
    assert used | set(signs["not_readable"]) | {4, 15, 16} == conditions
    assert {"condition": 7, "glucose_at_least": 200, "ph_below": 7.30} in signs["observation_signs"]
