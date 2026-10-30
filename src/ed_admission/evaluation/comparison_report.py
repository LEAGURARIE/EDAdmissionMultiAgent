"""The comparison report — the single agent against the multi-agent system, in three columns: quality, latency and
cost (docs/design/comparison_protocol.md, sections 4, 5 and 7). scripts/compare_systems.py prints and saves it.

What it reads, from the run history (database/run_log.py):
  runs.csv         one row per run: the metrics, the baseline's on the same cases, the cost summary
  admissions.csv   one row per decision: for the stability of the decisions across repetitions
What it takes:
  - for each system, its latest 3 test runs (--set test), never a partial run — the 3 repetitions
  - for each system and each scripted day, its latest run of that day (the days run once per system)
  - the runs must have decided the same cases — across the repetitions and across the two systems — or the report
    is refused and says why
How it reads the result — decided in the protocol before any run:
  - each measure: its mean and range over the repetitions; a difference counts only when the ranges do not overlap
    (the worse value of the better system is still better than the best of the other — section 9 of the protocol)
  - the reading: safety first, then suitability, then latency and cost; one faster and the other cheaper is no
    winner; nothing that counts is a tie
  - the baseline's numbers come from the same runs: it ran on the same cases

Owner: Lea Gur-Arie
"""
import csv
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from ed_admission import config
from ed_admission.database import run_log

SYSTEMS = ("single_agent", "multi_agent")
REPETITIONS = 3
DAYS = ("busy_day", "evening_night", "weekend", "competing_for_a_bed")

# group, column in runs.csv, label, which way is better, the baseline's column, the charter's target
MEASURES = [
    ("quality: safe", "violations", "hard-rule violations", "lower", "baseline violations", "0"),
    ("quality: safe", "need_met_rate", "needs met or escalated", "higher", "baseline need_met_rate", "100%"),
    ("quality: safe", "bed_not_available", "beds the ward cannot give", "lower", "baseline bed_not_available", "0"),
    ("quality: suitable", "match_rate", "decisions that match the answer", "higher", "baseline match_rate", "85%"),
    ("quality: tracked", "complete_trace_rate", "complete traces", "higher", None, "100%"),
    ("quality: tracked", "groundedness", "claims backed by the record", "higher", None, "95%"),
    ("latency", "mean_seconds", "seconds per case, mean", "lower", "baseline mean_seconds", ""),
    ("latency", "seconds_p95", "seconds per case, 95th percentile", "lower", None, ""),
    ("cost", "cost_mean_usd", "USD per case, mean", "lower", None, ""),
    ("cost", "cost_median_usd", "USD per case, median", "lower", None, ""),
    ("cost", "cost_max_usd", "USD per case, maximum", "lower", None, ""),
    ("cost", "cost_total_usd", "USD per run", "lower", None, ""),
]
DAY_MEASURES = [("minutes to bed", "lower"), ("violations", "lower"), ("bed_not_available", "lower")]


class NotComparable(Exception):
    """The runs did not decide the same cases."""


@dataclass
class Spread:
    values: list[float]

    @property
    def mean(self) -> float:
        return mean(self.values)

    def text(self, digits: int = 3) -> str:
        if len(self.values) == 1:
            return f"{self.values[0]:.{digits}f}"
        return f"{self.mean:.{digits}f} ({min(self.values):.{digits}f}–{max(self.values):.{digits}f})"


def _read(name: str) -> list[dict]:
    path = config.outputs_dir() / name
    if not path.exists():
        return []
    with path.open(encoding="utf8", newline="") as file:
        return list(csv.DictReader(file))


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _complete(row: dict) -> bool:
    return str(row.get("partial_run", "")).lower() not in ("true", "1")


def latest_test_runs(runs: list[dict]) -> dict[str, list[dict]]:
    """Each system's latest REPETITIONS test runs, partial runs left out."""
    chosen = {}
    for system in SYSTEMS:
        mine = sorted((r for r in runs if r.get("system") == system and r.get("set") == "test" and _complete(r)),
                      key=lambda r: r["run_id"])
        chosen[system] = mine[-REPETITIONS:]
    return chosen


