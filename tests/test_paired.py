"""Paired cases (data/answer_key/paired_cases.yaml): two patients wait at the same moment for one suitable bed.
The answers follow the approved rule, every case needs the wards' private notes, and the runner runs them."""
import yaml

from simple_deciders import first_free_bed
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation import load_paired, run_paired

CASES = load_paired()
ANSWERS = [yaml.safe_load(builder.paired_path(c).read_text(encoding="utf8")) for c in CASES]
INPUTS = builder._read("clinical_inputs.yaml")["cases"]


def test_seven_paired_cases_of_two_patients():
    assert len(CASES) == 7 and all(len(c["patients"]) == 2 for c in CASES)


def test_the_priority_follows_the_rule():
    """Needs the bed type first; then severe first; then the earlier admission decision."""
    for case, answer in zip(CASES, ANSWERS):
        key = lambda p: builder.priority_key(case, p, INPUTS)
        assert answer["priority"] == [p["case_id"] for p in sorted(case["patients"], key=key)], case["id"]


def test_the_isolation_room_goes_to_the_patient_who_needs_it():
    """PC-7: the severe observation patient, for whom the isolation room is only a last resort, comes second."""
    answer = next(a for a in ANSWERS if a["id"] == "PC-7")
    first = next(a for a in answer["answers"] if a["case_id"] == answer["priority"][0])
    assert first["needs"]["isolation"] and not first["needs"]["severe"]
    assert first["expected"]["bed_type"] == "isolation"


def test_one_patient_loses_the_bed():
    """The second in priority does not get what it would get alone: the two really compete."""
    for case, answer in zip(CASES, ANSWERS):
        second = next(a for a in answer["answers"] if a["case_id"] == answer["priority"][1])
        alone = builder.build_one({"case_id": second["case_id"], "template": case["template"],
                                   "target_ward": second["target_ward"]}, INPUTS[second["case_id"]])
        assert (second["expected"]["action"], second["expected"]["ladder_level"]) != \
               (alone["expected"]["action"], alone["expected"]["ladder_level"]), case["id"]


def test_the_reports_alone_are_not_enough():
    for answer in ANSWERS:
        assert any(builder._describe_expected(a["expected"]) != a["reports_alone_would_give"]
                   for a in answer["answers"]), answer["id"]


def test_the_runner_runs_the_paired_cases():
    results = run_paired(first_free_bed)
    assert [r.case_id for r in results] == [c["id"] for c in CASES]
    assert not any(r.error for r in results)
    assert all(len(r.decisions) == 2 for r in results)
