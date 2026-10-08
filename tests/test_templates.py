"""Tests for ward-state templates and case mode. No agents — the policies here are small stubs."""
from pathlib import Path

import pytest

from ed_admission import config
from ed_admission.environment import BedStatus, Decision, TemplateError, load_template

PROJECT = Path(__file__).resolve().parents[1]
HOSPITAL = PROJECT / "config" / "hospital.yaml"
TEMPLATES = PROJECT / "data" / "templates"


def report(engine, ward_id):
    return engine.department_status(ward_id)


def test_relative_template_follows_the_target_wards_ladder():
    engine = load_template(TEMPLATES / "T5.yaml", HOSPITAL, target_ward="cardiology")
    assert (engine.day, report(engine, "cardiology")["time"]) == ("Tuesday", "13:00")

    cardiology = report(engine, "cardiology")                       # level 1
    assert cardiology["free_beds"] == [{"bed": 13, "bed_type": "isolation"}]
    assert cardiology["cleaning_beds"] == [{"bed": 1, "bed_type": "regular", "ready_about": "13:30"}]

    for ward_id in ("internal_medicine_a", "internal_medicine_b"):  # both on level 2 for cardiology
        assert report(engine, ward_id)["regular"]["free"] == 1
        assert report(engine, ward_id)["observation"]["free"] == 1

    surgery = report(engine, "general_surgery")                     # level 3 -> default
    assert (surgery["regular"]["free"], surgery["observation"]["free"], surgery["isolation"]["free"]) == (2, 1, 1)
    assert report(engine, "intensive_care")["intensive_care"]["free"] == 1


def test_same_relative_template_for_another_target_ward():
    engine = load_template(TEMPLATES / "T5.yaml", HOSPITAL, target_ward="internal_medicine_a")
    assert report(engine, "internal_medicine_a")["cleaning_beds"] != []            # level 1
    assert report(engine, "internal_medicine_b")["regular"]["free"] == 1           # level 2
    assert report(engine, "cardiology")["regular"]["free"] == 2                    # level 3 -> default
    assert report(engine, "cardiology")["cleaning_beds"] == []


def test_fixed_template_discharges_today_and_held_bed():
    engine = load_template(TEMPLATES / "T7.yaml", HOSPITAL)
    cardiology = report(engine, "cardiology")
    assert cardiology["free_beds"] == []
    assert cardiology["expected_discharges_today"] == [                # Wednesday 12:00: a weekday morning shift
        {"bed": 1, "bed_type": "regular", "likely_within_hour": True},
        {"bed": 11, "bed_type": "observation", "likely_within_hour": True}]
    medicine_b = report(engine, "internal_medicine_b")
    assert medicine_b["regular"]["held"] == 1 and medicine_b["regular"]["free"] == 2
    assert "not-from-the-emergency-department" not in str(medicine_b)              # never says who holds it


def test_every_other_bed_is_occupied():
    engine = load_template(TEMPLATES / "T7.yaml", HOSPITAL)
    surgery = engine.hospital.ward("general_surgery")
    assert all(bed.status == BedStatus.OCCUPIED for bed in surgery.beds)


def test_relative_template_needs_a_target_ward():
    with pytest.raises(TemplateError):
        load_template(TEMPLATES / "T5.yaml", HOSPITAL)


def test_template_cannot_ask_for_more_beds_than_a_ward_has(tmp_path):
    too_many = tmp_path / "too_many.yaml"
    too_many.write_text('id: X\nname: x\nday: Monday\ntime: "10:00"\nrelative_to: none\n'
                        'wards:\n  cardiology: {observation_free: 3}\n', encoding="utf8")
    with pytest.raises(TemplateError):
        load_template(too_many, HOSPITAL)


def test_case_mode_returns_one_decision_and_changes_nothing():
    engine = load_template(TEMPLATES / "T5.yaml", HOSPITAL, target_ward="cardiology")
    before = engine.hospital.model_dump()

    def wait_for_cleaning(engine, event, waiting):
        return [Decision.wait(p.patient_id, "wait for cardiology bed 1, ready about 13:30") for p in waiting]

    decision = engine.decide_case("P-012", wait_for_cleaning)
    assert decision.patient_id == "P-012" and decision.action.value == "wait"
    assert engine.clock == 13 * 60                                                  # the clock did not move
    after = engine.hospital.model_dump()
    after["emergency_queue"] = before["emergency_queue"]
    assert after == before                                                          # no bed changed


# ---------------------------------------------------------------------- all twelve templates

