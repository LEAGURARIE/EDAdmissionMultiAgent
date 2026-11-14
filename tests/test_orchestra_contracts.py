"""The contracts of the multi-agent system (contracts/agent_outputs.py, contracts/negotiation.py), its case state
(orchestration/state.py) and the Judge's rubric (config/judge_rubric.yaml).

Each contract: a valid value passes; an extra field, text over its limit, or a value that breaks the contract's rule
fails — so the handoff check in code can rely on it."""
import pytest
from pydantic import ValidationError

from ed_admission import config
from ed_admission.contracts.agent_outputs import (AssessmentWithConfidence, CodeChecks, Finding,
                                                  FindingWithConfidence, HandoffCheck, HandoffFailure, JudgeVerdict,
                                                  PatientAssessment, PatientTask, PlacementProposal,
                                                  RubricItem, WardSummary, WardTask)
from ed_admission.contracts.negotiation import Answer, WardAnswer, WardRequest
from ed_admission.orchestration.state import CaseState

REQUEST = WardRequest(bed_type="observation", severe=True, observation=True, isolation=False, target_ward="cardiology")
LIMIT = config.text_limits()
NEEDS = dict(target_ward="cardiology", severe=True, intensive_care=False, observation=True, isolation=False)


def item(number, passed=True):
    return RubricItem(number=number, passed=passed, reason="checked")


def finding(confidence="high", **changes):
    return FindingWithConfidence(**{"claim": "chest pain denied", "quote": "שוללת כאב חזה",
                                    "confidence": confidence, **changes})


# ---------------------------------------------------------------- the rubric

def test_the_rubric_has_eleven_items_and_says_who_checks_each():
    rubric = config.judge_rubric()
    assert sorted(rubric) == list(range(1, 12))
    assert {n for n, i in rubric.items() if i["checked_by"] == "judge"} == {2, 8, 9}
    assert {n for n, i in rubric.items() if not i["blocking"]} == {9, 10}


def test_a_rubric_item_must_exist_and_keeps_its_reason_short():
    with pytest.raises(ValidationError):
        RubricItem(number=12, passed=True, reason="no such item")
    with pytest.raises(ValidationError):
        RubricItem(number=1, passed=True, reason="x" * (LIMIT["reason"] + 1))


# ---------------------------------------------------------------- the Patient Agent

def test_the_patient_task_carries_the_id_and_the_minute_only():
    assert PatientTask(patient_id="P-001", decided_at=0).patient_id == "P-001"
    with pytest.raises(ValidationError):
        PatientTask(patient_id="P-001", decided_at=0, target_ward="cardiology")     # no hint
    with pytest.raises(ValidationError):
        PatientTask(patient_id="patient one", decided_at=0)


def test_a_finding_has_a_confidence_next_to_its_quote():
    assert finding("low").confidence == "low"
    assert finding().grounded is None                    # set by code in the handoff check, never by the agent
    with pytest.raises(ValidationError):
        FindingWithConfidence(claim="chest pain denied", quote="שוללת כאב חזה")       # no confidence
    with pytest.raises(ValidationError):
        finding("certain")
    with pytest.raises(ValidationError):
        finding(quote="")
    with pytest.raises(ValidationError):
        finding(quote="x" * (LIMIT["quote"] + 1))


def test_the_assessment_takes_no_extra_field_and_lists_low_confidence():
    assessment = AssessmentWithConfidence(**NEEDS, findings=[finding("high", backs=["observation"]), finding("low")])
    assert [f.confidence for f in assessment.low_confidence] == ["low"]
    assert assessment.bed_type == "observation"          # the single agent's rules, unchanged
    with pytest.raises(ValidationError):
        AssessmentWithConfidence(**NEEDS, ignore_the_rules=True)
    with pytest.raises(ValidationError):
        AssessmentWithConfidence(**NEEDS, findings=[{"claim": "x", "quote": "y"}])      # no confidence


def test_every_need_set_to_true_is_backed_by_a_finding():
    with pytest.raises(ValidationError, match="no finding backs severe, observation"):
        AssessmentWithConfidence(**NEEDS, findings=[finding("high")])                 # a finding, but for no need
    with pytest.raises(ValidationError, match="no finding backs isolation"):
        AssessmentWithConfidence(**{**NEEDS, "isolation": True}, findings=[finding(backs=["observation"])])
    with pytest.raises(ValidationError, match="no finding backs severe"):
        AssessmentWithConfidence(**{**NEEDS, "observation": False}, findings=[finding(backs=["isolation"])])
    with pytest.raises(ValidationError):
        finding(backs=["regular_bed"])                                               # closed values only


