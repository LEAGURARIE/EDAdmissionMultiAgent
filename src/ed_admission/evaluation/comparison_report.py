"""The comparison report — the single agent against the multi-agent system, in three columns: quality, latency and
cost (docs/design/comparison_protocol.md, sections 4, 5 and 7). scripts/compare_systems.py prints and saves it.

What it reads, from the run history (database/run_log.py):
  runs.csv         one row per run: the metrics, the baseline's on the same cases, the cost summary
  admissions.csv   one row per decision: for the stability of the decisions across repetitions
What it takes:
  - for each system, its latest 3 test runs (--set test), never a partial run — the 3 repetitions
  - never a side check: a run whose label starts with one of the side checks (config/evaluation.yaml — the Opus arm)
    asks where a gap comes from, it is not the system compared (comparison protocol, section 9). The side checks are
    shown apart, each against the frozen system — the version of its chosen test runs — on the same set
  - for each system and each scripted day, its latest run of that day (the days run once per system)
  - the days in two parts: the three days of a shift, for the minutes to a bed — day by day and pooled over every
    patient placed — and the contention day apart: its moments decided right and its safety, never its minutes
    (two patients and one bed: one of them waits by design, and a system that misses the need waits less)
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
REPETITIONS = config.evaluation()["repetitions"]                # config/evaluation.yaml
SHIFT_DAYS = ("busy_day", "evening_night", "weekend")        # the minutes to a bed
CONTENTION_DAY = "competing_for_a_bed"                       # shown apart: moments and safety, not minutes
SIDE_CHECKS = {prefix: (check["name"], check["changed"])            # label start -> name, what it changed
               for prefix, check in config.evaluation()["side_checks"].items()}
SIDE_MEASURES = [("match_rate", "match", "higher"), ("violations", "hard-rule violations", "lower"),
                 ("escalations", "escalations", "lower"), ("cost_mean_usd", "USD per case", "lower")]

def _target(key: str) -> str:
    """The charter's target in words (config/evaluation.yaml): a rate in percent, a count as it is."""
    targets = config.evaluation()["targets"]
    if key not in targets:
        return ""
    return f"{targets[key]:.0%}" if isinstance(targets[key], float) else str(targets[key])


# group, column in runs.csv, label, which way is better, the baseline's column, the charter's target
MEASURES = [
    ("quality: safe", "violations", "hard-rule violations", "lower", "baseline violations", _target("violations")),
    ("quality: safe", "need_met_rate", "needs met or escalated", "higher", "baseline need_met_rate", _target("need_met_rate")),
    ("quality: safe", "bed_not_available", "beds the ward cannot give", "lower", "baseline bed_not_available", _target("bed_not_available")),
    ("quality: suitable", "match_rate", "decisions that match the answer", "higher", "baseline match_rate", _target("match_rate")),
    ("quality: tracked", "complete_trace_rate", "complete traces", "higher", None, _target("complete_trace_rate")),
    ("quality: tracked", "groundedness", "claims backed by the record", "higher", None, _target("groundedness")),
    ("latency", "mean_seconds", "seconds per case, mean", "lower", "baseline mean_seconds", ""),
    ("latency", "seconds_p95", "seconds per case, 95th percentile", "lower", None, ""),
    ("cost", "cost_mean_usd", "USD per case, mean", "lower", None, ""),
    ("cost", "cost_median_usd", "USD per case, median", "lower", None, ""),
    ("cost", "cost_max_usd", "USD per case, maximum", "lower", None, ""),
    ("cost", "cost_total_usd", "USD per run", "lower", None, ""),
]
DAY_MEASURES = ["minutes to bed", "violations", "bed_not_available"]
CONTENTION_MEASURES = ["moments right", "violations", "bed_not_available"]


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


def _side_check(row: dict) -> bool:
    return str(row.get("label", "")).startswith(tuple(SIDE_CHECKS))


def _complete(row: dict) -> bool:
    return str(row.get("partial_run", "")).lower() not in ("true", "1")


