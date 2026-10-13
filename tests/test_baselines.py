"""rank_placements, the baseline — the Code Coordinator (today's procedure: reads the structured fields, phones the
wards and gets their true answer) — and a test-only decider that never phones (tests/simple_deciders.py)."""
from pathlib import Path

import yaml

from ed_admission.baselines import code_coordinator
from simple_deciders import never_asks
from ed_admission.baselines.reading import read_record
from ed_admission.environment import load_scenario, load_template
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation import metrics, run_cases, run_paired
from ed_admission.tools.rank_placements import PatientNeeds, rank_placements

PROJECT = Path(__file__).resolve().parents[1]
TEMPLATES = PROJECT / "data" / "templates"
BED_FOR_TYPE = {"A": "regular", "B": "observation", "C": "isolation", "D": "isolation", "E": "intensive_care"}


def reports(template: str, target: str) -> dict:
    engine = load_template(TEMPLATES / f"{template}.yaml", target_ward=target)
    return {ward: engine.department_status(ward) for ward in engine.hospital.wards}


def test_a_non_severe_patient_waits_for_the_bed_in_cleaning():
    best = rank_placements(reports("T5", "cardiology"), PatientNeeds("cardiology", "regular", severe=False)).best
    assert (best.action, best.wait_because) == ("wait", "cleaning")


def test_a_severe_patient_never_waits():
    ranking = rank_placements(reports("T5", "cardiology"), PatientNeeds("cardiology", "observation", severe=True))
    assert all(option.action != "wait" for option in ranking.options)
    assert (ranking.best.action, ranking.best.ladder_level) == ("temporary_place", 2)


def test_an_observation_patient_gets_the_isolation_room_only_as_a_last_resort():
    best = rank_placements(reports("T3", "cardiology"), PatientNeeds("cardiology", "observation", severe=True)).best
    assert (best.action, best.ward_id, best.bed_type) == ("place", "cardiology", "isolation")


def test_intensive_care_full_means_escalate():
    best = rank_placements(reports("T11", "intensive_care"),
                           PatientNeeds("intensive_care", "intensive_care", severe=True)).best
    assert best.action == "escalate"


def test_a_taken_bed_is_not_offered_again():
    report = reports("T1", "internal_medicine_a")
    first = rank_placements(report, PatientNeeds("cardiology", "regular", severe=False)).best
    second = rank_placements(report, PatientNeeds("cardiology", "regular", severe=False),
                             taken={(first.ward_id, first.bed_number)}).best
    assert (second.ward_id, second.bed_number) != (first.ward_id, first.bed_number)


def test_both_baselines_run_on_the_whole_evaluation():
    for decider in (never_asks, code_coordinator):
        results = run_cases(decider) + run_cases(decider, evaluation_set="second")
        assert len(results) == 104 and not any(r.error for r in results)
        assert not any(r.error for r in run_paired(decider))
        for day in ("busy_day", "evening_night", "weekend"):
            engine = load_scenario(PROJECT / "data" / "scenarios" / f"{day}.yaml")
            engine.policy = decider
            engine.run_to_end()


def test_the_code_coordinator_is_right_whenever_it_read_the_patient_right():
    """Its phone calls give it the wards' true answer: every miss comes from reading the structured fields only."""
    results = run_cases(code_coordinator) + run_cases(code_coordinator, evaluation_set="second")
    for result, scored in zip(results, metrics.score_cases(results)):
        answer = yaml.safe_load(builder.answer_path({"case_id": result.case_id, "template": result.template})
                                .read_text(encoding="utf8"))
        reading = read_record(result.case_id, reports(result.template, answer["target_ward"]))
        if (reading.needs.bed_type, reading.needs.target_ward) == (BED_FOR_TYPE[answer["patient_type"]],
                                                                  answer["target_ward"]):
            assert scored.match, (result.case_id, result.template, scored.decided, scored.expected)
        assert not scored.bed_not_available                        # it asked: never a bed the ward can't give


def test_phone_calls_take_time_in_a_scripted_day():
    engine = load_scenario(PROJECT / "data" / "scenarios" / "busy_day.yaml")
    engine.policy = code_coordinator
    engine.run_to_end()
    assert engine.placements and all(p.wait_minutes >= 5 + 15 for p in engine.placements)   # a call + approval
