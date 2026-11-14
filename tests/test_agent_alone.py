"""Each agent measured alone with the real agents (evaluation/agent_metrics.py, scripts/run_agent_metrics.py), the
Judge's cases, and the records of a paid run kept as it goes (run_log.append_costs, the paid-run journal). No model
is called: a scripted model answers in the agents' place (tests/test_orchestra_agents.py)."""
import csv
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ed_admission.agents import multi_agent
from ed_admission.agents.agent_loop import LangChainCaller
from ed_admission.agents.budget import RunBudget, RunStopped
from ed_admission.contracts.agent_outputs import JudgeVerdict
from ed_admission.database import run_log
from ed_admission.evaluation import agent_metrics, deciders, sets
from ed_admission.observability import tracing
from ed_admission.observability.tracing import Trace
from ed_admission.tools.patient_record import quote_in_record, sentence_around
from test_orchestra_agents import ScriptedModel

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("ED_OUTPUTS", str(tmp_path))
    tracing.take()
    yield tmp_path
    tracing.take()


def real(cap=100.0, items=None):
    return agent_metrics.RealAgents(RunBudget("multi_agent", cap_usd=cap), ScriptedModel(),
                                    (lambda source, trace: items.append((source, trace))) if items is not None
                                    else None)


# ============ the Judge's cases ============
CASES = agent_metrics.judge_cases()["cases"]


def test_every_judge_case_builds_the_state_the_graph_would_hand_over():
    for case in CASES:
        state = agent_metrics.judge_state(case)
        assert state.proposal.ward_id == case["proposal"]["ward_id"]
        assert set(case["expected"]) == set(agent_metrics.JUDGE_ITEMS)


def test_every_quote_of_a_judge_case_is_in_its_record_with_its_sentence():
    for case in CASES:
        for finding in case["assessment"]["findings"]:
            assert quote_in_record(case["patient_id"], finding["quote"]), (case["case"], finding["quote"])
            assert sentence_around(case["patient_id"], finding["quote"])


def test_each_judge_item_has_cases_that_pass_and_cases_that_fail():
    for item in agent_metrics.JUDGE_ITEMS:
        mine = [c["expected"][item] for c in CASES if c["item"] == item]
        assert True in mine and False in mine, item


def test_no_judge_case_uses_a_test_set_patient():
    test_patients = {patient for patient, _ in sets.pairs_of("test")}
    assert not {c["patient_id"] for c in CASES} & test_patients


def test_the_judge_cases_wait_for_approval():
    assert agent_metrics.judge_cases()["status"] in ("draft", "approved")


# ============ measuring the Judge ============
def perfect_judge(engine, state):
    case = next(c for c in CASES if agent_metrics.judge_state(c) == state)
    items = [{"number": n, "passed": case["expected"][n], "reason": "as expected"} for n in agent_metrics.JUDGE_ITEMS]
    valid = case["expected"][2] and case["expected"][8]
    return JudgeVerdict(valid=valid, items=items, correction=None if valid else "read it again")


def test_a_judge_that_reads_every_case_right_scores_everything():
    summary = agent_metrics.summarize(agent_metrics.measure_judge(perfect_judge))
    assert all(row["rate"] == 1.0 for row in summary.values())
    assert summary["wrong placement caught"]["items"] == 4 and summary["right placement let through"]["items"] == 8


def test_the_real_judge_agent_that_lets_everything_through_catches_nothing():
    items = []
    summary = agent_metrics.summarize(agent_metrics.measure("judge", None, real=real(items=items)))
    assert summary["wrong placement caught"]["rate"] == 0.0
    assert summary["right placement let through"]["rate"] == 1.0
    assert len(items) == len(CASES) and all(source == "agent:judge" for source, _ in items)


# ============ the real Patient and Department Agents, on the development set ============
def test_the_real_patient_agent_is_measured_on_dev_and_each_item_is_reported_as_it_ends():
    items = []
    checks = agent_metrics.measure("patient", None, limit=2, real=real(items=items))
    measured = {c.item for c in checks}
    assert len(measured) == 2 and measured <= set(agent_metrics.patients_in("dev"))
    assert any(c.check == "quotes in the record" for c in checks)
    assert [source for source, _ in items] == ["agent:patient", "agent:patient"]
    assert all(trace.cost_usd > 0 and trace.ended == "final" for _, trace in items)


def test_the_real_department_agent_gets_only_dev_requests():
    items = []
    checks = agent_metrics.measure("department", None, limit=2, real=real(items=items))
    assert checks and len(items) == 2
    dev = agent_metrics.requests("dev")
    assert len(dev) < len(agent_metrics.requests("all"))


def test_a_ward_that_asks_stable_is_asked_again_with_codes_answer_as_the_graph_does():
    from ed_admission.contracts.negotiation import Answer, WardAnswer

    class AsksStable:                                        # asks once, then answers by what it was told
        def __init__(self):
            self.tasks = []

        def department(self, engine, task, item):
            self.tasks.append(task)
            if task.patient_answer is None:
                return WardAnswer(answer=Answer.CONDITION, bed_number=1, source="note", asks="stable",
                                  condition="a stable patient only", reason="the note")
            if task.patient_answer:
                return WardAnswer(answer=Answer.ACCEPT, bed_number=1, source="note", reason="stable")
            return WardAnswer(answer=Answer.DECLINE, reason="not stable")

    agents = AsksStable()
    agent_metrics.measure_department(None, limit=3, agents=agents, set_name="dev")
    again = [t for t in agents.tasks if t.patient_answer is not None]
    assert len(again) == 3                                   # each ward asked again, once
    for task in again:
        r = task.request
        assert task.patient_answer == (not (r.severe or r.observation or r.bed_type == "intensive_care"))


