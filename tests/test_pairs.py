"""The 60 evaluation pairs (data/answer_key/pairs.yaml) match the plan and can be built."""
from collections import Counter
from pathlib import Path

import pytest
import yaml

from ed_admission import config
from ed_admission.environment import load_template

PROJECT = Path(__file__).resolve().parents[1]
PAIRS = yaml.safe_load((PROJECT / "data" / "answer_key" / "pairs.yaml").read_text(encoding="utf8"))["pairs"]
TEMPLATES = PROJECT / "data" / "templates"
HOSPITAL = PROJECT / "config" / "hospital.yaml"
RECORDS = PROJECT / "data" / "records"


def test_sixty_pairs_one_per_record():
    case_ids = [p["case_id"] for p in PAIRS]
    assert len(case_ids) == 60 and len(set(case_ids)) == 60
    assert case_ids == [f"P-{n:03d}" for n in range(1, 61)]


def test_five_pairs_per_template():
    assert Counter(p["template"] for p in PAIRS) == {f"T{n}": 5 for n in range(1, 13)}


def test_target_wards_follow_the_plan():
    assert all(p["target_ward"] in config.ladders() for p in PAIRS)
    assert {p["target_ward"] for p in PAIRS if p["template"] == "T7"} <= {"cardiology", "general_surgery"}
    assert {p["target_ward"] for p in PAIRS if p["template"] == "T9"} <= {"internal_medicine_a", "internal_medicine_b"}


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: f"{p['case_id']}-{p['template']}")
def test_every_pair_builds_a_hospital(pair):
    engine = load_template(TEMPLATES / f"{pair['template']}.yaml", HOSPITAL, target_ward=pair["target_ward"])
    assert engine.hospital.emergency_queue == []


def test_every_written_record_has_a_pair():
    """In either pairs file — the added records (P-061 on) are paired in pairs_set2.yaml."""
    second = yaml.safe_load((PROJECT / "data" / "answer_key" / "pairs_set2.yaml").read_text(encoding="utf8"))["pairs"]
    written = {path.stem for path in RECORDS.glob("P-*.yaml")}
    assert written <= {p["case_id"] for p in PAIRS + second}
