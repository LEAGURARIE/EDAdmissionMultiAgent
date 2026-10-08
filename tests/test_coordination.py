"""Every pair in both evaluation sets tests coordination between the agents: the ward reports alone lead to a
wrong answer — the right one needs what only the Department Agents know (their private ward notes).
The records still test reading (challenges and traps), so every pair tests both."""
import pytest

from ed_admission.environment.shifts import discharge_in_report_is_waited_for
from ed_admission.evaluation import answer_key_builder as builder

INPUTS = builder._read("clinical_inputs.yaml")["cases"]
PAIRS = builder._read("pairs.yaml")["pairs"] + builder._read("pairs_set2.yaml")["pairs"]


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: f"{p['case_id']}__{p['template']}")
def test_the_reports_alone_are_not_enough(pair):
    patient_type = INPUTS[pair["case_id"]]["patient_type"]
    right = builder.expected_decision(builder.true_ward_state(pair), patient_type, pair["target_ward"])
    report_only = builder.expected_decision(builder.true_ward_state(pair, with_notes=False), patient_type,
                                            pair["target_ward"])
    same_kind = (report_only.action, report_only.bed_type) == (right.action, right.bed_type)
    acceptable = right.acceptable_wards or [right.ward_id]
    assert not (same_kind and report_only.ward_id in acceptable), "the ward reports alone give a right answer"


@pytest.mark.parametrize("template", sorted({p["template"] for p in PAIRS}, key=lambda t: int(t[1:])))
def test_every_template_has_private_notes(template):
    text = (builder.TEMPLATES / f"{template}.yaml").read_text(encoding="utf8")
    assert "note_effects:" in text, template                 # the notes, and what they mean for the beds
    assert "notes:" in (builder.TEMPLATES / f"{template}.yaml").read_text(encoding="utf8"), template


def test_a_wait_for_what_only_the_ward_said_is_marked_ward_note():
    """discharge_today only for a discharge the report shows, on a weekday morning; any other wait is ward_note."""
    for pair in PAIRS:
        answer = builder._read(builder.answer_path(pair).name)
        wait_for = answer["expected"]["wait_for"]
        if not wait_for or wait_for["because"] == "cleaning":
            continue
        engine = builder.true_ward_state(pair, with_notes=False)
        report = engine.hospital.ward(pair["target_ward"])
        in_report = any(b.expected_discharge_at is not None for b in report.beds)
        waited = in_report and discharge_in_report_is_waited_for(engine.today(), engine.clock)
        assert wait_for["because"] == ("discharge_today" if waited else "ward_note"), pair