ALL = [f"T{n}" for n in range(1, 13)]
TARGET_WARDS = ["internal_medicine_a", "internal_medicine_b", "cardiology", "general_surgery"]
REGULAR_WARDS = TARGET_WARDS


def free(engine, ward_id, bed_type):
    return report(engine, ward_id)[bed_type]["free"]


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_every_template_loads_for_every_target_ward(name, target_ward):
    engine = load_template(TEMPLATES / f"{name}.yaml", HOSPITAL, target_ward=target_ward)
    assert engine.day and report(engine, target_ward)["day"] == engine.day


@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_T2_target_ward_has_only_an_observation_bed(target_ward):
    engine = load_template(TEMPLATES / "T2.yaml", HOSPITAL, target_ward=target_ward)
    target = report(engine, target_ward)
    assert target["free_beds"] == [{"bed": 11, "bed_type": "observation"}]
    assert target["expected_discharges_today"] == []


@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_T3_no_observation_bed_anywhere_but_target_isolation_room_free(target_ward):
    engine = load_template(TEMPLATES / "T3.yaml", HOSPITAL, target_ward=target_ward)
    assert all(free(engine, w, "observation") == 0 for w in REGULAR_WARDS)
    assert free(engine, target_ward, "isolation") == 1
    assert all(free(engine, w, "isolation") == 0 for w in REGULAR_WARDS if w != target_ward)


@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_T4_target_has_both_observation_and_isolation(target_ward):
    engine = load_template(TEMPLATES / "T4.yaml", HOSPITAL, target_ward=target_ward)
    assert free(engine, target_ward, "observation") == 1 and free(engine, target_ward, "isolation") == 1


@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_T6_target_has_held_beds_next_to_free_ones(target_ward):
    engine = load_template(TEMPLATES / "T6.yaml", HOSPITAL, target_ward=target_ward)
    target = report(engine, target_ward)
    assert (target["regular"]["held"], target["observation"]["held"]) == (1, 1)
    assert (target["regular"]["free"], target["observation"]["free"]) == (1, 1)


@pytest.mark.parametrize("target_ward", TARGET_WARDS)
def test_T8_saturday_room_only_on_level_2(target_ward):
    engine = load_template(TEMPLATES / "T8.yaml", HOSPITAL, target_ward=target_ward)
    assert engine.day == "Saturday"
    level_2 = config.ladders()[target_ward][1]
    for ward_id in REGULAR_WARDS:
        has_room = free(engine, ward_id, "regular") + free(engine, ward_id, "observation") > 0
        assert has_room == (ward_id in level_2)


def test_T9_internal_medicine_full_at_night():
    engine = load_template(TEMPLATES / "T9.yaml", HOSPITAL)
    assert report(engine, "cardiology")["time"] == "23:00"
    for ward_id in ("internal_medicine_a", "internal_medicine_b"):
        assert report(engine, ward_id)["free_beds"] == [] and report(engine, ward_id)["expected_discharges_today"] == []


def test_T10_only_general_surgery_isolation_room_is_free():
    engine = load_template(TEMPLATES / "T10.yaml", HOSPITAL)
    free_rooms = [w for w in [*REGULAR_WARDS, "intensive_care"] if free(engine, w, "isolation")]
    assert free_rooms == ["general_surgery"]


def test_T11_intensive_care_full():
    engine = load_template(TEMPLATES / "T11.yaml", HOSPITAL)
    assert free(engine, "intensive_care", "intensive_care") == 0 and free(engine, "intensive_care", "isolation") == 0
    assert report(engine, "internal_medicine_b")["observation"]["held"] == 1


def test_T12_no_observation_and_no_isolation_anywhere():
    engine = load_template(TEMPLATES / "T12.yaml", HOSPITAL)
    assert all(free(engine, w, "observation") == 0 and free(engine, w, "isolation") == 0 for w in REGULAR_WARDS)
    assert free(engine, "intensive_care", "isolation") == 0


def test_ward_notes_are_private(tmp_path):
    """Notes stay with the ward: readable through ward_notes, never in the ward report."""
    template = tmp_path / "with_notes.yaml"
    template.write_text('id: X\nname: x\nday: Monday\ntime: "22:00"\nrelative_to: none\n'
                        'wards:\n  cardiology:\n    observation_free: 1\n'
                        '    notes: ["one nurse on the observation beds tonight"]\n', encoding="utf8")
    engine = load_template(template)
    assert engine.ward_notes("cardiology") == ["one nurse on the observation beds tonight"]
    assert "nurse" not in str(engine.department_status("cardiology"))
    assert engine.ward_notes("internal_medicine_a") == []