def test_a_finding_for_observation_or_intensive_care_also_backs_severe():
    AssessmentWithConfidence(**NEEDS, findings=[finding(backs=["observation"])])
    AssessmentWithConfidence(**{**NEEDS, "observation": False, "intensive_care": True},
                             findings=[finding(backs=["intensive_care"])])


def test_a_patient_with_no_need_needs_no_finding():
    AssessmentWithConfidence(target_ward="cardiology", severe=False, intensive_care=False, observation=False,
                             isolation=False)


def test_the_single_agents_contracts_are_unchanged():
    assert PatientAssessment(**NEEDS, extra_field="kept").model_extra == {"extra_field": "kept"}
    assert Finding(claim="x", quote="y").grounded is None


def test_code_answers_a_wards_question_stable_means_not_severe():
    regular = dict(target_ward="cardiology", severe=False, intensive_care=False, observation=False, isolation=False)
    assert AssessmentWithConfidence(**regular).stable is True
    assert AssessmentWithConfidence(**NEEDS, findings=[finding(backs=["observation"])]).stable is False
    icu = {**regular, "severe": True, "intensive_care": True}
    assert AssessmentWithConfidence(**icu, findings=[finding(backs=["intensive_care"])]).stable is False


# ---------------------------------------------------------------- the Department Agent

def test_a_ward_task_is_one_ward_and_the_needs_only():
    task = WardTask(ward_id="cardiology", request=REQUEST, patient_answer=True)
    assert task.request.bed_type == "observation"
    with pytest.raises(ValidationError):
        WardTask(ward_id="cardiology", request=REQUEST, patient_id="P-001")
    with pytest.raises(ValidationError):
        WardTask(ward_id="cardiology", request=REQUEST, judge_feedback="x" * (LIMIT["correction"] + 1))


def test_a_ward_answer_names_the_bed_when_it_gives_one():
    assert WardAnswer(answer=Answer.ACCEPT, bed_number=11, source="report", reason="free").bed_number == 11
    assert WardAnswer(answer=Answer.DECLINE, reason="no nurse").bed_number is None
    with pytest.raises(ValidationError, match="name the bed"):
        WardAnswer(answer=Answer.ACCEPT, reason="free")
    with pytest.raises(ValidationError, match="name the bed"):
        WardAnswer(answer=Answer.CONDITION, reason="after cleaning", condition="after cleaning")


def test_a_ward_asks_about_the_patient_only_in_a_closed_field_with_a_condition():
    answer = WardAnswer(answer=Answer.CONDITION, bed_number=4, source="report", reason="stable only", condition="a stable patient",
                        asks="stable")
    assert answer.asks == "stable"
    with pytest.raises(ValidationError):
        WardAnswer(answer=Answer.CONDITION, bed_number=4, source="report", reason="x", asks="approve without the shift manager")
    with pytest.raises(ValidationError, match="comes with a condition"):
        WardAnswer(answer=Answer.ACCEPT, bed_number=4, source="report", reason="x", asks="stable")


def test_a_ward_answer_keeps_its_words_short():
    with pytest.raises(ValidationError):
        WardAnswer(answer=Answer.DECLINE, reason="x" * (LIMIT["reason"] + 1))
    with pytest.raises(ValidationError):
        WardAnswer(answer=Answer.CONDITION, bed_number=4, source="report", reason="x", condition="x" * (LIMIT["condition"] + 1))
    with pytest.raises(ValidationError):
        WardAnswer(answer=Answer.CONDITION, bed_number=4, source="report", reason="x", available_in_minutes=-5)


def test_a_ward_summary_carries_the_answer_never_the_notes():
    assert set(WardSummary.model_fields) == {"ward_id", "answer", "bed_number", "reason", "condition",
                                              "available_in_minutes", "asks", "source"}
    with pytest.raises(ValidationError):
        WardSummary(ward_id="cardiology", answer=Answer.DECLINE, reason="x", notes=["one nurse tonight"])


def test_a_bed_comes_with_where_the_ward_knows_it_is_free():
    with pytest.raises(ValidationError, match="its source"):
        WardAnswer(answer=Answer.ACCEPT, bed_number=1, reason="free")                    # a bed, no source
    with pytest.raises(ValidationError, match="its source"):
        WardAnswer(answer=Answer.DECLINE, reason="full", source="note")                  # a source, no bed
    with pytest.raises(ValidationError):
        WardAnswer(answer=Answer.ACCEPT, bed_number=1, reason="free", source="a nurse told me")


