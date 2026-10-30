"""The single agent's tools (agents/single_agent_tools.py): the nine tools the model sees, their schemas and
descriptions, and what each one does in code — the order, the case's patients only, the needs from the agent's own
assessment, a bed given once. No model is called."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from ed_admission import config
from ed_admission.environment import load_template
from ed_admission.agents.single_agent_tools import DESCRIPTIONS, single_agent_tools
from ed_admission.observability.tracing import Trace
from ed_admission.tools.decide import RuleViolation
from ed_admission.tools.toolbox import Toolbox

PROJECT = Path(__file__).resolve().parents[1]
WARD = "internal_medicine_a"
NAMES = ["read_patient_record", "compute_news2", "clinical_lists", "record_assessment", "all_department_status",
         "rank_placements", "department_view", "decide", "escalate_to_human"]


def case(patients=("P-001",), template="T1"):
    """The tools of one case on a template (T1 by default)."""
    engine = load_template(PROJECT / "data" / "templates" / f"{template}.yaml", target_ward=WARD)
    trace = Trace(case_id=patients[0], decider="single_agent")
    toolbox = Toolbox("single_agent", engine, trace=trace)
    return {tool.name: tool for tool in single_agent_tools(toolbox, list(patients))}, trace


def assess(tools, patient_id="P-001", **changes):
    needs = {"patient_id": patient_id, "target_ward": WARD, "binding_request": False, "severe": False,
             "intensive_care": False, "observation": False, "isolation": False,
             "findings": [{"claim": "takes amlodipine", "quote": "amlodipine 5mg"}]}
    return tools["record_assessment"].function(**{**needs, **changes})


# ---------- what the model sees ----------
def test_nine_tools_strict_each_described_in_four_parts():
    tools, _ = case()
    assert list(tools) == NAMES
    for tool in tools.values():
        assert tool.schema()["strict"] is True and not tool.final
        for part in ("What:", "When:", "When not:", "How:"):
            assert part in tool.description, (tool.name, part)


def test_every_schema_is_closed_and_keeps_only_what_the_api_keeps():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
            assert not {"minLength", "pattern", "minimum", "maximum"} & set(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    tools, _ = case()
    for tool in tools.values():
        walk(tool.input_schema)


def test_the_news2_levels_in_the_description_come_from_the_config():
    levels = config.news2_levels()
    assert f"{levels['intensive_care_from']} or more: intensive care" in DESCRIPTIONS["compute_news2"]
    assert f"{levels['single_parameter_points']} points in one sign" in DESCRIPTIONS["compute_news2"]


def test_a_wrong_argument_is_refused_before_anything_runs():
    tools, _ = case()
    with pytest.raises(ValidationError):
        tools["read_patient_record"].function(patient_id="P-001", ward="cardiology")
    with pytest.raises(ValidationError, match="not a ward"):
        assess(tools, target_ward="the roof")


# ---------- the order and the case ----------
def test_no_bed_search_before_the_assessment():
    tools, _ = case()
    with pytest.raises(ValueError, match="record_assessment"):
        tools["rank_placements"].function(patient_id="P-001")


def test_only_the_patients_of_the_case():
    tools, _ = case(patients=("P-001",))
    with pytest.raises(ValueError, match="not a patient of this case"):
        tools["read_patient_record"].function(patient_id="P-002")


# ---------- the assessment ----------
def test_the_assessment_is_kept_its_quotes_checked_and_traced():
    tools, trace = case()
    result = assess(tools, findings=[{"claim": "takes amlodipine", "quote": "amlodipine 5mg"},
                                     {"claim": "invented", "quote": "a sentence no record has"}])
    assert result["recorded"] == "P-001" and result["bed_type"] == "regular"
    assert result["quotes_not_found_in_the_record"] == ["a sentence no record has"]
    assert [c.quote for c in trace.claims] == ["amlodipine 5mg", "a sentence no record has"]   # both measured


def test_the_last_assessment_counts():
    tools, _ = case()
    assess(tools)
    assert assess(tools, severe=True, observation=True)["bed_type"] == "observation"


# ---------- the wards: the needs come from the assessment ----------
def test_the_ranking_searches_for_the_bed_the_assessment_needs():
    tools, _ = case()
    assess(tools, severe=True, observation=True)
    options = tools["rank_placements"].function(patient_id="P-001")["options"]
    beds = [o for o in options if o["action"] in ("place", "temporary_place")]
    assert beds and all(o["bed_type"] == "observation" for o in beds)


def test_the_agent_reads_any_wards_report_and_notes_itself():
    tools, trace = case(template="T13")                                  # a ward with notes
    seen = {ward: tools["department_view"].function(ward_id=ward) for ward in ("internal_medicine_a", "cardiology")}
    assert all(set(view) == {"ward_id", "report", "notes"} for view in seen.values())
    assert any(view["notes"] for view in seen.values())                  # the notes are there, as text
    assert "note_effects" not in str(seen)                               # what a note means is never handed over
    assert [s.name for s in trace.steps] == ["department_view", "department_view"]


def test_an_unknown_ward_is_refused():
    tools, _ = case()
    with pytest.raises(ValueError, match="not a ward"):
        tools["department_view"].function(ward_id="the roof")


# ---------- the decision ----------
def first_bed(tools, patient_id):
    option = next(o for o in tools["rank_placements"].function(patient_id=patient_id)["options"]
                  if o["action"] == "place")
    return option["ward_id"], option["bed_number"]


def test_a_bed_given_in_the_case_is_not_offered_to_the_next_patient():
    tools, _ = case(patients=("P-001", "P-002"))
    assess(tools, "P-001")
    ward, bed = first_bed(tools, "P-001")
    decision = tools["decide"].function(patient_id="P-001", action="place", target_ward=WARD, ward_id=ward,
                                        bed_number=bed, reason="a free bed in the target ward")
    assert decision.bed_number == bed
    assess(tools, "P-002", findings=[])
    assert first_bed(tools, "P-002") != (ward, bed)


def test_a_decision_that_lowers_the_need_is_refused_with_the_reasons():
    tools, _ = case()
    assess(tools)
    ward, bed = first_bed(tools, "P-001")
    assess(tools, severe=True, observation=True)                                   # now the patient needs more
    with pytest.raises(RuleViolation):
        tools["decide"].function(patient_id="P-001", action="place", target_ward=WARD, ward_id=ward, bed_number=bed,
                                 reason="a regular bed")


def test_an_escalation_keeps_every_flag_of_the_assessment():
    tools, _ = case()
    assess(tools, flags=["missing_vital_sign"])
    decision = tools["escalate_to_human"].function(patient_id="P-001", reason="no bed on the whole ladder")
    assert decision.action.value == "escalate" and [f.value for f in decision.flags] == ["missing_vital_sign"]


def test_an_empty_reason_is_refused():
    tools, _ = case()
    assess(tools)
    with pytest.raises(ValidationError):
        tools["escalate_to_human"].function(patient_id="P-001", reason="")
