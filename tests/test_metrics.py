"""The metrics (src/ed_admission/evaluation/metrics.py): a decider that gives exactly the expected answers scores
perfectly; known mistakes are counted as the right violation."""
import yaml

from simple_deciders import first_free_bed
from ed_admission.environment import Decision
from ed_admission.environment.decision import Flag
from ed_admission.environment.hospital import BedStatus, BedType
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation import load_pairs, metrics, run_cases
from ed_admission.evaluation.case_runner import CaseResult

PAIRS = load_pairs("first") + load_pairs("second")
ANSWERS = [yaml.safe_load(builder.answer_path(p).read_text(encoding="utf8")) for p in PAIRS]


def expected_as_decision(answer: dict) -> Decision:
    """The expected answer as a decision: a free bed of the expected type, in the true ward state."""
    e, pid = answer["expected"], answer["case_id"]
    flags = [Flag(f) for f in e["required_flags"]]
    if e["action"] in ("place", "temporary_place"):
        state = builder.true_ward_state(answer)
        bed = next(b for b in state.hospital.ward(e["ward_id"]).beds
                   if b.bed_type == BedType(e["bed_type"]) and b.status == BedStatus.FREE)
        return Decision(patient_id=pid, action=e["action"], ward_id=e["ward_id"], bed_number=bed.number,
                        awaiting_transfer_to=e["awaiting_transfer_to"], flags=flags,
                        physician_approval_for_icu=e["physician_approval_for_icu"])
    if e["action"] == "wait":
        w = e["wait_for"]
        return Decision(patient_id=pid, action="wait", flags=flags,
                        wait_for={"ward_id": w["ward_id"], "bed_number": w["bed_number"], "because": w["because"]})
    return Decision(patient_id=pid, action="escalate", flags=flags)


def test_the_expected_answers_score_perfectly():
    results = [CaseResult(a["case_id"], a["template"], expected_as_decision(a), 0.0) for a in ANSWERS]
    summary = metrics.summarize(metrics.score_cases(results))
    assert summary["match_rate"] == 1.0 and summary["violations"] == 0 and summary["need_met_rate"] == 1.0
    assert summary["bed_not_available"] == 0 and summary["missing_flags"] == 0
    assert all(summary["meets_charter"].values())
    assert summary["report_match_rate"] == 0.0         # every pair needs the notes: right answer != reports alone


def reports_alone_as_decision(answer: dict) -> Decision:
    """What a decider that reads the patient right but never asks the wards would decide."""
    alone = metrics._from_reports_alone(answer)
    e = {"action": alone.action, "ward_id": alone.ward_id, "bed_type": alone.bed_type, "wait_for": alone.wait_for,
         "awaiting_transfer_to": alone.awaiting_transfer_to, "required_flags": [], "physician_approval_for_icu": False}
    if alone.action in ("place", "temporary_place"):
        state = builder.true_ward_state(answer, with_notes=False)
        bed = next(b for b in state.hospital.ward(alone.ward_id).beds
                   if b.bed_type == BedType(alone.bed_type) and b.status == BedStatus.FREE)
        return Decision(patient_id=answer["case_id"], action=alone.action, ward_id=alone.ward_id,
                        bed_number=bed.number, awaiting_transfer_to=alone.awaiting_transfer_to)
    return expected_as_decision({**answer, "expected": e})


def test_a_decider_that_never_asks_reaches_only_the_report_match():
    results = [CaseResult(a["case_id"], a["template"], reports_alone_as_decision(a), 0.0) for a in ANSWERS]
    summary = metrics.summarize(metrics.score_cases(results))
    assert summary["report_match_rate"] == 1.0 and summary["match_rate"] == 0.0


def test_an_observation_patient_in_a_regular_bed_is_a_violation():
    answer = next(a for a in ANSWERS if a["patient_type"] == "B")
    state = builder.true_ward_state(answer)
    ward = next(w for w in state.hospital.wards.values()
                if any(b.bed_type == BedType.REGULAR and b.status == BedStatus.FREE for b in w.beds))
    bed = next(b for b in ward.beds if b.bed_type == BedType.REGULAR and b.status == BedStatus.FREE)
    action = "place" if ward.ward_id == answer["target_ward"] else "temporary_place"
    decision = Decision(patient_id=answer["case_id"], action=action, ward_id=ward.ward_id, bed_number=bed.number,
                        awaiting_transfer_to=None if action == "place" else answer["target_ward"])
    scored = metrics.score(answer, decision)
    assert "observation_need_in_a_regular_bed" in scored.violations and scored.need_met is False


