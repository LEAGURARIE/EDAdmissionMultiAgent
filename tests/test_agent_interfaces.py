"""What the agents use: the toolbox (permissions in code), the view a Department Agent gets, the trace (complete,
grounded), and a scripted day that counts a bed the ward cannot give."""
from pathlib import Path

import pytest

from ed_admission.contracts.negotiation import WardRequest
from ed_admission.environment import load_scenario, load_template
from ed_admission.evaluation import metrics
from ed_admission.observability.tracing import Claim, Trace
from ed_admission.tools.toolbox import PermissionDenied, Toolbox

PROJECT = Path(__file__).resolve().parents[1]
TEMPLATES = PROJECT / "data" / "templates"


def engine_of(template: str, target: str = "internal_medicine_a"):
    return load_template(TEMPLATES / f"{template}.yaml", target_ward=target)


# ---------------------------------------------------------------- the toolbox

def test_the_department_agent_never_sees_a_record_or_another_ward():
    toolbox = Toolbox("department", engine_of("T13"), ward_id="internal_medicine_a")
    with pytest.raises(PermissionDenied):
        toolbox.read_patient_record("P-001")
    with pytest.raises(PermissionDenied):
        toolbox.all_department_status()
    view = toolbox.department_view()
    assert view.ward_id == "internal_medicine_a" and view.notes            # its own report and its own notes


def test_the_reports_show_every_ward_but_no_note():
    engine = engine_of("T13")
    reports = Toolbox("single_agent", engine).all_department_status()
    assert set(reports) == set(engine.hospital.wards)
    assert engine.ward_notes("internal_medicine_a")[0] not in str(reports)


def test_no_agent_of_the_multi_agent_system_decides_or_escalates():
    """The Coordinator is code: it decides from the agents' closed answers. The agents only answer."""
    engine = engine_of("T1")
    for toolbox in (Toolbox("patient", engine), Toolbox("judge", engine),
                    Toolbox("department", engine, ward_id="cardiology")):
        with pytest.raises(PermissionDenied):
            toolbox.escalate_to_human("P-001", "no bed")


def test_a_department_toolbox_must_be_bound_to_one_ward():
    with pytest.raises(ValueError):
        Toolbox("department", engine_of("T1"))
    with pytest.raises(ValueError):
        Toolbox("patient", engine_of("T1"), ward_id="cardiology")
    with pytest.raises(ValueError):
        Toolbox("coordinator", engine_of("T1"))                      # the Coordinator is code: it has no toolbox


def test_severity_gets_news2_and_the_calls_go_to_the_trace():
    trace = Trace("P-063", "test")
    result = Toolbox("patient", engine_of("T2"), trace=trace).compute_news2("P-063")
    assert result["total"] == 4 and result["level_of_care"] == "regular"
    assert [s.name for s in trace.steps] == ["compute_news2"]


# ---------------------------------------------------------------- the request and the trace

def test_a_request_to_a_ward_carries_the_needs_and_no_patient_identifier():
    request = WardRequest(bed_type="observation", severe=True, observation=True, isolation=False,
                          target_ward="internal_medicine_a")
    assert "patient_id" not in request.model_dump()


def test_groundedness_checks_the_quote_against_the_record():
    trace = Trace("P-043", "test")
    trace.agent_call("patient", "P-043", "isolation", model="m", prompt_version="1",
                     claims=[Claim("C. difficile suspected", "לשלול C. diff"), Claim("made up", "לא כתוב ברשומה")])
    assert metrics.groundedness([trace]) == 0.5


def test_groundedness_reads_the_patient_agents_task_in_a_paired_case():
    from ed_admission.contracts.agent_outputs import PatientTask
    trace = Trace("P-043+P-001", "multi_agent")             # a paired case: no record has this name
    trace.agent_call("patient", PatientTask(patient_id="P-043", decided_at=0), "isolation", model="m",
                     prompt_version="1", claims=[Claim("C. difficile suspected", "לשלול C. diff")])
    assert metrics.groundedness([trace]) == 1.0


# ---------------------------------------------------------------- a scripted day

def test_a_day_counts_a_bed_the_ward_cannot_give():
    """Weekend: cardiology's free observation bed is promised (its note) — a decider that never asks takes it."""
    from simple_deciders import first_free_bed
    engine = load_scenario(PROJECT / "data" / "scenarios" / "weekend.yaml")
    engine.policy = first_free_bed
    engine.run_to_end()
    assert metrics.score_day(engine)["bed_not_available"] >= 1


def test_the_single_agent_does_every_agents_job():
    """The fair comparison (2026-10-15): one agent with every agent's tools. It reads the record and hands in its
    own assessment (record_assessment), and it reads every ward's report and notes itself (department_view, the
    Department Agents' tool): the same task as the multi-agent system, not a ward that answers from the true state."""
    from ed_admission.tools.toolbox import PERMISSIONS
    every_agent = {tool for tool, roles in PERMISSIONS.items() if roles - {"single_agent"}}
    single = {tool for tool, roles in PERMISSIONS.items() if "single_agent" in roles}
    # answer_ward is a ward's own answer to a request: the single agent asks no ward, it reads the notes and decides.
    # read_case and give_verdict are the Judge Agent's: the single agent's decisions are checked by decide, as before
    assert every_agent - {"answer_ward", "read_case", "give_verdict"} <= single
    view = Toolbox("single_agent", engine_of("T13")).department_view("internal_medicine_a")
    assert view.ward_id == "internal_medicine_a"


def test_the_department_agent_still_sees_only_its_own_ward():
    toolbox = Toolbox("department", engine_of("T13"), ward_id="internal_medicine_a")
    assert toolbox.department_view().ward_id == "internal_medicine_a"
    with pytest.raises(PermissionDenied):
        toolbox.department_view("cardiology")
