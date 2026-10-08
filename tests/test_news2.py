"""Tests for compute_news2 — every boundary of RCP 2017 Chart 1, and the mapping of config/clinical_rules/news2_levels.yaml."""
from pathlib import Path

import pytest

from ed_admission.contracts.patient_record import Vitals, load_record
from ed_admission.tools.news2 import compute_news2, level_of_care


EXAMPLE = Path(__file__).parent / "fixtures" / "example_record.yaml"

NORMAL = dict(respiratory_rate=16, oxygen_saturation=97, supplemental_oxygen=False, systolic_bp=125,
              heart_rate=75, consciousness="alert", temperature=37.0)        # every parameter scores 0


def news2(**changes):
    return compute_news2(Vitals(**(NORMAL | changes)))


def test_normal_vitals_score_zero():
    assert news2().total == 0 and level_of_care(news2()) == "regular"


@pytest.mark.parametrize("field, value, points", [
    ("respiratory_rate", 8, 3), ("respiratory_rate", 9, 1), ("respiratory_rate", 11, 1), ("respiratory_rate", 12, 0),
    ("respiratory_rate", 20, 0), ("respiratory_rate", 21, 2), ("respiratory_rate", 24, 2), ("respiratory_rate", 25, 3),
    ("oxygen_saturation", 91, 3), ("oxygen_saturation", 92, 2), ("oxygen_saturation", 93, 2),
    ("oxygen_saturation", 94, 1), ("oxygen_saturation", 95, 1), ("oxygen_saturation", 96, 0),
    ("systolic_bp", 90, 3), ("systolic_bp", 91, 2), ("systolic_bp", 100, 2), ("systolic_bp", 101, 1),
    ("systolic_bp", 110, 1), ("systolic_bp", 111, 0), ("systolic_bp", 219, 0), ("systolic_bp", 220, 3),
    ("heart_rate", 40, 3), ("heart_rate", 41, 1), ("heart_rate", 50, 1), ("heart_rate", 51, 0), ("heart_rate", 90, 0),
    ("heart_rate", 91, 1), ("heart_rate", 110, 1), ("heart_rate", 111, 2), ("heart_rate", 130, 2), ("heart_rate", 131, 3),
    ("temperature", 35.0, 3), ("temperature", 35.1, 1), ("temperature", 36.0, 1), ("temperature", 36.1, 0),
    ("temperature", 38.0, 0), ("temperature", 38.1, 1), ("temperature", 39.0, 1), ("temperature", 39.1, 2),
    ("consciousness", "confusion", 3), ("consciousness", "voice", 3), ("consciousness", "pain", 3),
    ("consciousness", "unresponsive", 3), ("supplemental_oxygen", True, 2),
])
def test_chart_1_boundaries(field, value, points):
    assert news2(**{field: value}).points[field] == points


@pytest.mark.parametrize("spo2, on_oxygen, points", [
    (83, False, 3), (84, False, 2), (85, False, 2), (86, False, 1), (87, False, 1), (88, False, 0), (92, False, 0),
    (93, False, 0), (99, False, 0),                       # ≥93 on air: 0
    (93, True, 1), (94, True, 1), (95, True, 2), (96, True, 2), (97, True, 3),
])
def test_spo2_scale_2(spo2, on_oxygen, points):
    result = news2(oxygen_saturation=spo2, supplemental_oxygen=on_oxygen, spo2_scale_2=True)
    assert result.points["oxygen_saturation"] == points


def test_example_record_scores_six():
    """The suspected-TB patient: respiratory rate 22 → 2, saturation 93 → 2, pulse 104 → 1, temperature 38.1 → 1."""
    result = compute_news2(load_record(EXAMPLE).vitals)
    assert result.total == 6 and level_of_care(result) == "observation"


def test_levels_of_care():
    assert level_of_care(news2(respiratory_rate=22, heart_rate=104)) == "regular"                  # 3 points
    assert level_of_care(news2(oxygen_saturation=91)) == "observation"                             # red score
    assert level_of_care(news2(respiratory_rate=22, oxygen_saturation=93, heart_rate=104)) == "observation"  # 5
    assert level_of_care(news2(respiratory_rate=25, oxygen_saturation=91, heart_rate=104)) == "intensive_care"  # 7


def test_missing_vital_is_never_assumed_normal():
    result = news2(oxygen_saturation=None)
    assert not result.complete and result.total is None and result.missing == ["oxygen_saturation"]
    assert level_of_care(result) is None                   # cannot decide — must be flagged


def test_known_points_can_still_decide():
    high = news2(respiratory_rate=25, consciousness="confusion", heart_rate=111, temperature=None)   # 8 known
    assert level_of_care(high) == "intensive_care"
    red = news2(oxygen_saturation=91, temperature=None)    # 3 known; the missing one adds at most 3 -> at most 6
    assert level_of_care(red) == "observation"             # observation for sure, intensive care impossible
    red_two_missing = news2(oxygen_saturation=91, temperature=None, heart_rate=None)   # could still reach 9
    assert level_of_care(red_two_missing) is None          # at least observation, maybe intensive care — undecided


def test_scale_2_above_92_needs_to_know_air_or_oxygen():
    result = news2(oxygen_saturation=95, supplemental_oxygen=None, spo2_scale_2=True)
    assert "oxygen_saturation" in result.missing and "supplemental_oxygen" in result.missing
