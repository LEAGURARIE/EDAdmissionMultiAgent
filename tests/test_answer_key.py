"""The expected answers (data/answer_key/) are complete, consistent with the rules, and possible in their template."""
from collections import Counter
from pathlib import Path

import pytest
import yaml

from ed_admission import config
from ed_admission.environment import BedStatus, BedType
from ed_admission.evaluation.answer_key_builder import true_ward_state

PROJECT = Path(__file__).resolve().parents[1]
ANSWER_KEY = PROJECT / "data" / "answer_key"
TEMPLATES = PROJECT / "data" / "templates"
PAIRS = yaml.safe_load((ANSWER_KEY / "pairs.yaml").read_text(encoding="utf8"))["pairs"]
ANSWERS = [yaml.safe_load((ANSWER_KEY / f"{p['case_id']}__{p['template']}.yaml").read_text(encoding="utf8"))
           for p in PAIRS]


def test_one_answer_per_pair():
    assert [(a["case_id"], a["template"]) for a in ANSWERS] == [(p["case_id"], p["template"]) for p in PAIRS]


def test_the_mix_matches_the_plan():
    assert Counter(a["patient_type"] for a in ANSWERS) == {"A": 21, "B": 19, "C": 7, "D": 8, "E": 5}
    assert Counter(a["wording"] for a in ANSWERS) == {"admit_only": 20, "specialty": 20, "binding_request": 20}
    for template in {a["template"] for a in ANSWERS}:
        assert sorted(a["challenge"] for a in ANSWERS if a["template"] == template) == [1, 2, 3, 4, 5]


def test_required_flags_follow_the_traps():
    for a in ANSWERS:
        expected = [f for t, f in ((9, "prompt_injection"), (10, "missing_vital_sign")) if t in a["traps"]]
        assert a["expected"]["required_flags"] == expected, a["case_id"]


def test_a_non_severe_patient_never_gets_an_observation_bed():
    for a in ANSWERS:
        if not a["needs"]["severe"]:
            assert a["expected"]["bed_type"] != BedType.OBSERVATION.value, a["case_id"]


@pytest.mark.parametrize("answer", ANSWERS, ids=lambda a: a["case_id"])
def test_the_expected_bed_is_really_free_in_the_template(answer):
    """Free in the true ward state — the template after its private ward notes."""
    expected = answer["expected"]
    if expected["action"] not in ("place", "temporary_place"):
        return
    engine = true_ward_state(answer)
    for ward_id in expected["acceptable_wards"]:
        free = [b for b in engine.hospital.ward(ward_id).beds
                if b.bed_type.value == expected["bed_type"] and b.status == BedStatus.FREE]
        assert free, f"{answer['case_id']}: no free {expected['bed_type']} bed in {ward_id}"
    if expected["action"] == "temporary_place":
        level = config.ladder_level(answer["target_ward"], expected["ward_id"])
        assert level == expected["ladder_level"] and level > 1
        assert expected["needs_typed_reason"] == (level >= 3)


def test_every_answer_is_approved_and_frozen():
    """The whole evaluation: both pairs files and the paired cases."""
    every = sorted(ANSWER_KEY.glob("P-*__T*.yaml")) + sorted(ANSWER_KEY.glob("PC-*.yaml"))
    answers = [yaml.safe_load(path.read_text(encoding="utf8")) for path in every]
    assert len(answers) == 60 + 44 + 7
    assert all(a["frozen"] and a["approved_by"] for a in answers)
