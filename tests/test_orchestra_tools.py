"""The multi-agent system's tools (agents/orchestra_tools.py): each agent gets its own tools and one closing tool, the
record tools are the single agent's word for word, and the closing tools check in code before anything leaves the
agent. No model is called."""
from pathlib import Path

import pytest

from ed_admission.agents.orchestra_tools import DESCRIPTIONS, department_tools, judge_tools, patient_tools
from ed_admission.agents.single_agent_tools import DESCRIPTIONS as SINGLE_AGENT
from ed_admission.agents.single_agent_tools import single_agent_tools
from ed_admission.contracts.agent_outputs import AssessmentWithConfidence, WardSummary
from ed_admission.environment import load_template
from ed_admission.tools.toolbox import PermissionDenied, Toolbox

PROJECT = Path(__file__).resolve().parents[1]
WARD = "internal_medicine_a"
TB = "חשד ל-TB ריאתי עם חלל"                      # P-018, word for word
OXYGEN = "סט' 91% באוויר, הוחל O2 1L - 95%."


def engine(template="T19"):
    return load_template(PROJECT / "data" / "templates" / f"{template}.yaml", target_ward=WARD)


def by_name(tools):
    return {tool.name: tool for tool in tools}


def finding(quote, backs=(), confidence="high"):
    return {"claim": "from the record", "quote": quote, "confidence": confidence, "backs": list(backs)}


def assessment(**changes):
    return {"target_ward": "internal_medicine", "binding_request": False, "severe": True, "intensive_care": False,
            "observation": True, "isolation": True, "flags": [], "note": None,
            "findings": [finding(TB, ["isolation"]), finding(OXYGEN, ["observation"])], **changes}


# ---------------------------------------------------------------- the Patient Agent

def test_the_patient_agent_has_four_tools_and_one_closes():
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018"))
    assert list(tools) == ["read_patient_record", "compute_news2", "clinical_lists", "record_assessment"]
    assert [name for name, tool in tools.items() if tool.final] == ["record_assessment"]
    assert all(tool.strict for tool in tools.values())


def test_the_record_tools_are_the_single_agents_word_for_word():
    single = by_name(single_agent_tools(Toolbox("single_agent", engine()), ["P-018"]))
    multi = by_name(patient_tools(Toolbox("patient", engine()), "P-018"))
    for name in ("read_patient_record", "compute_news2", "clinical_lists"):
        assert DESCRIPTIONS[name] == SINGLE_AGENT[name]
        assert multi[name].description == single[name].description
        assert multi[name].input_schema == single[name].input_schema


def test_a_patient_agent_reads_its_own_patient_only():
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018"))
    assert tools["read_patient_record"].function(patient_id="P-018")["case_id"] == "P-018"
    with pytest.raises(ValueError, match="not the patient of this task"):
        tools["read_patient_record"].function(patient_id="P-001")


def test_the_assessment_is_accepted_with_its_quotes_grounded():
    kept = {}
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018", kept))
    result = tools["record_assessment"].function(**assessment())
    assert isinstance(result, AssessmentWithConfidence) and kept["assessment"] is result
    assert all(f.grounded for f in result.findings) and result.bed_type == "isolation"
    assert "patient_id" not in tools["record_assessment"].input_schema["properties"]      # bound to the task


def test_a_quote_not_in_the_record_goes_back_to_the_agent():
    kept = {}
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018", kept))
    made_up = assessment(findings=[finding(TB, ["isolation"]), finding("סטורציה 80%", ["observation"])])
    with pytest.raises(ValueError, match="quote_not_in_record"):
        tools["record_assessment"].function(**made_up)
    assert "assessment" not in kept                                   # nothing passes on


def test_a_need_without_a_finding_goes_back_to_the_agent():
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018"))
    with pytest.raises(ValueError, match="no finding backs severe, observation"):
        tools["record_assessment"].function(**assessment(findings=[finding(TB, ["isolation"])]))


def test_the_assessment_checks_the_ward_as_the_single_agents_does():
    tools = by_name(patient_tools(Toolbox("patient", engine()), "P-018"))
    with pytest.raises(ValueError, match="is not a ward"):
        tools["record_assessment"].function(**assessment(target_ward="the_roof"))


def test_the_patient_tools_need_a_patient_toolbox():
    with pytest.raises(ValueError):
        patient_tools(Toolbox("department", engine(), ward_id=WARD), "P-018")


# ---------------------------------------------------------------- the Department Agent

def department():
    return by_name(department_tools(Toolbox("department", engine(), ward_id=WARD)))


def test_the_department_agent_has_two_tools_and_one_closes():
    tools = department()
    assert list(tools) == ["department_view", "answer_ward"]
    assert [name for name, tool in tools.items() if tool.final] == ["answer_ward"]


def test_the_department_agent_sees_its_ward_and_its_notes_only():
    view = department()["department_view"].function()
    assert view["ward_id"] == WARD and view["notes"]                    # T19: the note about bed 1
    with pytest.raises(PermissionDenied):
        Toolbox("department", engine(), ward_id=WARD).read_patient_record("P-018")


def test_t19_a_bed_from_the_note_is_accepted():
    answer = department()["answer_ward"].function(answer="condition", bed_number=1, reason="freed after the round",
                                                  condition="a stable patient", asks="stable", source="note")
    assert isinstance(answer, WardSummary) and answer.ward_id == WARD and answer.source == "note"


def test_t19_the_same_bed_from_the_report_goes_back_to_the_agent():
    with pytest.raises(ValueError, match="bed_not_in_report"):
        department()["answer_ward"].function(answer="accept", bed_number=1, reason="free", source="report")