def _decisions(admissions: list[dict], run_id: str) -> dict[tuple, tuple]:
    """One run's decisions: (source, case, patient) -> (action, ward)."""
    return {(r["source"], r.get("case", ""), r["patient_id"]): (r.get("action", ""), r.get("ward_id", ""))
            for r in admissions if r["run_id"] == run_id and not r["source"].startswith("day:")}


def stability(admissions: list[dict], runs: list[dict]) -> float | None:
    """The share of cases decided the same — the same action and ward — in every repetition."""
    if len(runs) < 2:
        return None
    decided = [_decisions(admissions, r["run_id"]) for r in runs]
    cases = set.intersection(*(set(d) for d in decided))
    return sum(len({d[c] for d in decided}) == 1 for c in cases) / len(cases) if cases else None


def better(direction: str, a: Spread | None, b: Spread | None) -> int:
    """1: a is better beyond the spread, -1: b is, 0: a tie (the ranges overlap) or nothing to compare."""
    if a is None or b is None:
        return 0
    lo_a, hi_a, lo_b, hi_b = min(a.values), max(a.values), min(b.values), max(b.values)
    if direction == "lower":
        return 1 if hi_a < lo_b else -1 if hi_b < lo_a else 0
    return 1 if lo_a > hi_b else -1 if lo_b > hi_a else 0


def reading(rows: list[dict], measured: list[str]) -> str:
    """The protocol's reading, section 5: safety, then suitability, then latency and cost."""
    if len(measured) < 2:
        return f"Only {', '.join(measured) or 'no system'} measured on the test set — no comparison yet."
    first, second = SYSTEMS

    def wins(group_or_key):
        found = {r["winner"] for r in rows if (r["group"] == group_or_key or r["key"] == group_or_key)
                 and r["winner"]}
        return found

    safety = wins("quality: safe")
    if len(safety) == 2:
        return "Mixed on safety: each system is safer on a different measure — no winner."
    if safety:
        return f"{safety.pop()} is safer."
    suitable = wins("match_rate")
    if suitable:
        return f"As safe; {suitable.pop()} is more suitable."
    faster, cheaper = wins("mean_seconds"), wins("cost_mean_usd")
    if faster and cheaper and faster != cheaper:
        return "As safe and as suitable; one is faster and the other cheaper — no winner."
    if faster or cheaper:
        winner = (faster or cheaper).pop()
        return f"As safe and as suitable; {winner} is {'faster' if faster else 'cheaper'}."
    return "A tie: no difference larger than the spread between repetitions."


def build(admissions_per_month: int | None = None) -> dict:
    """The report: the runs it used, one row per measure, the days, the stability, and the reading."""
    runs, admissions = _read(run_log.RUNS), _read(run_log.ADMISSIONS)
    chosen = latest_test_runs(runs)
    measured = [s for s in SYSTEMS if chosen[s]]
    cases = {s: [set(_decisions(admissions, r["run_id"])) for r in chosen[s]] for s in measured}
    every = [c for s in measured for c in cases[s]]
    if every and any(c != every[0] for c in every):
        raise NotComparable("the test runs did not decide the same cases — every repetition of both systems must "
                            "run the same frozen test set")

    rows = []
    for group, key, label, direction, baseline_key, target in MEASURES:
        spreads = {}
        for system in measured:
            values = [v for v in (_number(r.get(key)) for r in chosen[system]) if v is not None]
            spreads[system] = Spread(values) if values else None
        baseline = next((_number(r.get(baseline_key)) for s in measured for r in chosen[s]), None) \
            if baseline_key else None
        verdict = better(direction, spreads.get(SYSTEMS[0]), spreads.get(SYSTEMS[1]))
        rows.append({"group": group, "key": key, "measure": label, "better": direction, "target": target,
                     **{s: spreads.get(s) for s in SYSTEMS}, "baseline": baseline,
                     "winner": SYSTEMS[0] if verdict == 1 else SYSTEMS[1] if verdict == -1 else ""})
    if admissions_per_month:
        for row in rows:
            if row["key"] == "cost_mean_usd":
                rows.append({"group": "cost", "key": "cost_month_usd", "measure":
                             f"USD per month, {admissions_per_month} admissions", "better": "lower", "target": "",
                             **{s: Spread([v * admissions_per_month for v in row[s].values]) if row[s] else None
                                for s in SYSTEMS}, "baseline": None, "winner": row["winner"]})
                break

    days = {}
    for day in DAYS:
        for system in SYSTEMS:
            mine = sorted((r for r in runs if r.get("system") == system and r.get("days") == day and _complete(r)),
                          key=lambda r: r["run_id"])
            if mine:
                latest = mine[-1]
                days.setdefault(day, {})[system] = {name: _number(latest.get(f"{day} {name}"))
                                                    for name, _ in DAY_MEASURES}
                days[day]["baseline minutes to bed"] = _number(latest.get(f"baseline {day} minutes to bed"))
                if day == "competing_for_a_bed":
                    days[day][system]["moments right"] = (f"{latest.get('competing moments right', '')} of "
                                                          f"{latest.get('competing moments as designed', '')}")
    return {"runs": {s: [r["run_id"] for r in chosen[s]] for s in SYSTEMS},
            "repetitions": {s: len(chosen[s]) for s in SYSTEMS},
            "stability": {s: stability(admissions, chosen[s]) for s in SYSTEMS},
            "rows": rows, "days": days, "reading": reading(rows, measured)}