def test_a_severe_patient_told_to_wait_is_a_violation():
    answer = next(a for a in ANSWERS if a["needs"]["severe"])
    scored = metrics.score(answer, Decision.wait(answer["case_id"], "waiting"))
    assert "severe_patient_waits" in scored.violations


def test_a_missing_flag_is_counted():
    answer = next(a for a in ANSWERS if a["expected"]["required_flags"])
    decision = expected_as_decision(answer).model_copy(update={"flags": []})
    assert metrics.score(answer, decision).missing_flags == answer["expected"]["required_flags"]


def test_first_free_bed_breaks_hard_rules():
    """The safety reference: it ignores the patient, so it breaks hard rules and meets almost no needs."""
    summary = metrics.summarize(metrics.score_cases(run_cases(first_free_bed)))
    assert summary["violations"] > 0 and not summary["meets_charter"]["violations"]


def test_breakdowns_cover_every_decision():
    scored = metrics.score_cases(run_cases(first_free_bed))
    assert sum(row["decisions"] for row in metrics.breakdown(scored, "template").values()) == len(scored)
    assert sum(row["decisions"] for row in metrics.breakdown(scored, "challenge").values()) == len(scored)


def run_day(name, policy):
    from pathlib import Path
    from ed_admission.environment import load_scenario
    engine = load_scenario(Path(__file__).resolve().parents[1] / "data" / "scenarios" / f"{name}.yaml")
    engine.policy = policy
    engine.run_to_end()
    return engine


def test_minutes_saved_against_the_same_decider_is_zero():
    from ed_admission.baselines import code_coordinator
    result = metrics.minutes_saved(run_day("busy_day", code_coordinator), run_day("busy_day", code_coordinator))
    assert result["patients"] == 15 and result["total_saved"] == 0
    assert all(r["saved"] in (0, None) for r in result["rows"])


def test_minutes_saved_covers_every_patient_of_the_day():
    from ed_admission.baselines import code_coordinator
    from simple_deciders import never_asks
    result = metrics.minutes_saved(run_day("weekend", code_coordinator), run_day("weekend", never_asks))
    assert result["patients"] == 12
    for row in result["rows"]:
        if row["saved"] is not None:
            assert row["saved"] == row["baseline"]["minutes"] - row["system"]["minutes"]


def test_a_slow_decider_pays_in_minutes():
    """The engine measures the decider: 70 real seconds count as 2 simulated minutes before the approval."""
    import time as real_time
    from simple_deciders import first_free_bed
    from ed_admission.environment import simulation_engine
    clock = iter([0.0, 70.0] + [70.0] * 1000)
    original = simulation_engine.time.perf_counter
    simulation_engine.time.perf_counter = lambda: next(clock)
    try:
        engine = run_day("busy_day", first_free_bed)
    finally:
        simulation_engine.time.perf_counter = original
    first = engine.placements[0]
    assert first.deciding_minutes == 2 and first.wait_minutes >= 2 + 15


# ---------------------------------------------------------------- traces handed to the measurement

def test_traces_are_collected_once_and_summarized_with_cost():
    from ed_admission.observability import tracing
    from ed_admission.observability.tracing import Claim, Trace
    tracing.take()
    trace = Trace("P-043", "test")
    trace.agent_call("patient", "P-043", "isolation", model="m", prompt_version="1", tokens_in=100,
                     tokens_out=20, cost_usd=0.01, claims=[Claim("C. difficile suspected", "לשלול C. diff")])
    tracing.collect(trace)
    taken = tracing.take()
    assert taken == [trace] and tracing.take() == []
    summary = metrics.trace_summary(taken)
    assert summary["groundedness"] == 1.0 and summary["cost_total_usd"] == 0.01 and summary["tokens_in"] == 100


def test_a_decider_without_traces_has_no_trace_metrics():
    assert metrics.trace_summary([]) is None