def test_the_test_set_is_refused_for_the_real_agents():
    with pytest.raises(ValueError, match="sealed"):
        agent_metrics.measure("patient", None, real=real(), set_name="test")


def test_a_spent_run_budget_stops_the_measurement_and_is_not_a_wrong_row():
    items = []
    with pytest.raises(RunStopped):
        agent_metrics.measure("patient", None, limit=3, real=real(cap=0.000001, items=items))
    assert len(items) == 1                                   # the item that reached the cap is still reported


# ============ the script ============
def script(*args, outputs):
    import os
    env = {**os.environ, "ED_OUTPUTS": str(outputs), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "scripts/run_agent_metrics.py", *args], cwd=PROJECT, env=env,
                          capture_output=True, text=True, encoding="utf-8")


def test_the_script_refuses_a_real_agent_without_paid_and_the_test_set(outputs):
    assert "with --paid" in script("--agent", "patient", "--real", outputs=outputs).stdout
    assert "sealed" in script("--agent", "patient", "--real", "--paid", "--set", "test", outputs=outputs).stdout
    assert "no code example" in script("--agent", "judge", outputs=outputs).stdout


def test_paid_items_are_written_one_at_a_time(outputs):
    from importlib import util
    spec = util.spec_from_file_location("run_agent_metrics", PROJECT / "scripts" / "run_agent_metrics.py")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = run_log.new_run("agent", "multi_agent", "try", agent="patient")
    paid = module.PaidItems(run)
    for name in ("P-004", "P-008"):
        trace = Trace(case_id=name, decider="multi_agent")
        trace.agent_call("patient", "task", {"status": "final"}, model="m", prompt_version="v", cost_usd=0.01)
        paid("agent:patient", trace)
        with (outputs / "costs.csv").open(encoding="utf8", newline="") as file:
            assert [r["case_id"] for r in csv.DictReader(file)][-1] == name     # written the moment it ended
    with (outputs / "costs.csv").open(encoding="utf8", newline="") as file:
        assert [r["case_id"] for r in csv.DictReader(file)] == ["P-004", "P-008"]
    assert (outputs / "traces" / f"{run['run_id']}.jsonl").read_text(encoding="utf8").count("\n") == 2
    assert paid.summary()["cost_total_usd"] == pytest.approx(0.02)


def test_append_costs_replaces_an_earlier_run_with_the_same_label(outputs):
    first = run_log.new_run("agent", "multi_agent", "same", agent="judge")
    run_log.append_costs(first, [{"case_id": "J01"}])
    second = {**first, "run_id": first["run_id"] + "-again"}
    run_log.append_costs(second, [{"case_id": "J02"}])
    run_log.append_costs(second, [{"case_id": "J03"}])
    with (outputs / "costs.csv").open(encoding="utf8", newline="") as file:
        assert [r["case_id"] for r in csv.DictReader(file)] == ["J02", "J03"]


# ============ the paid-run journal, how a case ended, one model per agent ============
def test_a_paid_run_writes_every_case_to_the_journal_as_it_is_collected(outputs, monkeypatch):
    monkeypatch.setattr(multi_agent.budget, "start_run", lambda system: RunBudget(system, cap_usd=1.0))
    multi_agent.use_agents(lambda engine, trace, run_budget: None)
    try:
        with deciders.paid_run(multi_agent.run):
            tracing.collect(Trace(case_id="P-001", decider="multi_agent"))
            [journal] = list((outputs / "traces").glob("journal-multi_agent-*.jsonl"))
            assert '"case_id": "P-001"' in journal.read_text(encoding="utf8")
    finally:
        multi_agent.use_agents(None)


def test_a_case_whose_agent_did_not_finish_says_so():
    trace = Trace(case_id="P-001", decider="multi_agent")
    trace.agent_call("patient", "task", {"status": "final"}, model="m", prompt_version="v")
    trace.agent_call("department", "task", {"status": "no_final"}, model="m", prompt_version="v")
    assert multi_agent._how_it_ended(trace) == "no_final"
    assert multi_agent._how_it_ended(Trace(case_id="P-002", decider="multi_agent")) == "final"


def test_the_wards_of_a_rung_calling_at_once_get_one_model():
    made = []

    def make_model(agent):
        made.append(agent)
        threading.Event().wait(0.05)                        # slow enough for the calls to overlap
        raise RuntimeError("stop here: only the model's making is tested")

    caller = LangChainCaller(make_model)
    caller.models["department"] = None                      # already made once for this run
    threads = [threading.Thread(target=lambda: _try(caller)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert made == []


def _try(caller):
    try:
        caller("system", [], [], "department")
    except Exception:
        pass