# ============ PRINTED AND SAVED ============
def _cell(value) -> str:
    if value is None:
        return "—"
    return value.text() if isinstance(value, Spread) else f"{value:.3f}" if isinstance(value, float) else str(value)


def markdown(report: dict) -> str:
    names = {"single_agent": "Single agent", "multi_agent": "Multi-agent system"}
    lines = ["# Comparison report — the single agent against the multi-agent system", "",
             "Mean and range over the repetitions; a difference counts only when the ranges do not overlap "
             "(docs/design/comparison_protocol.md).", "",
             "| Runs | " + " | ".join(names.values()) + " |", "|---|---|---|",
             "| Test runs (repetitions) | " + " | ".join(f"{report['repetitions'][s]} of {REPETITIONS}"
                                                      for s in SYSTEMS) + " |",
             "| Stability: same decision in every repetition | "
             + " | ".join("—" if report["stability"][s] is None else f"{report['stability'][s]:.0%}" for s in SYSTEMS)
             + " |", "",
             "| Column | Measure | " + " | ".join(names.values()) + " | Baseline | Target | Better beyond the spread |",
             "|---|---|---|---|---|---|---|"]
    for row in report["rows"]:
        lines.append(f"| {row['group']} | {row['measure']} | {_cell(row['single_agent'])} | "
                     f"{_cell(row['multi_agent'])} | {_cell(row['baseline'])} | {row['target']} | "
                     f"{names.get(row['winner'], 'tie' if row['single_agent'] and row['multi_agent'] else '—')} |")
    if report["days"]:
        lines += ["", "## The scripted days — once per system", "",
                  "| Day | Measure | " + " | ".join(names.values()) + " | Baseline |", "|---|---|---|---|---|"]
        for day, by_system in report["days"].items():
            for name in [m for m, _ in DAY_MEASURES] + (["moments right"] if day == "competing_for_a_bed" else []):
                cells = [_cell(by_system.get(s, {}).get(name)) for s in SYSTEMS]
                base = _cell(by_system.get("baseline minutes to bed")) if name == "minutes to bed" else "—"
                lines.append(f"| {day} | {name} | {cells[0]} | {cells[1]} | {base} |")
    lines += ["", f"**Reading:** {report['reading']}"]
    return "\n".join(lines) + "\n"


def save(report: dict) -> tuple[Path, Path]:
    """comparison_report.csv (numbers) and comparison_report.md (to read), next to the run history."""
    folder = config.outputs_dir()
    md = folder / "comparison_report.md"
    md.write_text(markdown(report), encoding="utf8")
    path = folder / "comparison_report.csv"
    with path.open("w", encoding="utf8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["group", "measure", "better", "target", "baseline",
                         *[f"{s} {part}" for s in SYSTEMS for part in ("mean", "min", "max", "runs")], "winner"])
        for row in report["rows"]:
            cells = []
            for s in SYSTEMS:
                spread = row[s]
                cells += ([round(spread.mean, 6), min(spread.values), max(spread.values), len(spread.values)]
                          if spread else ["", "", "", 0])
            writer.writerow([row["group"], row["measure"], row["better"], row["target"],
                             "" if row["baseline"] is None else row["baseline"], *cells, row["winner"]])
    return path, md
