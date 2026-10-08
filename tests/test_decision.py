"""Tests for the decision contract (src/ed_admission/environment/decision.py)."""
import pytest
from pydantic import ValidationError

from ed_admission.environment import Decision
from ed_admission.environment.decision import Action, Flag


def test_place_is_ladder_level_1_and_needs_no_typed_reason():
    decision = Decision.place("P-007", "cardiology", 11)
    assert (decision.ladder_level, decision.needs_typed_reason) == (1, False)


@pytest.mark.parametrize("target, ward, level, typed_reason", [
    ("internal_medicine_a", "internal_medicine_b", 2, False),
    ("internal_medicine_a", "cardiology", 3, True),
    ("internal_medicine_a", "general_surgery", 4, True),
    ("cardiology", "internal_medicine_b", 2, False),          # both internal medicine wards are level 2 for cardiology
])
def test_temporary_place_level_is_computed_from_the_ladder(target, ward, level, typed_reason):
    decision = Decision.temporary_place("P-041", ward, 4, awaiting_transfer_to=target)
    assert (decision.ladder_level, decision.needs_typed_reason) == (level, typed_reason)


def test_temporary_place_in_the_target_ward_itself_is_rejected():
    with pytest.raises(ValidationError):
        Decision.temporary_place("P-041", "cardiology", 4, awaiting_transfer_to="cardiology")


def test_temporary_place_off_the_ladder_is_rejected():
    with pytest.raises(ValidationError):
        Decision.temporary_place("P-041", "intensive_care", 2, awaiting_transfer_to="cardiology")


def test_wait_for_a_known_bed():
    decision = Decision.wait("P-021", "not severe", ward_id="internal_medicine_a", because="cleaning", bed_number=1)
    assert decision.wait_for.because == "cleaning" and decision.ladder_level is None


def test_escalate_with_a_flag():
    decision = Decision.escalate("P-010", "injection in the note", flags=[Flag.PROMPT_INJECTION])
    assert decision.action == Action.ESCALATE and decision.flags == [Flag.PROMPT_INJECTION]


@pytest.mark.parametrize("fields", [
    {"action": "place"},                                                     # no bed
    {"action": "wait", "ward_id": "cardiology", "bed_number": 3},           # wait must not name a bed
    {"action": "place", "ward_id": "cardiology", "bed_number": 3, "awaiting_transfer_to": "cardiology"},
    {"action": "escalate", "flags": ["not_a_flag"]},                         # flags come from a closed list
    {"action": "escalate", "approved": True},                                # unknown field
])
def test_inconsistent_decisions_are_rejected(fields):
    with pytest.raises(ValidationError):
        Decision(patient_id="P-001", **fields)
