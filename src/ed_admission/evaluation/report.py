"""The short report: the system's goal in one table — safe, suitable, fast — next to the baseline (the Code
Coordinator: today's procedure, written as code) and the targets. Once a decider keeps traces, a fourth group: tracked.

  Safe      hard-rule violations (0) · observation / isolation needs met or escalated (100%) · a bed the ward
            cannot give (0)
  Suitable  match with the expected answer (85%)
  Fast      minutes from the admission decision to a bed, the three scripted days together (less than the baseline)
  Tracked   complete traces (100%) · groundedness (95%) — only for a decider that keeps traces

Used by scripts/run_metrics.py (a decider and the baseline) and scripts/run_baseline.py (the baseline alone).
Everything else — by rule, challenge, trap, template, each day, minutes saved, cost — is in --details.

Owner: Lea Gur-Arie
"""
from ed_admission import config
from ed_admission.evaluation import metrics

LOWER_IS_BETTER = {"unsafe_rate", "bed_not_available_rate"}


def _target(key: str) -> str:
    """The charter's target in words (config/evaluation.yaml); minutes to a bed: less than the baseline."""
    targets = config.evaluation()["targets"]
    return f"{targets[key]:.0%}" if key in targets else "less than the baseline"


# group, key, label, kind, target in words
ROWS = [
    ("Safe", "unsafe_rate", "hard rule broken", "rate", _target("unsafe_rate")),
    ("", "need_met_rate", "needs met", "rate", _target("need_met_rate")),
    ("", "bed_not_available_rate", "bed the ward can't give", "rate", _target("bed_not_available_rate")),
    ("Suitable", "match_rate", "match", "rate", _target("match_rate")),
    ("Fast", "minutes_to_bed", "minutes to a bed", "minutes", _target("minutes_to_bed")),
]
TRACKED = [
    ("Tracked", "complete_trace_rate", "complete traces", "rate", _target("complete_trace_rate")),
    ("", "groundedness", "groundedness", "rate", _target("groundedness")),
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
    numbers["case_desk"] = {key: [d.get(key) for d in days.values()]
                            for key in ("concurrent_cases", "max_minutes_in_queue", "conflicts")}
    if traces:
        numbers.update(complete_trace_rate=traces["complete_trace_rate"], groundedness=traces["groundedness"])
    return numbers


def moments_line(name: str, moments: list[dict]) -> str:
    """The contention day in one line: moments decided right, out of those the day reached as designed."""
    reached = [m for m in moments if m["as_designed"]]
    right = sum(m["right"] for m in reached)
    missed = len(moments) - len(reached)
    tail = f" · {missed} not reached as designed" if missed else ""
    return f"  {name}: competing for a bed — {right} of {len(reached)} moments right{tail}"


def _show(value, kind: str) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}" if kind == "rate" else f"{value:.0f}"


def _met(key: str, value, baseline) -> bool | None:
    if value is None:
        return None
    if key == "minutes_to_bed":
        return baseline is not None and value < baseline
    target = config.evaluation()["targets"][key]
    return value <= target if key in LOWER_IS_BETTER else value >= target


def render(columns: dict[str, dict], judged: str | None = None, baseline: str = "code coordinator") -> str:
    """The table. `columns`: name -> column(); `judged`: the column marked ✓ / ✗ against the targets."""
    names = list(columns)
    width = max(10, *(len(n) + 2 for n in names))
    lines = [f"  {'':<10}{'':<25}" + "".join(f"{n:>{width}}" for n in names) + "   target"]
    rows = ROWS + (TRACKED if any("groundedness" in c for c in columns.values()) else [])
    for group, key, label, kind, target in rows:
        cells = "".join(f"{_show(columns[n].get(key), kind):>{width}}" for n in names)
        mark = ""
        if judged is not None:
            met = _met(key, columns[judged].get(key), columns.get(baseline, {}).get(key))
            mark = "" if met is None else ("  ✓" if met else "  ✗")
        lines.append(f"  {group:<10}{label:<25}{cells}   {target}{mark}")
    for name in names:
        desk = columns[name].get("case_desk")
        if desk and desk["concurrent_cases"]:
            at_once = set(desk["concurrent_cases"])
            at_once = "no limit" if at_once == {None} else " / ".join(str(n) for n in sorted(at_once, key=str))
            longest = max((m for m in desk["max_minutes_in_queue"] if m is not None), default=0)
            lines.append(f"  {name}: {at_once} case(s) at a time · longest wait in the queue {longest:.0f} min · "
                         f"{sum(desk['conflicts'])} conflicts")
        if columns[name]["still_waiting"]:
            lines.append(f"  {name}: {columns[name]['still_waiting']} patients still waiting at the end of the days"
                         " — not counted in its minutes to a bed")
    return "\n".join(lines)
