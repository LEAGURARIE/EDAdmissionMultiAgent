"""The development and test sets (evaluation/sets.py, data/answer_key/dev_test_sets.yaml): frozen before any paid
run, the same as the code that builds them, one pair from each template in each, no patient in both, every kind in
both — and the scripts run exactly them."""
import pytest
import yaml

from ed_admission.evaluation import run_cases, sets
from ed_admission.evaluation.case_runner import load_paired


@pytest.fixture(scope="module")
def frozen():
    return sets.load()


def test_the_frozen_file_is_what_the_code_builds(frozen):
    assert frozen == sets.build()
    assert yaml.safe_load(sets.SETS_FILE.read_text(encoding="utf8"))["frozen"] == "2026-10-15"


def test_one_pair_from_each_template_in_each_set(frozen):
    templates = sorted({p["template"] for p in sets.all_pairs()})
    for name in ("dev", "test"):
        assert sorted(p["template"] for p in frozen[name]) == templates


def test_no_patient_is_in_both_sets_or_twice_in_one(frozen):
    dev, test = ([p["case_id"] for p in frozen[name]] for name in ("dev", "test"))
    assert len(set(dev)) == len(dev) and len(set(test)) == len(test)
    assert not set(dev) & set(test)


def test_no_patient_of_a_paired_case_is_in_the_development_set(frozen):
    paired = {p["case_id"] for case in load_paired() for p in case["patients"]}
    assert not {p["case_id"] for p in frozen["dev"]} & paired


def test_every_kind_is_in_both_sets(frozen):
    pairs = {(p["case_id"], p["template"]): p for p in sets.all_pairs()}
    every = set().union(*(sets.kinds(p) for p in pairs.values()))
    for name in ("dev", "test"):
        covered = set().union(*(sets.kinds(pairs[(p["case_id"], p["template"])]) for p in frozen[name]))
        assert covered == every, (name, sorted(every - covered))


def test_every_chosen_pair_exists(frozen):
    pairs = {(p["case_id"], p["template"]) for p in sets.all_pairs()}
    assert all((p["case_id"], p["template"]) in pairs for name in ("dev", "test") for p in frozen[name])


def test_the_runner_runs_exactly_the_set():
    seen = []

    def records(engine, event, waiting):
        seen.append(waiting[0].patient_id)
        return []

    only = sets.pairs_of("dev")
    results = run_cases(records, evaluation_set="first", only=only) + run_cases(records, evaluation_set="second",
                                                                                only=only)
    assert {(r.case_id, r.template) for r in results} == only
    assert sets.pairs_of("all") is None
