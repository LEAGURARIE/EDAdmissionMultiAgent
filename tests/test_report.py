"""The short report (evaluation/report.py): the goal in one table — safe, suitable, fast, and tracked once a decider
keeps traces — with the targets, and ✓ / ✗ for the judged decider."""
from ed_admission.evaluation import report


def _column(**changes):
    numbers = {"violations": 0, "need_met_rate": 1.0, "bed_not_available": 0, "match_rate": 0.9,
               "minutes_to_bed": 10.0, "still_waiting": 0}
    return {**numbers, **changes}


def test_the_report_shows_safe_suitable_fast_with_the_targets():
    text = report.render({"system": _column(), "today": _column(minutes_to_bed=20.0)}, judged="system")
    for words in ("Safe", "Suitable", "Fast", "violations", "needs met", "bed the ward can't give", "match",
                  "minutes to a bed", "less than today", "85%"):
        assert words in text
    assert "✗" not in text and text.count("✓") == 5
    assert "Tracked" not in text                         # no traces, no tracking rows


def test_a_slower_or_unsafe_system_is_marked():
    text = report.render({"system": _column(violations=2, minutes_to_bed=30.0), "today": _column(minutes_to_bed=20.0)},
                         judged="system")
    assert text.count("✗") == 2


def test_tracking_appears_once_a_decider_keeps_traces():
    text = report.render({"system": _column(complete_trace_rate=1.0, groundedness=0.9), "today": _column()},
                         judged="system")
    assert "Tracked" in text and "groundedness" in text and text.count("✗") == 2   # groundedness, and not faster


def test_patients_left_waiting_are_named_not_hidden_in_the_average():
    text = report.render({"first free bed": _column(still_waiting=12)})
    assert "first free bed: 12 patients still waiting" in text