def latest_test_runs(runs: list[dict]) -> dict[str, list[dict]]:
    """Each system's latest REPETITIONS test runs, partial runs and side checks left out."""
    chosen = {}
    for system in SYSTEMS:
        mine = sorted((r for r in runs if r.get("system") == system and r.get("set") == "test" and _complete(r)
                       and not _side_check(r)), key=lambda r: r["run_id"])
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

    days, latest = shift_days(runs)
    return {"side_checks": side_checks(runs, chosen["multi_agent"]),"runs": {s: [r["run_id"] for r in chosen[s]] for s in SYSTEMS},
            "repetitions": {s: len(chosen[s]) for s in SYSTEMS},
            "stability": {s: stability(admissions, chosen[s]) for s in SYSTEMS},
            "rows": rows, "days": days, "three_days": pooled_minutes(latest),
            "contention": contention_day(runs), "reading": reading(rows, measured)}


# ============ THE SIDE CHECKS ============
def _spread(runs: list[dict], key: str) -> Spread | None:
    values = [v for v in (_number(r.get(key)) for r in runs) if v is not None]
    return Spread(values) if values else None


def side_checks(runs: list[dict], frozen_test: list[dict]) -> list[dict]:
    """Each side check, set by set, against the frozen multi-agent system on the same set: the frozen system is the
    version of its chosen test runs; on the test set its chosen runs, on another set its complete runs of that
    version. A difference counts only beyond the spread, as in the comparison — and a single run has none."""
    if not frozen_test:
        return []
    version = frozen_test[0].get("version")
    checks = []
    for prefix, (name, changed) in SIDE_CHECKS.items():
        mine = [r for r in runs if str(r.get("label", "")).startswith(prefix) and _complete(r)
                and r.get("system") == "multi_agent" and r.get("set") in ("dev", "test")]
        for set_name in ("dev", "test"):
            check = sorted((r for r in mine if r["set"] == set_name), key=lambda r: r["run_id"])
            if not check:
                continue
            frozen = frozen_test if set_name == "test" else [
                r for r in runs if r.get("system") == "multi_agent" and r.get("set") == set_name
                and r.get("version") == version and _complete(r) and not _side_check(r)]
            measures = []
            for key, label, direction in SIDE_MEASURES:
                a, b = _spread(check, key), _spread(frozen, key)
                verdict = better(direction, a, b)
                if a is None or b is None:
                    said = ""
                elif min(len(a.values), len(b.values)) < 2:
                    said = "one run — no spread"          # a single run has no range: nothing counts as beyond it
                else:
                    said = "better" if verdict == 1 else "worse" if verdict == -1 else "the same"
                measures.append({"key": key, "measure": label, "check": a, "frozen": b, "verdict": said})
            checks.append({"check": name, "label": prefix, "changed": changed, "set": set_name,
                           "runs": [r["run_id"] for r in check], "frozen_runs": [r["run_id"] for r in frozen],
                           "measures": measures})
    return checks


# ============ THE SCRIPTED DAYS ============
def _latest_day_run(runs: list[dict], system: str, day: str) -> dict | None:
    mine = sorted((r for r in runs if r.get("system") == system and r.get("days") == day and _complete(r)
                   and not _side_check(r)), key=lambda r: r["run_id"])
    return mine[-1] if mine else None


def shift_days(runs: list[dict]) -> tuple[dict, dict]:
    """The three days of a shift, day by day: each system's latest run of the day, and the baseline from it.
    Also returns those runs, for the pooled minutes."""
    days, latest = {}, {}
    for day in SHIFT_DAYS:
        for system in SYSTEMS:
            run = _latest_day_run(runs, system, day)
            if run:
                latest.setdefault(system, {})[day] = run
                days.setdefault(day, {})[system] = {name: _number(run.get(f"{day} {name}")) for name in DAY_MEASURES}
                days[day].setdefault("baseline", {name: _number(run.get(f"baseline {day} {name}"))
                                                  for name in DAY_MEASURES})
    return days, latest


