"""Measuring one agent alone (evaluation/agent_metrics.py), the code examples of the two agents
(evaluation/example_agents.py), and choosing a decider by name or by import path (evaluation/deciders.py)."""
import pytest

from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.contracts.negotiation import Answer, WardAnswer
from ed_admission.evaluation import agent_metrics, deciders
from ed_admission.baselines.code_coordinator import code_coordinator
from ed_admission.evaluation import example_agents

TRUTH = agent_metrics.records()


def perfect(toolbox, patient_id) -> PatientAssessment:
    t = TRUTH[patient_id]
    return PatientAssessment(target_ward=t["target_ward"], binding_request=t["wording"] == "binding_request",
                             flags=sorted(t["flags"]), **t["needs"])


def test_every_record_of_both_pair_files_is_measured():
    assert len(TRUTH) == 74


def test_a_perfect_patient_agent_scores_everything():
    checks = agent_metrics.measure("patient", perfect)
    assert len(checks) == 74 * 9 and not agent_metrics.wrong(checks)


@pytest.mark.parametrize("agent", ["patient", "department"])
def test_every_code_example_runs_without_errors(agent):
    checks = agent_metrics.measure(agent, getattr(example_agents, agent))
    assert checks and not any(c.error for c in checks)
    assert agent_metrics.wrong(checks), "the examples read structured fields only — there is room to beat them"


def test_the_patient_example_never_finds_isolation():
    checks = [c for c in agent_metrics.measure("patient", example_agents.patient) if c.check == "isolation"]
    assert all(c.got is False for c in checks) and any(not c.ok for c in checks)


def test_an_agent_that_fails_gets_its_rows_wrong_with_the_error_kept():
    def broken(toolbox, patient_id):
        raise RuntimeError("model unavailable")
    checks = agent_metrics.measure("patient", broken)
    assert all(not c.ok and "model unavailable" in c.error for c in checks)


def test_the_patient_agent_gets_only_its_own_tools():
    def peeks_at_the_wards(toolbox, patient_id):
        toolbox.all_department_status()
    checks = agent_metrics.measure("patient", peeks_at_the_wards)
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


def test_deciders_by_name_or_by_import_path():
    assert deciders.resolve("code_coordinator") is code_coordinator
    assert deciders.resolve("ed_admission.baselines.code_coordinator:code_coordinator") is code_coordinator
    assert deciders.label("ed_admission.baselines.code_coordinator:code_coordinator") == "code_coordinator"
    with pytest.raises(ValueError):
        deciders.resolve("no_such_decider")


# ---------------------------------------------------------------- internal medicine A or B

def test_internal_medicine_without_a_or_b_is_right_when_the_record_names_neither():
    def says_internal_medicine(toolbox, patient_id):
        t = TRUTH[patient_id]
        internal = t["target_ward"].startswith("internal_medicine") and not t["names_internal_medicine"]
        return perfect(toolbox, patient_id).model_copy(update={"target_ward": "internal_medicine"} if internal else {})
    checks = [c for c in agent_metrics.measure("patient", says_internal_medicine) if c.check == "target_ward"]
    assert not [c for c in checks if not c.ok]
    assert sum(c.expected == "internal_medicine" for c in checks) > 20


def test_a_record_that_names_a_or_b_must_get_that_ward():
    named = [pid for pid, t in TRUTH.items() if t["target_ward"].startswith("internal_medicine")
             and t["names_internal_medicine"]]
    assert named
    checks = agent_metrics.measure(
        "patient", lambda toolbox, pid: perfect(toolbox, pid).model_copy(update={"target_ward": "internal_medicine"}))
    assert all(not c.ok for c in checks if c.check == "target_ward" and c.item in named)


def test_the_coordinator_chooses_the_internal_medicine_ward_with_the_lower_occupancy():
    from ed_admission import config
    from ed_admission.environment import load_template
    from ed_admission.tools.rank_placements import occupancy, resolve_target_ward
    engine = load_template(config.PROJECT / "data" / "templates" / "T5.yaml", target_ward="cardiology")
    reports = {w: engine.department_status(w) for w in engine.hospital.wards}
    chosen = resolve_target_ward("internal_medicine", reports)
    other = ({"internal_medicine_a", "internal_medicine_b"} - {chosen}).pop()
    assert occupancy(reports[chosen]) <= occupancy(reports[other])
    assert resolve_target_ward("cardiology", reports) == "cardiology"