def test_a_bed_from_the_report_that_the_report_shows_free_is_accepted():
    answer = department()["answer_ward"].function(answer="accept", bed_number=11, reason="free", source="report")
    assert answer.bed_number == 11                                      # T19: observation bed 11 is free in the report


def test_a_bed_the_ward_does_not_have_goes_back_to_the_agent():
    with pytest.raises(ValueError, match="bed_not_in_ward"):
        department()["answer_ward"].function(answer="accept", bed_number=40, reason="free", source="note")


def test_a_bed_from_a_note_in_a_ward_without_notes_goes_back_to_the_agent():
    tools = by_name(department_tools(Toolbox("department", engine("T7"), ward_id=WARD)))
    assert not tools["department_view"].function()["notes"]
    with pytest.raises(ValueError, match="no_ward_note"):
        tools["answer_ward"].function(answer="accept", bed_number=1, reason="free", source="note")


def test_a_decline_needs_no_bed():
    answer = department()["answer_ward"].function(answer="decline", reason="no staff for an unstable patient")
    assert answer.bed_number is None and answer.source is None


# ---------------------------------------------------------------- the Judge Agent
def case_state(quote="TB"):
    """P-018 on T19: the Patient Agent found isolation and observation; internal medicine A gave bed 1 from its note."""
    from ed_admission.contracts.agent_outputs import CodeChecks, PatientTask, PlacementProposal, RubricItem
    from ed_admission.orchestration.state import CaseState
    found = AssessmentWithConfidence(**{**assessment(), "findings": [
        {"claim": "suspected TB", "quote": quote, "confidence": "medium", "backs": ["isolation"]},
        {"claim": "needs oxygen", "quote": OXYGEN, "confidence": "high", "backs": ["observation"]}]})
    bed_1 = WardSummary(ward_id=WARD, answer="condition", bed_number=1, reason="freed after the round",
                        condition="a stable patient", asks="stable", source="note")
    checks = CodeChecks(items=[RubricItem(number=number, passed=True, reason="checked") for number in (1, 3, 4)])
    return CaseState(task=PatientTask(patient_id="P-018", decided_at=0), assessment=found, ward_answers=[bed_1],
                     proposal=PlacementProposal(kind="place", ward_id=WARD, bed_number=1, rung=1,
                                                reason="internal medicine A gave bed 1", wards=[bed_1],
                                                code_checks=checks))


def judge(state):
    return by_name(judge_tools(Toolbox("judge", engine()), state))


def test_the_judge_reads_each_quote_inside_its_sentence():
    case = judge(case_state(quote="TB"))["read_case"].function()
    tb = case["findings"][0]
    assert tb["quote"] == "TB" and "לשלול TB" in tb["sentence"]          # cut short: the sentence shows "rule out"
    assert case["findings"][1]["sentence"] == OXYGEN


def test_the_judge_sees_the_notes_of_the_wards_that_answered_only():
    case = judge(case_state())["read_case"].function()
    assert [ward["ward_id"] for ward in case["wards"]] == [WARD]
    assert "מיטה רגילה 1 התפנתה" in case["wards"][0]["notes"][0]     # bed 1 "from a note" is in the note
    assert case["proposal"]["bed_number"] == 1 and [c["number"] for c in case["code_checks"]] == [1, 3, 4]


def test_the_case_file_has_no_record_and_no_patient_id():
    import json
    text = json.dumps(judge(case_state())["read_case"].function(), ensure_ascii=False)
    assert "P-018" not in text and "physician_note" not in text and "vitals" not in text


def test_only_the_judge_reads_the_case_and_only_after_a_proposal():
    with pytest.raises(PermissionDenied):
        Toolbox("patient", engine()).read_case(case_state())
    with pytest.raises(ValueError):
        judge_tools(Toolbox("patient", engine()), case_state())
    no_proposal = case_state().model_copy(update={"proposal": None})
    with pytest.raises(ValueError, match="a proposal"):
        judge(no_proposal)["read_case"].function()


def test_a_sentence_ends_at_a_full_stop_or_a_blank_line_not_at_a_line_break():
    from ed_admission.tools.patient_record import sentence_around
    assert sentence_around("P-018", "שיעול\nכחודש וחצי").startswith("בתשאול חוזר שיעול כחודש וחצי")
    assert sentence_around("P-018", "words that are not there") is None


def item(number, passed=True):
    return {"number": number, "passed": passed, "reason": "checked"}


def test_the_judge_agent_has_two_tools_and_the_verdict_closes():
    tools = judge(case_state())
    assert list(tools) == ["read_case", "give_verdict"]
    assert [name for name, tool in tools.items() if tool.final] == ["give_verdict"]


def test_a_valid_verdict_is_handed_in():
    verdict = judge(case_state())["give_verdict"].function(valid=True, items=[item(2), item(8), item(9)])
    assert verdict.valid and verdict.correction_goes_to is None


def test_a_misread_quote_sends_the_correction_to_the_patient_agent():
    verdict = judge(case_state())["give_verdict"].function(
        valid=False, items=[item(2, False), item(8), item(9)],
        correction="'TB' comes from 'rule out TB': suspected, not diagnosed")
    assert verdict.correction_goes_to == "patient"


def test_a_verdict_that_skips_an_item_goes_back_to_the_judge():
    with pytest.raises(ValueError, match="every one of the Judge's items"):
        judge(case_state())["give_verdict"].function(valid=True, items=[item(8), item(9)])


def test_only_the_judge_hands_in_a_verdict():
    from ed_admission.contracts.agent_outputs import JudgeVerdict
    verdict = JudgeVerdict(valid=True, items=[item(2), item(8), item(9)])
    with pytest.raises(PermissionDenied):
        Toolbox("department", engine(), ward_id=WARD).give_verdict(verdict)
