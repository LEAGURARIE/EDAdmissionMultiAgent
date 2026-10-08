"""Every expected answer in the evaluation — both pairs files and the paired cases — keeps the hard rules,
asks for the right flags, and names a bed that is really free in the true ward state (the template after its notes)."""
import pytest
import yaml

from ed_admission import config
from ed_admission.environment import BedStatus
from ed_admission.evaluation import answer_key_builder as builder

INPUTS = builder._read("clinical_inputs.yaml")["cases"]
PAIRS = builder._read("pairs.yaml")["pairs"] + builder._read("pairs_set2.yaml")["pairs"]
SINGLE = [yaml.safe_load(builder.answer_path(p).read_text(encoding="utf8")) for p in PAIRS]
PAIRED = [a for c in builder._read("paired_cases.yaml")["cases"]
          for a in yaml.safe_load(builder.paired_path(c).read_text(encoding="utf8"))["answers"]]
FLAGGED_SITUATIONS = {"limit_on_treatment", "confusion_not_new"}


@pytest.mark.parametrize("answer", SINGLE + PAIRED, ids=lambda a: f"{a['case_id']}__{a['template']}")
def test_the_hard_rules_hold(answer):
    needs, expected = answer["needs"], answer["expected"]
    if not needs["severe"]:
        assert expected["bed_type"] != "observation"
    else:
        assert expected["action"] != "wait"
    if needs["isolation"]:
        assert expected["bed_type"] in (None, "isolation")
    if needs["observation"] and not needs["isolation"]:
        assert expected["bed_type"] != "regular"
    assert (expected["bed_type"] == "intensive_care") <= needs["intensive_care"]
    level = expected["ladder_level"]
    assert expected["needs_typed_reason"] == (level is not None and level >= 3)


@pytest.mark.parametrize("answer", SINGLE + PAIRED, ids=lambda a: f"{a['case_id']}__{a['template']}")
def test_the_required_flags(answer):
    inputs = INPUTS[answer["case_id"]]
    flags = [f for t, f in ((9, "prompt_injection"), (10, "missing_vital_sign")) if t in inputs["traps"]]
    flags += ["severity_judgment_note"] if inputs.get("situation") in FLAGGED_SITUATIONS else []
    assert answer["expected"]["required_flags"] == flags


@pytest.mark.parametrize("answer", SINGLE, ids=lambda a: f"{a['case_id']}__{a['template']}")
def test_the_bed_is_free_in_the_true_ward_state(answer):
    expected = answer["expected"]
    if expected["action"] not in ("place", "temporary_place"):
        return
    engine = builder.true_ward_state(answer)
    for ward_id in expected["acceptable_wards"]:
        assert [b for b in engine.hospital.ward(ward_id).beds
                if b.bed_type.value == expected["bed_type"] and b.status == BedStatus.FREE], ward_id
        if expected["action"] == "temporary_place":
            assert config.ladder_level(answer["target_ward"], ward_id) == expected["ladder_level"]
