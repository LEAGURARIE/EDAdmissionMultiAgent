"""The comparison report (evaluation/comparison_report.py): the latest 3 test runs of each system, the same cases or
no report, mean and range, a difference only when the ranges do not overlap, the protocol's reading, the stability
of the decisions, the days — and the run history keeping every repetition. No model is called."""
import csv

import pytest

from ed_admission.database import run_log
from ed_admission.evaluation import comparison_report as report

DECIDER = {"single_agent": "single_agent", "multi_agent": "coordinator"}
SAME = {"violations": 0, "need_met_rate": 1.0, "bed_not_available": 0, "match_rate": 0.8, "mean_seconds": 30,
        "cost_mean_usd": 0.15}


@pytest.fixture(autouse=True)
def outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("ED_OUTPUTS", str(tmp_path))
    return tmp_path


def log(system, run_id, numbers, decisions=None, label=None, set_name="test", partial=False):
    run = {"run_id": run_id, "time": "", "kind": "system", "decider": DECIDER[system], "agent": "", "version": "v",
           "label": label if label is not None else run_id}
    run_log.log_run(run, {"set": set_name, "system": system, "partial_run": partial,
                          "baseline match_rate": 0.38, **numbers})
    decisions = decisions or {"P-001": ("place", "cardiology"), "P-002": ("escalate", "")}
    run_log.log_admissions(run, [{"source": "pairs:first", "case": "T1", "patient_id": pid, "action": action,
                                  "ward_id": ward} for pid, (action, ward) in decisions.items()])


def three(system, start, **per_run):
    """Three repetitions: each keyword a list of three values, the rest as in SAME."""
    for i in range(3):
        log(system, f"2026101{start + i}-1000-{system}", {**SAME, **{k: v[i] for k, v in per_run.items()}})


def row(built, key):
    return next(r for r in built["rows"] if r["key"] == key)


# ---------- the reading ----------
def test_cheaper_beyond_the_spread_and_otherwise_the_same():
    three("single_agent", 1, cost_mean_usd=[0.14, 0.15, 0.16])
    three("multi_agent", 5, cost_mean_usd=[0.20, 0.22, 0.21])
    built = report.build()
    assert row(built, "cost_mean_usd")["winner"] == "single_agent"
    assert row(built, "match_rate")["winner"] == ""                       # the same: a tie
    assert built["reading"] == "As safe and as suitable; single_agent is cheaper."


def test_overlapping_ranges_are_a_tie():
    three("single_agent", 1, match_rate=[0.70, 0.80, 0.75])
    three("multi_agent", 5, match_rate=[0.78, 0.85, 0.82])                 # 0.80 > 0.78: they overlap
    built = report.build()
    assert row(built, "match_rate")["winner"] == ""
    assert built["reading"].startswith("A tie")


def test_safety_comes_first():
    three("single_agent", 1, violations=[0, 0, 0], match_rate=[0.5, 0.5, 0.5])
    three("multi_agent", 5, violations=[1, 2, 1], match_rate=[0.9, 0.9, 0.9])
    assert report.build()["reading"] == "single_agent is safer."


def test_faster_against_cheaper_is_no_winner():
    three("single_agent", 1, mean_seconds=[20, 21, 22], cost_mean_usd=[0.30, 0.31, 0.32])
    three("multi_agent", 5, mean_seconds=[40, 41, 42], cost_mean_usd=[0.10, 0.11, 0.12])
    assert "no winner" in report.build()["reading"]


def test_with_one_system_there_is_no_comparison_yet():
    three("single_agent", 1)
    built = report.build()
    assert built["reading"].startswith("Only single_agent measured")
    assert row(built, "match_rate")["multi_agent"] is None and row(built, "match_rate")["baseline"] == 0.38


# ---------- which runs ----------
def test_the_latest_three_complete_test_runs_are_taken():
    three("single_agent", 1)
    log("single_agent", "20261014-0900-dev", SAME, set_name="dev")                        # not a test run
    log("single_agent", "20261019-1000-late", SAME, partial=True)                        # partial: left out
    assert report.build()["runs"]["single_agent"] == [f"2026101{i}-1000-single_agent" for i in (1, 2, 3)]


def test_a_side_check_is_never_taken_even_when_it_is_the_latest():
    three("single_agent", 1)
    three("multi_agent", 4)
    for i in range(3):                                                    # every agent on Opus: a side check
        log("multi_agent", f"2026102{i}-1000-opus", SAME, label=f"Opus arm: test · repetition {i + 1}")
    assert report.build()["runs"]["multi_agent"] == [f"2026101{i}-1000-multi_agent" for i in (4, 5, 6)]


def test_a_side_check_is_shown_apart_against_the_frozen_system_on_the_same_set():
    three("single_agent", 1)
    three("multi_agent", 4, match_rate=[0.60, 0.61, 0.58])
    log("multi_agent", "20261017-1000-dev", {**SAME, "match_rate": 0.68}, set_name="dev")     # the frozen system
    log("multi_agent", "20261020-1000-opus", {**SAME, "match_rate": 0.77}, set_name="dev", label="Opus arm: dev")
    for i, match in enumerate((0.58, 0.64, 0.61)):
        log("multi_agent", f"2026102{i + 1}-1000-opus", {**SAME, "match_rate": match},
            label=f"Opus arm: test · repetition {i + 1}")
    checks = {c["set"]: c for c in report.build()["side_checks"] if c["label"] == "Opus arm"}
    assert checks["dev"]["frozen_runs"] == ["20261017-1000-dev"] and len(checks["test"]["runs"]) == 3
    match = {c: next(m for m in checks[c]["measures"] if m["key"] == "match_rate") for c in checks}
    assert match["dev"]["verdict"] == "one run — no spread"                    # 0.77 vs 0.68: one run each
    assert match["test"]["verdict"] == "the same"                              # 0.58–0.64 vs 0.58–0.61
    assert "Where the gap comes from" in report.markdown(report.build())