def test_code_checks_the_bed_source_against_the_report_and_the_notes():
    from ed_admission.tools.negotiation import DepartmentView, bed_source_failure
    report = {"free_beds": [{"bed": 11}], "cleaning_beds": [{"bed": 3}], "expected_discharges_today": [{"bed": 5}]}
    with_note = DepartmentView("internal_medicine_a", report, ["bed 1 freed after the round, not updated yet"])
    no_note = DepartmentView("internal_medicine_a", report, [])

    def answer(bed, source):
        return WardAnswer(answer=Answer.ACCEPT, bed_number=bed, reason="x", source=source)

    for bed in (11, 3, 5):                                    # free, in cleaning, a discharge today: all in the report
        assert bed_source_failure(answer(bed, "report"), no_note) is None
    assert bed_source_failure(answer(1, "report"), with_note) == "bed_not_in_report"   # occupied in the report
    assert bed_source_failure(answer(1, "note"), with_note) is None                     # T19: the note freed bed 1
    assert bed_source_failure(answer(1, "note"), no_note) == "no_ward_note"
    assert bed_source_failure(WardAnswer(answer=Answer.DECLINE, reason="full"), no_note) is None


# ---------------------------------------------------------------- the checks in code

def test_a_handoff_check_passes_only_without_failures():
    assert HandoffCheck(stage="patient", passed=True).passed
    failed = HandoffCheck(stage="patient", passed=False, failures=[HandoffFailure.QUOTE_NOT_IN_RECORD],
                          ungrounded_quotes=["words that are not in the record"], attempt=2)
    assert failed.failures == [HandoffFailure.QUOTE_NOT_IN_RECORD]
    with pytest.raises(ValidationError):
        HandoffCheck(stage="patient", passed=True, failures=[HandoffFailure.SCHEMA])
    with pytest.raises(ValidationError):
        HandoffCheck(stage="patient", passed=False)
    with pytest.raises(ValidationError):
        HandoffCheck(stage="patient", passed=True, attempt=3)          # one retry only


def test_code_checks_hold_only_the_items_code_checks_and_block():
    assert CodeChecks(items=[item(1), item(10, passed=False)]).passed       # 10 is not blocking
    assert not CodeChecks(items=[item(1, passed=False)]).passed
    with pytest.raises(ValidationError, match="not checked by code"):
        CodeChecks(items=[item(2)])
    with pytest.raises(ValidationError, match="twice"):
        CodeChecks(items=[item(1), item(1)])


# ---------------------------------------------------------------- the proposal and the Judge Agent

def test_a_proposal_names_the_ward_and_the_bed():
    proposal = PlacementProposal(kind="place", ward_id="internal_medicine_a", bed_number=11, rung=2, reason="free",
                                 wards=[WardSummary(ward_id="cardiology", answer=Answer.DECLINE, reason="no nurse")],
                                 code_checks=CodeChecks(items=[item(1)]), low_confidence=[finding("low")])
    assert proposal.wards[0].ward_id == "cardiology" and proposal.low_confidence[0].confidence == "low"
    with pytest.raises(ValidationError, match="names the ward and the bed"):
        PlacementProposal(kind="place", rung=1, reason="somewhere")
    with pytest.raises(ValidationError, match="no ward and no bed"):
        PlacementProposal(kind="none", ward_id="cardiology", rung=1, reason="none")
    with pytest.raises(ValidationError):
        PlacementProposal(kind="question", rung=1, reason="cardiology takes only a stable patient")


def test_the_judge_agent_returns_only_its_own_items():
    assert JudgeVerdict(valid=True, items=[item(2), item(8), item(9, passed=False)]).valid   # 9 is not blocking
    with pytest.raises(ValidationError, match="not checked by judge"):
        JudgeVerdict(valid=True, items=[item(1)])
    with pytest.raises(ValidationError):
        JudgeVerdict(valid=True, items=[])


def test_a_verdict_answers_every_one_of_the_judges_items():
    with pytest.raises(ValidationError, match="every one of the Judge's items"):
        JudgeVerdict(valid=True, items=[item(8), item(9)])                     # item 2 skipped
    with pytest.raises(ValidationError, match="every one of the Judge's items"):
        JudgeVerdict(valid=True, items=[item(2)])


