"""Tests for the case runner (src/ed_admission/evaluation/case_runner.py)."""
from pathlib import Path

from ed_admission.baselines import first_free_bed
from ed_admission.environment import Decision
from ed_admission.evaluation import load_pairs, run_cases

RECORDS = Path(__file__).resolve().parents[1] / "data" / "records"
WRITTEN = sorted(path.stem for path in RECORDS.glob("P-*.yaml") if path.stem in {p["case_id"] for p in load_pairs()})


def test_runs_every_written_record_once():
    results = run_cases(first_free_bed)
    assert [r.case_id for r in results] == WRITTEN
    assert all(r.decision is not None and not r.error for r in results)


def test_every_case_starts_from_a_fresh_hospital():
    """P-001 and P-002 share template T1: if state carried over, the second would not get the same bed."""
    first, second = run_cases(first_free_bed, case_ids=["P-001", "P-002"])
    assert (first.decision.ward_id, first.decision.bed_number) == (second.decision.ward_id, second.decision.bed_number)


def test_the_decider_sees_only_one_patient_and_no_answers():
    seen = []

    def spy(engine, event, waiting):
        seen.append([p.model_dump() for p in waiting])
        return [Decision.escalate(p.patient_id) for p in waiting]

    run_cases(spy, case_ids=["P-007"])
    assert seen == [[{"patient_id": "P-007", "decided_at": 15 * 60, "awaiting_approval": False}]]


def test_a_failing_decider_is_recorded_and_the_run_goes_on():
    def broken(engine, event, waiting):
        raise RuntimeError("model timeout")

    results = run_cases(broken, case_ids=["P-001", "P-002"])
    assert [r.decision for r in results] == [None, None]
    assert all("model timeout" in r.error for r in results)