def _pool(runs_by_day: dict, prefix: str) -> float | None:
    """The mean minutes to a bed over every patient placed on the three days: each day weighted by its placed
    patients. None unless all three days are there, with their placed counts."""
    total = placed = 0.0
    for day in SHIFT_DAYS:
        run = runs_by_day.get(day)
        minutes = _number(run.get(f"{prefix}{day} minutes to bed")) if run else None
        count = _number(run.get(f"{prefix}{day} placed")) if run else None
        if minutes is None or count is None:
            return None
        total, placed = total + minutes * count, placed + count
    return total / placed if placed else None


def pooled_minutes(latest: dict) -> dict:
    """The three days together: each system's pooled minutes to a bed, and the baseline's — from the first system
    whose day runs have it (the baseline is the same code in both)."""
    pooled = {system: _pool(latest.get(system, {}), "") for system in SYSTEMS}
    pooled["baseline"] = next((value for value in (_pool(latest.get(s, {}), "baseline ") for s in SYSTEMS)
                               if value is not None), None)
    return pooled


def contention_day(runs: list[dict]) -> dict:
    """The contention day apart: moments decided right out of those reached as designed, and its safety — for each
    system and for the baseline. No minutes to a bed."""
    found = {}
    for system in SYSTEMS:
        run = _latest_day_run(runs, system, CONTENTION_DAY)
        if not run:
            continue
        for who, prefix in ((system, ""), ("baseline", "baseline ")):
            if who in found or run.get(f"{prefix}competing moments as designed") in (None, ""):
                continue
            found[who] = {"moments right": f"{run.get(f'{prefix}competing moments right')} of "
                                           f"{run.get(f'{prefix}competing moments as designed')}",
                          **{name: _number(run.get(f"{prefix}{CONTENTION_DAY} {name}"))
                             for name in ("violations", "bed_not_available")}}
    return found


# ============ PRINTED AND SAVED ============
def _cell(value) -> str:
    if value is None:
        return "—"
    return value.text() if isinstance(value, Spread) else f"{value:.3f}" if isinstance(value, float) else str(value)


def _day_cell(value, name: str) -> str:
    """A day's number: minutes with one decimal, counts whole."""
    if not isinstance(value, float):
        return _cell(value)
    return f"{value:.1f}" if name.startswith("minutes") else f"{value:.0f}"


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
    header = ["| Day | Measure | " + " | ".join(names.values()) + " | Baseline |", "|---|---|---|---|---|"]
    if report["days"]:
        lines += ["", "## The three days of a shift — once per system: the minutes to a bed", "", *header]
        for day, by_system in report["days"].items():
            for name in DAY_MEASURES:
                cells = [_day_cell(by_system.get(who, {}).get(name), name) for who in (*SYSTEMS, "baseline")]
                lines.append(f"| {day} | {name} | " + " | ".join(cells) + " |")
        pooled = report["three_days"]
        lines.append("| **the three days** | **minutes to bed, every patient placed** | "
                     + " | ".join(_day_cell(pooled.get(who), "minutes") for who in (*SYSTEMS, "baseline")) + " |")
    if report["contention"]:
        lines += ["", "## Competing for a bed (day 4) — apart: moments decided right, not the minutes to a bed", "",
                  *header]
        for name in CONTENTION_MEASURES:
            cells = [_day_cell(report["contention"].get(who, {}).get(name), name) for who in (*SYSTEMS, "baseline")]
            lines.append(f"| {CONTENTION_DAY} | {name} | " + " | ".join(cells) + " |")
    lines += ["", f"**Reading:** {report['reading']}"]
    if report.get("side_checks"):
        lines += ["", "## Where the gap comes from — side checks, never in the comparison", "",
                  "Each against the frozen multi-agent system on the same set; \"the same\" — the ranges overlap.", "",
                  "| Side check | What it changed | Set | Runs | Measure | Side check | Frozen system | Beyond the spread |",
                  "|---|---|---|---|---|---|---|---|"]
        for check in report["side_checks"]:
            for measure in check["measures"]:
                lines.append(f"| {check['check']} | {check['changed']} | {check['set']} | "
                             f"{len(check['runs'])} vs {len(check['frozen_runs'])} | {measure['measure']} | "
                             f"{_cell(measure['check'])} | {_cell(measure['frozen'])} | {measure['verdict'] or '—'} |")
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
