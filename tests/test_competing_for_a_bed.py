"""Day 4 — competing for a bed (data/scenarios/competing_for_a_bed.yaml): three designed moments when two patients
need one bed, scored at the moment by the approved rule (metrics.contention_moments, data/answer_key/
contention_moments.yaml). The decider gets ids and times only; who should get the bed is computed by the evaluation."""
from functools import partial
from pathlib import Path

from simple_deciders import knows_the_answers

from ed_admission.baselines import code_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import metrics

DAY = "competing_for_a_bed"
PATH = Path(__file__).resolve().parents[1] / "data" / "scenarios" / f"{DAY}.yaml"


def run(policy):
    engine = load_scenario(PATH)
    engine.policy = policy
    engine.run_to_end()
    return engine, metrics.contention_moments(DAY, engine)


def test_a_decider_that_follows_the_rule_reaches_every_moment_and_is_right():
    _, moments = run(knows_the_answers)
    assert [m["as_designed"] for m in moments] == [True, True, True]
    assert [(m["should_get"], m["got"]) for m in moments] == [("P-046", "P-046"), ("P-043", "P-043"),
                                                               ("P-057", "P-057")]


def test_first_come_first_served_is_wrong_where_the_rule_says_otherwise():
    _, moments = run(partial(knows_the_answers, order="arrival"))
    assert [m["right"] for m in moments] == [True, False, False]      # same state: time order is the rule


def test_each_moment_is_two_patients_together_in_one_case():
    engine, _ = run(knows_the_answers)
    together = [c["patients"] for c in engine.cases if len(c["patients"]) == 2]
    assert together == [["P-046", "P-058"], ["P-030", "P-043"], ["P-050", "P-057"]]


def test_a_moment_not_reached_as_designed_is_reported_not_scored():
    _, moments = run(code_coordinator)            # it does not read isolation in the free text
    for m in moments:
        assert m["as_designed"] == (m["why_not"] is None)
        assert ("right" in m) == m["as_designed"]


def test_the_day_is_scored_apart_from_the_three_days():
    engine, _ = run(knows_the_answers)
    assert metrics.score_day(engine, DAY)["moments"] and metrics.score_day(engine)["moments"] == []
