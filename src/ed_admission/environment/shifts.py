"""The shift and the weekend at a given moment — part of every ward report, so the decider can weigh them.
Times come from config/clinical_rules/placement_rules.yaml (shifts, weekend), never written here.

Owner: integrator
"""
from ed_admission import config
from ed_admission.environment.events import MINUTES_PER_DAY, to_minutes

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def shift(clock: int) -> str:
    """morning · evening · night, by the minute of the day."""
    starts = {name: to_minutes(time) for name, time in config.placement_rules()["shifts"].items()}
    minute = clock % MINUTES_PER_DAY
    if starts["morning"] <= minute < starts["evening"]:
        return "morning"
    if starts["evening"] <= minute < starts["night"]:
        return "evening"
    return "night"


def weekend(day: str | None, clock: int) -> bool:
    """From the weekend's start (Friday afternoon) to its end (Sunday morning)."""
    if day is None:
        return False
    rule = config.placement_rules()["weekend"]
    week_minute = lambda d, minute: DAYS.index(d) * MINUTES_PER_DAY + minute
    now = week_minute(day, clock % MINUTES_PER_DAY)
    start = week_minute(rule["from"]["day"], to_minutes(rule["from"]["time"]))
    end = week_minute(rule["to"]["day"], to_minutes(rule["to"]["time"]))
    return now >= start or now < end if start > end else start <= now < end


def discharge_in_report_is_waited_for(day: str | None, clock: int) -> bool:
    """The rule: only in a weekday morning shift."""
    return shift(clock) == "morning" and not weekend(day, clock)
