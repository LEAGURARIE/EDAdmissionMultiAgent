"""Measuring one agent alone (evaluation/agent_metrics.py), the code examples of every agent
(orchestration/example_agents.py), and choosing a decider by name or by import path (evaluation/deciders.py)."""
import pytest

from ed_admission.contracts.agent_outputs import (ClinicalProblemOutput, SafetyVerdict, SeverityOutput,
                                                  SupervisionOutput)
from ed_admission.contracts.negotiation import Answer, WardAnswer
from ed_admission.evaluation import agent_metrics, deciders
from ed_admission.orchestration import example_agents
from ed_admission.orchestration.example_pipeline import example_pipeline

TRUTH = agent_metrics.records()


def test_every_record_of_both_pair_files_is_measured():
    assert len(TRUTH) == 74


@pytest.mark.parametrize("agent", ["clinical_problem", "severity", "supervision"])
def test_a_perfect_patient_side_agent_scores_everything(agent):
    def perfect(toolbox, patient_id):
        t = TRUTH[patient_id]
        flags = sorted(t["flags"])
        return {"clinical_problem": ClinicalProblemOutput(target_ward=t["target_ward"], flags=flags),
                "severity": SeverityOutput(severe=t["needs"]["severe"], intensive_care=t["needs"]["intensive_care"],
                                           flags=flags),
                "supervision": SupervisionOutput(observation=t["needs"]["observation"],
                                                 isolation=t["needs"]["isolation"])}[agent]
    checks = agent_metrics.measure(agent, perfect)
    assert checks and not agent_metrics.wrong(checks)


@pytest.mark.parametrize("agent", ["clinical_problem", "severity", "supervision", "department"])
def test_every_code_example_runs_without_errors(agent):
    checks = agent_metrics.measure(agent, getattr(example_agents, agent))
    assert checks and not any(c.error for c in checks)
    assert agent_metrics.wrong(checks), "the examples read structured fields only — there is room to beat them"


def test_an_agent_that_fails_gets_its_rows_wrong_with_the_error_kept():
    def broken(toolbox, patient_id):
        raise RuntimeError("model unavailable")
    checks = agent_metrics.measure("severity", broken)
    assert all(not c.ok and "model unavailable" in c.error for c in checks)


def test_a_patient_side_agent_gets_only_its_own_tools():
    def peeks_at_the_wards(toolbox, patient_id):
        toolbox.all_department_status()
    checks = agent_metrics.measure("severity", peeks_at_the_wards)
    assert all("PermissionDenied" in c.error for c in checks)


def test_a_department_agent_that_reads_only_its_report_misses_every_request_the_note_decides():
    checks = agent_metrics.measure("department", example_agents.department)
    decided_by_notes = [c for c in checks if c.check == "bed_now, the note decides"]
    assert decided_by_notes and not any(c.ok for c in decided_by_notes)


def test_a_department_agent_that_always_declines_is_right_only_where_there_is_no_bed():
    def never(view, request):
        return WardAnswer(answer=Answer.DECLINE, reason="no")
    checks = [c for c in agent_metrics.measure("department", never) if c.check == "bed_now"]
    assert all(c.ok == (c.expected is False) for c in checks)


def test_a_safety_critic_that_approves_everything_catches_nothing():
    checks = agent_metrics.measure("safety_critic", lambda toolbox, pid, decision: SafetyVerdict(approve=True))
    summary = agent_metrics.summarize(checks)
    assert summary["approves a safe decision"]["rate"] == 1
    assert summary["blocks an unsafe decision"]["rate"] == 0


def test_deciders_by_name_or_by_import_path():
    assert deciders.resolve("example_pipeline") is example_pipeline
    assert deciders.resolve("ed_admission.orchestration.example_pipeline:example_pipeline") is example_pipeline
    assert deciders.label("ed_admission.orchestration.example_pipeline:example_pipeline") == "example_pipeline"
    with pytest.raises(ValueError):
        deciders.resolve("no_such_decider")
