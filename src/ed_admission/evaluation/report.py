"""The short report: the system's goal in one table — safe, suitable, fast — next to today's procedure (the
simulated coordinator) and the targets. Once a decider keeps traces, a fourth group: tracked.

  Safe      hard-rule violations (0) · observation / isolation needs met or escalated (100%) · a bed the ward
            cannot give (0)
  Suitable  match with the expected answer (85%)
  Fast      minutes from the admission decision to a bed, the three scripted days together (less than today)
  Tracked   complete traces (100%) · groundedness (95%) — only for a decider that keeps traces

Used by scripts/run_metrics.py (a decider and today) and scripts/run_baselines.py (the three baselines).
Everything else — by rule, challenge, trap, template, each day, minutes saved, cost — is in --details.

Owner: Lea (integrator)
"""
from ed_admission.evaluation import metrics

# group, key, label, kind, target in words
ROWS = [
    ("Safe", "violations", "violations", "count", "0"),
    ("", "need_met_rate", "needs met", "rate", "100%"),
    ("", "bed_not_available", "bed the ward can't give", "count", "0"),
    ("Suitable", "match_rate", "match", "rate", "85%"),
    ("Fast", "minutes_to_bed", "minutes to a bed", "minutes", "less than today"),
]
TRACKED = [
    ("Tracked", "complete_trace_rate", "complete traces", "rate", "100%"),
    ("", "groundedness", "groundedness", "rate", "95%"),
]


def column(scored: list, days: dict[str, dict], traces: dict | None = None) -> dict:
    """One decider's numbers for the report: its scored decisions, its scripted days (metrics.score_day), and its
    trace summary (metrics.trace_summary) when it keeps traces."""
    summary = metrics.summarize(scored)
    placed = sum(d["placed"] for d in days.values())
    minutes = sum((d["mean_minutes_to_bed"] or 0) * d["placed"] for d in days.values())
    numbers = {key: summary[key] for _, key, *_ in ROWS if key in summary}
    numbers["minutes_to_bed"] = minutes / placed if placed else None
    numbers["still_waiting"] = sum(len(d["still_waiting"]) for d in days.values())
    if traces:
        numbers.update(complete_trace_rate=traces["complete_trace_rate"], groundedness=traces["groundedness"])
    return numbers


def _show(value, kind: str) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" if kind == "rate" else f"{value:.0f}"


def _met(key: str, value, today) -> bool | None:
    if value is None:
        return None
    if key == "minutes_to_bed":
        return today is not None and value < today
    if key in ("violations", "bed_not_available"):
        return value == 0
    return value >= {"need_met_rate": 1.0, "match_rate": 0.85, "complete_trace_rate": 1.0, "groundedness": 0.95}[key]


def render(columns: dict[str, dict], judged: str | None = None, today: str = "today") -> str:
    """The table. `columns`: name -> column(); `judged`: the column marked ✓ / ✗ against the targets."""
    names = list(columns)
    width = max(10, *(len(n) + 2 for n in names))
    lines = [f"  {'':<10}{'':<25}" + "".join(f"{n:>{width}}" for n in names) + "   target"]
    rows = ROWS + (TRACKED if any("groundedness" in c for c in columns.values()) else [])
    for group, key, label, kind, target in rows:
        cells = "".join(f"{_show(columns[n].get(key), kind):>{width}}" for n in names)
        mark = ""
        if judged is not None:
            met = _met(key, columns[judged].get(key), columns.get(today, {}).get(key))
            mark = "" if met is None else ("  ✓" if met else "  ✗")
        lines.append(f"  {group:<10}{label:<25}{cells}   {target}{mark}")
    for name in names:
        if columns[name]["still_waiting"]:
            lines.append(f"  {name}: {columns[name]['still_waiting']} patients still waiting at the end of the days"
                         " — not counted in its minutes to a bed")
    return "\n".join(lines)
