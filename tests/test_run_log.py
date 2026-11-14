"""The run history (database/run_log.py): runs.csv and admissions.csv accumulate, a rerun at the same version
replaces its own rows, new columns never break old rows — and every decision names the patient's target ward."""
import csv

import pytest
from pydantic import ValidationError

from ed_admission import config
from ed_admission.baselines import code_coordinator
from simple_deciders import never_asks
from ed_admission.database import run_log
from ed_admission.environment import Decision
from ed_admission.evaluation import run_cases


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("ED_OUTPUTS", str(tmp_path))
    return tmp_path


def _rows(path):
    with path.open(encoding="utf8", newline="") as file:
        return list(csv.DictReader(file))


def test_results_go_to_the_folder_in_ed_outputs(outputs):
    assert config.outputs_dir() == outputs


def test_the_version_is_a_fingerprint_that_does_not_change_by_itself():
    assert run_log.version() == run_log.version() and len(run_log.version()) == 10


def test_runs_accumulate_and_a_rerun_with_the_same_label_replaces_its_own_row(outputs):
    """The same decider, version and label again replaces its row; another label is another run (2026-10-15: the
    repetitions of a test, each with its own label, must all be kept)."""
    first = run_log.new_run("system", "never_asks", "first try")
    run_log.log_run(first, {"match_rate": 0.1})
    other = run_log.new_run("system", "multi_agent")
    run_log.log_run(other, {"match_rate": 0.2, "groundedness": 1.0})       # a new column
    again = run_log.new_run("system", "never_asks", "first try")
    run_log.log_run(again, {"match_rate": 0.3})
    another = run_log.new_run("system", "never_asks", "second try")
    run_log.log_run(another, {"match_rate": 0.4})
    rows = _rows(outputs / run_log.RUNS)
    assert [(r["decider"], r["label"], r["match_rate"]) for r in rows] == [
        ("multi_agent", "", "0.2"), ("never_asks", "first try", "0.3"), ("never_asks", "second try", "0.4")]
    assert "groundedness" in rows[0] and rows[1]["groundedness"] == ""


def test_a_new_version_adds_rows_instead_of_replacing(outputs):
    run = run_log.new_run("agent", "example", agent="patient")
    run_log.log_run(run, {"severe": 0.7})
    run_log.log_run({**run, "version": "a-new-one"}, {"severe": 0.8})
    assert [r["severe"] for r in _rows(outputs / run_log.RUNS)] == ["0.7", "0.8"]


def test_the_admissions_log_has_one_row_per_patient_with_the_target_ward(outputs):
    result = run_cases(never_asks, case_ids=["P-043"])[0]
    run = run_log.new_run("system", "never_asks")
    run_log.log_admissions(run, [{"source": "pairs:first", "patient_id": "P-043",
                                  **run_log.decision_row(result.decision)}])
    row = _rows(outputs / run_log.ADMISSIONS)[0]
    assert row["patient_id"] == "P-043" and row["target_ward"] and row["run_id"] == run["run_id"]


@pytest.mark.parametrize("decider", [never_asks, code_coordinator])
def test_every_decision_of_a_decider_that_reads_the_patient_names_the_target_ward(decider):
    assert all(r.decision.target_ward for r in run_cases(decider))


def test_the_target_ward_must_agree_with_the_placement():
    Decision(patient_id="P-001", action="place", ward_id="cardiology", bed_number=1, target_ward="cardiology")
    with pytest.raises(ValidationError):
        Decision(patient_id="P-001", action="place", ward_id="cardiology", bed_number=1,
                 target_ward="internal_medicine_a")
    with pytest.raises(ValidationError):
        Decision(patient_id="P-001", action="temporary_place", ward_id="internal_medicine_b", bed_number=1,
                 awaiting_transfer_to="internal_medicine_a", target_ward="cardiology")
    with pytest.raises(ValidationError):
        Decision(patient_id="P-001", action="escalate", target_ward="no_such_ward")
