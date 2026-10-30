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
    assert day["single_agent"]["minutes to bed"] == 31.5 and day["baseline minutes to bed"] == 44.0


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
