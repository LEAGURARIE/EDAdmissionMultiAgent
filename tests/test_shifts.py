"""The shift and the weekend in the ward report, and the rule that uses them (placement_rules.yaml)."""
from ed_admission.environment.events import to_minutes
from ed_admission.environment.shifts import discharge_in_report_is_waited_for, shift, weekend


def test_shifts():
    assert [shift(to_minutes(t)) for t in ("06:59", "07:00", "14:59", "15:00", "22:59", "23:00")] ==            ["night", "morning", "morning", "evening", "evening", "night"]
    assert shift(to_minutes("02:00 +1d")) == "night"


def test_weekend_from_friday_afternoon_to_sunday_morning():
    assert not weekend("Friday", to_minutes("13:59"))
    assert weekend("Friday", to_minutes("14:00")) and weekend("Saturday", to_minutes("12:00"))
    assert weekend("Sunday", to_minutes("06:59")) and not weekend("Sunday", to_minutes("07:00"))
    assert not weekend("Wednesday", to_minutes("12:00"))


def test_a_reported_discharge_is_waited_for_only_on_a_weekday_morning():
    assert discharge_in_report_is_waited_for("Monday", to_minutes("10:00"))
    assert not discharge_in_report_is_waited_for("Monday", to_minutes("15:30"))
    assert not discharge_in_report_is_waited_for("Wednesday", to_minutes("02:00"))
    assert not discharge_in_report_is_waited_for("Saturday", to_minutes("10:00"))


def test_the_rule_is_in_the_ward_report():
    """likely_within_hour on every expected discharge: true on a weekday morning, false at night or at the weekend."""
    from pathlib import Path
    from ed_admission.environment import load_template
    templates = Path(__file__).resolve().parents[1] / "data" / "templates"
    for name, likely in (("T1", True), ("T20", False), ("T21", False), ("T22", True)):
        engine = load_template(templates / f"{name}.yaml", target_ward="internal_medicine_a")
        discharges = [d for w in engine.hospital.wards for d in engine.department_status(w)["expected_discharges_today"]]
        assert discharges and all(d["likely_within_hour"] is likely for d in discharges), name