def test_runs_on_different_cases_are_not_compared():
    three("single_agent", 1)
    log("multi_agent", "20261015-1000-x", SAME, decisions={"P-009": ("place", "cardiology")})
    with pytest.raises(report.NotComparable, match="same cases"):
        report.build()


def test_stability_is_the_share_decided_the_same_every_time():
    for i, ward in enumerate(["cardiology", "cardiology", "internal_medicine_a"]):
        log("single_agent", f"2026101{i}-1000-s", SAME,
            decisions={"P-001": ("place", "cardiology"), "P-002": ("temporary_place", ward)})
    assert report.build()["stability"]["single_agent"] == 0.5


# ---------- the days, the month, the files ----------
def test_the_days_are_read_from_their_own_runs():
    three("single_agent", 1)
    log("single_agent", "20261016-1000-day", {"days": "busy_day", "busy_day minutes to bed": 31.5,
                                               "busy_day violations": 0, "busy_day bed_not_available": 1,
                                               "baseline busy_day minutes to bed": 44.0}, set_name="none")
    day = report.build()["days"]["busy_day"]
    assert day["single_agent"]["minutes to bed"] == 31.5 and day["baseline"]["minutes to bed"] == 44.0


def day_run(system, day, minutes, placed, baseline_minutes, baseline_placed, hour="10"):
    log(system, f"20261016-{hour}00-{day}", {"days": day, f"{day} minutes to bed": minutes, f"{day} placed": placed,
                                             f"baseline {day} minutes to bed": baseline_minutes,
                                             f"baseline {day} placed": baseline_placed}, set_name="none")


def test_the_three_days_are_pooled_over_every_patient_placed():
    three("single_agent", 1)
    day_run("single_agent", "busy_day", 30.0, 15, 20.0, 15, "10")
    day_run("single_agent", "evening_night", 10.0, 12, 40.0, 12, "11")
    day_run("single_agent", "weekend", 20.0, 12, 30.0, 10, "12")
    pooled = report.build()["three_days"]
    assert pooled["single_agent"] == pytest.approx((30 * 15 + 10 * 12 + 20 * 12) / 39)    # weighted, not 20.0
    assert pooled["baseline"] == pytest.approx((20 * 15 + 40 * 12 + 30 * 10) / 37)
    assert pooled["multi_agent"] is None


def test_no_pooled_minutes_without_all_three_days():
    three("single_agent", 1)
    day_run("single_agent", "busy_day", 30.0, 15, 20.0, 15)
    assert report.build()["three_days"]["single_agent"] is None


def test_the_contention_day_is_apart_and_has_no_minutes(outputs):
    three("single_agent", 1)
    log("single_agent", "20261016-1300-c", {
        "days": "competing_for_a_bed", "competing moments as designed": 3, "competing moments right": 3,
        "competing_for_a_bed violations": 0, "competing_for_a_bed bed_not_available": 0,
        "baseline competing moments as designed": 0, "baseline competing moments right": 0,
        "baseline competing_for_a_bed violations": 4, "baseline competing_for_a_bed bed_not_available": 0},
        set_name="none")
    built = report.build()
    assert "competing_for_a_bed" not in built["days"]
    assert built["contention"]["single_agent"]["moments right"] == "3 of 3"
    assert built["contention"]["baseline"] == {"moments right": "0 of 0", "violations": 4.0,
                                               "bed_not_available": 0.0}
    text = report.markdown(built)
    day4 = text.split("## Competing for a bed")[1]
    assert "minutes to bed" not in day4 and "| competing_for_a_bed | violations | 0 | — | 4 |" in day4


def test_the_monthly_cost_only_when_the_admissions_are_given():
    three("single_agent", 1, cost_mean_usd=[0.10, 0.10, 0.10])
    assert not any(r["key"] == "cost_month_usd" for r in report.build()["rows"])
    month = row(report.build(admissions_per_month=900), "cost_month_usd")
    assert month["single_agent"].mean == pytest.approx(90.0)


def test_both_files_are_saved(outputs):
    three("single_agent", 1)
    csv_path, md_path = report.save(report.build())
    assert "**Reading:**" in md_path.read_text(encoding="utf8")
    with csv_path.open(encoding="utf8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert {r["measure"] for r in rows} >= {"hard-rule violations", "USD per case, mean"}


# ---------- the run history keeps every repetition ----------
def test_a_new_label_adds_a_run_and_the_same_label_replaces_it(outputs):
    log("single_agent", "a", SAME, label="test · repetition 1")
    log("single_agent", "b", SAME, label="test · repetition 2")
    log("single_agent", "c", SAME, label="test · repetition 2")                       # the same again: replaces b
    with (outputs / "runs.csv").open(encoding="utf8", newline="") as file:
        assert [r["run_id"] for r in csv.DictReader(file)] == ["a", "c"]
