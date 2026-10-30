"""The cost records: each case's cost in its trace, costs.csv (one row per case), the run's cost summary for runs.csv.
The comparison of the two systems is tested in tests/test_comparison_report.py. No model is called."""
import csv

import pytest

from ed_admission.database import run_log
from ed_admission.evaluation import metrics
from ed_admission.observability.tracing import Trace


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("ED_OUTPUTS", str(tmp_path))
    return tmp_path


def single_agent_case(case_id, cost, ended="final", calls=4):
    trace = Trace(case_id=case_id, decider="single_agent", ended=ended)
    trace.agent_call("single_agent", case_id, "decided", model="claude-opus-5-5", prompt_version="1",
                     tokens_in=10_000, tokens_out=800, cost_usd=cost, model_calls=calls)
    return trace


def multi_agent_case(case_id, patient_cost, department_cost, ended="final"):
    trace = Trace(case_id=case_id, decider="coordinator", ended=ended)
    trace.agent_call("patient", case_id, "assessed", model="m", prompt_version="1", cost_usd=patient_cost,
                     model_calls=2, tokens_in=5_000, tokens_out=300)
    trace.agent_call("department", case_id, "answered", model="m", prompt_version="1", cost_usd=department_cost,
                     model_calls=1, tokens_in=2_000, tokens_out=100)
    return trace


def log(run_id, sourced):
    run = {"run_id": run_id, "time": "", "kind": "system", "decider": run_id, "agent": "", "version": "v",
           "label": ""}
    run_log.log_costs(run, [run_log.cost_row(source, trace) for source, trace in sourced])


# ---------- the trace ----------
def test_the_trace_knows_its_system_calls_tokens_and_cost_per_agent():
    single = single_agent_case("P-001", 0.30)
    assert single.system == "single_agent" and single.model_calls == 4 and single.tokens_in == 10_000
    multi = multi_agent_case("P-001", 0.20, 0.05)
    assert multi.system == "multi_agent" and multi.model_calls == 3
    assert multi.cost_by_role == {"patient": 0.20, "department": 0.05}
    assert Trace(case_id="P-001", decider="code_coordinator").system == "code"


# ---------- costs.csv ----------
def test_one_row_per_case_with_a_column_per_agent(outputs):
    log("20261020-100000-coordinator", [("pairs:first", multi_agent_case("P-001", 0.2, 0.05)),
                                         ("day:busy_day", multi_agent_case("P-002", 0.1, 0.0, ended="budget"))])
    with (outputs / "costs.csv").open(encoding="utf8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [(r["source"], r["case_id"], r["ended"]) for r in rows] == [("pairs:first", "P-001", "final"),
                                                                     ("day:busy_day", "P-002", "budget")]
    assert rows[0]["system"] == "multi_agent" and float(rows[0]["cost_usd"]) == pytest.approx(0.25)
    assert float(rows[0]["cost_patient_usd"]) == 0.2 and float(rows[0]["cost_department_usd"]) == 0.05


# ---------- the run's summary, for runs.csv ----------
def test_the_run_summary_counts_every_case_the_cap_stops_and_a_partial_run():
    traces = [single_agent_case("P-001", 0.10), single_agent_case("P-002", 0.30, ended="budget"),
              single_agent_case("P-003", 0.20), single_agent_case("P-004", 0.40, ended="run_budget")]
    s = metrics.cost_summary(traces)
    assert s["system"] == "single_agent" and s["cost_cases"] == 4
    assert s["cost_total_usd"] == pytest.approx(1.0) and s["cost_mean_usd"] == pytest.approx(0.25)
    assert s["cost_median_usd"] == pytest.approx(0.25) and s["cost_max_usd"] == 0.40
    assert s["cases_over_case_cap"] == 1 and s["partial_run"] is True


def test_no_traces_no_cost_summary():
    assert metrics.cost_summary([]) is None