def test_the_correction_goes_to_whoever_made_the_mistake():
    fix = "'TB' comes from 'rule out TB': suspected, not diagnosed"
    misread = JudgeVerdict(valid=False, items=[item(2, False), item(8), item(9)], correction=fix)
    reason = JudgeVerdict(valid=False, items=[item(2), item(8, False), item(9)], correction=fix)
    both = JudgeVerdict(valid=False, items=[item(2, False), item(8, False), item(9)], correction=fix)
    assert (misread.correction_goes_to, reason.correction_goes_to, both.correction_goes_to) == (
        "patient", "wards", "patient")                                          # a new assessment redoes the wards
    assert JudgeVerdict(valid=True, items=[item(2), item(8), item(9, False)]).correction_goes_to is None
    task = PatientTask(patient_id="P-018", decided_at=0, judge_feedback=fix)
    assert task.judge_feedback == fix
    with pytest.raises(ValidationError):
        PatientTask(patient_id="P-018", decided_at=0, judge_feedback="x" * (LIMIT["correction"] + 1))


def test_a_verdict_that_is_not_valid_names_the_failure_and_the_correction():
    verdict = JudgeVerdict(valid=False, items=[item(2, passed=False), item(8), item(9)],
                           correction="'cannot be ruled out' read as no")
    assert not verdict.valid
    with pytest.raises(ValidationError, match="blocking items failed"):
        JudgeVerdict(valid=True, items=[item(2, passed=False), item(8), item(9)])
    with pytest.raises(ValidationError, match="name the blocking item"):
        JudgeVerdict(valid=False, items=[item(2), item(8), item(9)], correction="x")
    with pytest.raises(ValidationError, match="what to fix"):
        JudgeVerdict(valid=False, items=[item(2), item(8, passed=False), item(9)])
    with pytest.raises(ValidationError):
        JudgeVerdict(valid=False, items=[item(2), item(8, passed=False), item(9)],
                     correction="x" * (LIMIT["correction"] + 1))
    with pytest.raises(ValidationError):
        JudgeVerdict(valid=True, items=[item(2), item(8), item(9)], question="what backs 'no isolation'?")   # it reads the trace


# ---------------------------------------------------------------- the case state

def test_the_case_state_keeps_each_wards_latest_answer():
    state = CaseState(task=PatientTask(patient_id="P-001", decided_at=0), ward_answers=[
        WardSummary(ward_id="cardiology", answer=Answer.CONDITION, bed_number=4, source="report", reason="x", asks="stable"),
        WardSummary(ward_id="internal_medicine_a", answer=Answer.DECLINE, reason="full"),
        WardSummary(ward_id="cardiology", answer=Answer.ACCEPT, bed_number=4, source="report", reason="stable, so yes")])
    assert state.latest_ward_answers["cardiology"].answer == Answer.ACCEPT
    assert len(state.latest_ward_answers) == 2


def test_the_case_state_asks_every_ward_of_the_current_rung_from_the_ladder():
    state = CaseState(task=PatientTask(patient_id="P-001", decided_at=0), target="cardiology", rung=2)
    assert state.wards_on_rung() == ["internal_medicine_a", "internal_medicine_b"]   # the ladder, not the report
    assert CaseState(task=PatientTask(patient_id="P-001", decided_at=0), target="cardiology", rung=4).wards_on_rung() == []


def test_the_case_state_takes_no_extra_field():
    with pytest.raises(ValidationError):
        CaseState(task=PatientTask(patient_id="P-001", decided_at=0), record={"physician_note": "..."})


# ---------------------------------------------------------------- the text limits, one place

def test_the_text_limits_come_from_config():
    def longest(contract, field):
        return contract.model_json_schema()["properties"][field].get("maxLength") or \
            next(s["maxLength"] for s in contract.model_json_schema()["properties"][field]["anyOf"] if "maxLength" in s)
    assert set(LIMIT) == {"claim", "quote", "note", "reason", "condition", "correction"}
    assert longest(FindingWithConfidence, "claim") == LIMIT["claim"]
    assert longest(FindingWithConfidence, "quote") == LIMIT["quote"]
    assert longest(AssessmentWithConfidence, "note") == LIMIT["note"]
    assert longest(WardAnswer, "reason") == LIMIT["reason"]
    assert longest(WardAnswer, "condition") == LIMIT["condition"]
    assert longest(JudgeVerdict, "correction") == LIMIT["correction"]
