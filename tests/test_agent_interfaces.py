"""What the agents will use: the toolbox (permissions in code), the negotiation channel (limits, time, the view a
Department Agent gets), the placement case (steps in order), the trace (complete, grounded) — and an end-to-end run
of the example pipeline through all of them."""
from pathlib import Path

import pytest

from ed_admission.contracts.negotiation import Answer, WardAnswer, WardRequest
from ed_admission.environment import load_scenario, load_template
from ed_admission.environment.decision import Decision
from ed_admission.evaluation import metrics, run_cases, run_paired
from ed_admission.observability import tracing
from ed_admission.observability.tracing import Claim, Trace
from ed_admission.orchestration import example_pipeline as pipeline
from ed_admission.orchestration.placement_case import PlacementCase, StepOutOfOrder
from ed_admission.tools.negotiation import LimitReached, Negotiation, truthful_ward
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


def test_the_coordinator_sees_every_report_but_no_note():
    engine = engine_of("T13")
    reports = Toolbox("coordinator", engine).all_department_status()
    assert set(reports) == set(engine.hospital.wards)
    assert engine.ward_notes("internal_medicine_a")[0] not in str(reports)
    with pytest.raises(PermissionDenied):
        Toolbox("coordinator", engine).department_view()


def test_only_the_coordinator_acts():
    engine = engine_of("T1")
    for role in ("patient",):
        with pytest.raises(PermissionDenied):
            Toolbox(role, engine).escalate_to_human("P-001", "no bed")


def test_a_department_toolbox_must_be_bound_to_one_ward():
    with pytest.raises(ValueError):
        Toolbox("department", engine_of("T1"))
    with pytest.raises(ValueError):
        Toolbox("coordinator", engine_of("T1"), ward_id="cardiology")


def test_severity_gets_news2_and_the_calls_go_to_the_trace():
    trace = Trace("P-063", "test")
    result = Toolbox("patient", engine_of("T2"), trace=trace).compute_news2("P-063")
    assert result["total"] == 4 and result["level_of_care"] == "regular"
    assert [s.name for s in trace.steps] == ["compute_news2"]


# ---------------------------------------------------------------- the negotiation

REQUEST = WardRequest(bed_type="observation", severe=True, observation=True, isolation=False,
                      target_ward="internal_medicine_a")


def test_a_ward_answers_from_its_true_state():
    """T13: the target ward's report shows a free observation bed; its note says no nurse — it declines."""
    engine = engine_of("T13")
    negotiation = Negotiation(engine, {w: truthful_ward(engine) for w in engine.hospital.wards})
    assert negotiation.ask("internal_medicine_a", REQUEST).answer == Answer.DECLINE
    assert len(negotiation.exchanges) == 1


def test_the_department_agent_gets_needs_and_its_own_ward_only():
    seen = []

    def responder(view, request):
        seen.append((view.ward_id, sorted(request.model_dump())))
        return WardAnswer(answer=Answer.DECLINE, reason="no")
    engine = engine_of("T13")
    Negotiation(engine, {"internal_medicine_a": responder}).ask("internal_medicine_a", REQUEST)
    assert seen == [("internal_medicine_a", sorted(REQUEST.model_dump()))]
    assert "patient_id" not in REQUEST.model_dump()


def test_the_limits_3_per_ward_and_8_per_case():
    engine = engine_of("T1")
    negotiation = Negotiation(engine, {w: truthful_ward(engine) for w in engine.hospital.wards})
    for _ in range(3):
        negotiation.ask("cardiology", REQUEST)
    with pytest.raises(LimitReached):
        negotiation.ask("cardiology", REQUEST)
    for ward in ("internal_medicine_a", "internal_medicine_b", "general_surgery"):
        negotiation.ask(ward, REQUEST)
    negotiation.ask("internal_medicine_a", REQUEST)
    negotiation.ask("internal_medicine_b", REQUEST)                      # 8 requests in the case
    with pytest.raises(LimitReached):
        negotiation.ask("general_surgery", REQUEST)


# ---------------------------------------------------------------- the placement case and the trace

def test_the_steps_must_come_in_order():
    case = PlacementCase("P-001", Trace("P-001", "test"))
    with pytest.raises(StepOutOfOrder):
        case.move("ranked")
    case.move("patient_assessed")
    case.move("decided", Decision.escalate("P-001", "an agent failed twice"))    # escalation ends a case early
    assert not case.trace.complete                                                # ... and the trace says so


def test_after_three_refused_decisions_only_an_escalation_is_left():
    case = PlacementCase("P-001", Trace("P-001", "test"))
    for step in ("patient_assessed", "availability_collected", "ranked"):
        case.move(step)
    for _ in range(3):
        assert not case.must_escalate
        case.refuse(["the patient needs observation; the bed is a regular bed"])
    assert case.must_escalate


def test_the_coordinators_tools_move_the_case_and_refuse_a_step_out_of_order():
    from ed_admission.orchestration import example_agents
    from ed_admission.tools.rank_placements import PatientNeeds
    engine = engine_of("T1")
    case = PlacementCase("P-001", Trace("P-001", "test"), Negotiation(engine, {}))
    coordinator = Toolbox("coordinator", engine, case=case, patient_agent=example_agents.patient)
    with pytest.raises(StepOutOfOrder):
        coordinator.rank_placements(PatientNeeds("internal_medicine_a", "regular", severe=False))   # not assessed yet
    coordinator.assess_patient("P-001")
    coordinator.all_department_status()
    coordinator.all_department_status()                                   # read again: nothing changes
    coordinator.rank_placements(PatientNeeds("internal_medicine_a", "regular", severe=False))
    assert case.history == ["triggered", "patient_assessed", "availability_collected", "ranked"]
    coordinator.escalate_to_human("P-001", "no safe option")
    assert case.step == "decided" and case.trace.complete


def test_groundedness_checks_the_quote_against_the_record():
    trace = Trace("P-043", "test")
    trace.agent_call("patient", "P-043", "isolation", model="m", prompt_version="1",
                     claims=[Claim("C. difficile suspected", "לשלול C. diff"), Claim("made up", "לא כתוב ברשומה")])
    assert metrics.groundedness([trace]) == 0.5


# ---------------------------------------------------------------- end to end

def test_the_example_pipeline_runs_through_every_interface():
    tracing.take()                                        # start from a clean collection
    results = run_cases(pipeline.example_pipeline) + run_cases(pipeline.example_pipeline, evaluation_set="second")
    assert len(results) == 104 and not any(r.error for r in results)
    assert not any(r.error for r in run_paired(pipeline.example_pipeline))
    traces = tracing.take()
    assert len(traces) == 104 + 14
    assert metrics.complete_trace_rate(traces) == 1.0 and metrics.groundedness(traces) == 1.0
    assert metrics.summarize(metrics.score_cases(results))["bed_not_available"] == 0   # it asked every ward


def test_the_example_pipeline_runs_a_scripted_day():
    engine = load_scenario(PROJECT / "data" / "scenarios" / "evening_night.yaml")
    engine.policy = pipeline.example_pipeline
    engine.run_to_end()
    assert metrics.score_day(engine)["bed_not_available"] == 0


def test_a_day_counts_a_bed_the_ward_cannot_give():
    """Weekend: cardiology's free observation bed is promised (its note) — a decider that never asks takes it."""
    from simple_deciders import first_free_bed
    engine = load_scenario(PROJECT / "data" / "scenarios" / "weekend.yaml")
    engine.policy = first_free_bed
    engine.run_to_end()
    assert metrics.score_day(engine)["bed_not_available"] >= 1


def test_the_single_agent_does_every_agents_job():
    """The fair comparison (2026-10-15): one agent with every agent's tools. It reads the record and hands in its
    own assessment (record_assessment, instead of assess_patient which runs the Patient Agent), and it reads every
    ward's report and notes itself (department_view, the Department Agents' tool) — so it has no wards to ask
    (no ask_ward): the same task as the multi-agent system, not a ward that answers from the true state."""
    from ed_admission.tools.toolbox import PERMISSIONS
    every_agent = {tool for tool, roles in PERMISSIONS.items() if roles - {"single_agent"}}
    single = {tool for tool, roles in PERMISSIONS.items() if "single_agent" in roles}
    assert single == every_agent - {"assess_patient", "ask_ward"} | {"record_assessment"}
    view = Toolbox("single_agent", engine_of("T13")).department_view("internal_medicine_a")
    assert view.ward_id == "internal_medicine_a"


def test_the_department_agent_still_sees_only_its_own_ward():
    toolbox = Toolbox("department", engine_of("T13"), ward_id="internal_medicine_a")
    assert toolbox.department_view().ward_id == "internal_medicine_a"
    with pytest.raises(PermissionDenied):
        toolbox.department_view("cardiology")
