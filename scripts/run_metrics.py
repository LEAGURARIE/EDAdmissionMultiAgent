"""Run a decider on the whole evaluation and print its metrics next to today's — the "simulated coordinator" — and
against the charter's targets.

Run (from the project folder):
    .venv\\Scripts\\python scripts\\run_metrics.py                     (decider: first_free_bed)
    .venv\\Scripts\\python scripts\\run_metrics.py --decider example_pipeline
    .venv\\Scripts\\python scripts\\run_metrics.py --decider my_package.my_module:my_decider   (your own decider)

What it runs, for the decider and for today: the pairs of both files (one decision each), the paired cases (two
patients, one bed) and the three scripted days. Then it prints:
  - every metric: the decider · today · the difference — and the charter's target
  - by challenge, trap and template: match and violations, the decider and today
  - the scripted days, and the minutes saved for each patient against today
  - the traces the decider handed (observability/tracing.py, collect): complete traces, groundedness, time and cost
    per case — none for the code baselines, which keep no traces
The full rows are saved to outputs/metrics_<decider>.json (outputs/ is not uploaded to Git).
Owner: integrator
"""
import argparse
import json
import sys
from pathlib import Path

from ed_admission.baselines import simulated_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import deciders, metrics, run_cases, run_paired
from ed_admission.observability import tracing

PROJECT = Path(__file__).resolve().parents[1]
DAYS = ["busy_day", "evening_night", "weekend"]

# metric key -> (label, kind, charter target in words); kind: rate (a share) or count
ROWS = [
    ("match_rate", "match", "rate", "85%"),
    ("report_match_rate", "match, reports alone", "rate", ""),
    ("violations", "hard-rule violations", "count", "0"),
    ("need_met_rate", "needs met or escalated", "rate", "100%"),
    ("off_service", "off-service placements", "count", ""),
    ("bed_not_available", "bed the ward can't give", "count", ""),
    ("missing_flags", "missing flags", "count", ""),
    ("escalations", "escalations", "count", ""),
]
DAY_ROWS = [("placed", "placed"), ("escalated", "escalated"), ("still_waiting", "still waiting"),
            ("violations", "violations"), ("bed_not_available", "bed the ward can't give"),
            ("mean_minutes_to_bed", "mean minutes to a bed"), ("mean_minutes_to_bed_severe", "  severe patients")]


def show(value, kind: str) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}" if kind == "rate" else f"{value:.0f}" if isinstance(value, float) else str(value)


def difference(system, today, kind: str) -> str:
    if system is None or today is None:
        return "-"
    gap = system - today
    return f"{gap * 100:+.0f}%" if kind == "rate" else f"{gap:+.0f}"


def print_comparison(title: str, system: dict, today: dict) -> None:
    print(f"\n{title} — {system['decisions']} decisions, {system['errors']} errors")
    print(f"  {'':<26}{'decider':>9}{'today':>9}{'difference':>12}   charter target")
    for key, label, kind, target in ROWS:
        ok = ""
        if key in system["meets_charter"]:
            ok = "✓" if system["meets_charter"][key] else "✗"
        print(f"  {label:<26}{show(system[key], kind):>9}{show(today[key], kind):>9}"
              f"{difference(system[key], today[key], kind):>12}   {target} {ok}")
    rules = sorted(set(system["violations_by_rule"]) | set(today["violations_by_rule"]))
    for rule in rules:
        s, t = system["violations_by_rule"].get(rule, 0), today["violations_by_rule"].get(rule, 0)
        print(f"      {metrics.HARD_RULES[rule]}: {s} · today {t}")


def print_breakdown(by: str, system: list, today: list) -> None:
    mine, theirs = metrics.breakdown(system, by), metrics.breakdown(today, by)
    print(f"\nBy {by} — match: decider · today   violations: decider · today")
    for key, row in mine.items():
        other = theirs.get(key, {"match_rate": 0.0, "violations": 0})
        print(f"  {str(key):<6} {row['match_rate']:>5.0%} · {other['match_rate']:>4.0%}      "
              f"{row['violations']:>3} · {other['violations']:<3}   ({row['decisions']} decisions)")


def run_evaluation(decider):
    """The pairs and paired cases, scored — and the traces the decider handed while running them."""
    tracing.take()                                                  # a clean collection
    pairs = metrics.score_cases(run_cases(decider) + run_cases(decider, evaluation_set="second"))
    paired = metrics.score_paired(run_paired(decider))
    return pairs, paired, tracing.take()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", default="first_free_bed",
                        help=f"one of {sorted(deciders.DECIDERS)}, or module:function")
    args = parser.parse_args()
    decider = deciders.resolve(args.decider)

    pairs, paired, traces = run_evaluation(decider)
    today_pairs, today_paired, _ = run_evaluation(simulated_coordinator)
    print(f"decider: {args.decider}   ·   today: the simulated coordinator (today's procedure)")
    print_comparison("Pairs", metrics.summarize(pairs), metrics.summarize(today_pairs))
    print_comparison("Paired cases", metrics.summarize(paired), metrics.summarize(today_paired))
    print_comparison("All decisions", metrics.summarize(pairs + paired), metrics.summarize(today_pairs + today_paired))
    for by in ("challenge", "trap", "template"):
        print_breakdown(by, pairs + paired, today_pairs + today_paired)

    days, today_days, saved = {}, {}, {}
    for name in DAYS:
        engines = {}
        for label, policy in (("system", decider), ("today", simulated_coordinator)):
            engines[label] = load_scenario(PROJECT / "data" / "scenarios" / f"{name}.yaml")
            engines[label].policy = policy
            engines[label].run_to_end()
        days[name], today_days[name] = metrics.score_day(engines["system"]), metrics.score_day(engines["today"])
        saved[name] = metrics.minutes_saved(engines["today"], engines["system"])
    tracing.take()                                                  # the days' traces are not part of the trace metrics
    print("\nScripted days — decider · today")
    print(f"  {'':<26}" + "".join(f"{name:>22}" for name in DAYS))
    for key, label in DAY_ROWS:
        cells = []
        for name in DAYS:
            s, t = days[name][key], today_days[name][key]
            s, t = (len(s), len(t)) if isinstance(s, list) else (s, t)
            cells.append(f"{show(s, 'count')} · {show(t, 'count')}")
        print(f"  {label:<26}" + "".join(f"{cell:>22}" for cell in cells))

    print("\nMinutes saved for each patient, from the admission decision to a bed — against today:")
    for name, day in saved.items():
        mean = "-" if day["mean_saved"] is None else f"{day['mean_saved']:+.0f}"
        median = "-" if day["median_saved"] is None else f"{day['median_saved']:+.0f}"
        print(f"  {name:<14} mean {mean} min · median {median} min · placed in both {day['placed_in_both']}"
              f"/{day['patients']} · placed only by this decider {len(day['placed_by_system_only'])}"
              f" · placed only today {len(day['placed_today_only'])}")

    everything = metrics.summarize(pairs + paired)
    trace_metrics = metrics.trace_summary(traces)
    print("\nTraces, time and cost per case:")
    print(f"  time per case            mean {everything['mean_seconds']:.2f} s · max {everything['max_seconds']:.2f} s")
    if trace_metrics is None:
        print("  no traces — this decider keeps none (a code baseline), so complete traces, groundedness and cost"
              " are not measured")
    else:
        t = trace_metrics
        print(f"  complete traces          {t['complete_trace_rate']:>6.0%}   target 100% "
              f"{'✓' if t['complete_trace_rate'] == 1 else '✗'}   ({t['cases']} cases)")
        print(f"  groundedness             {t['groundedness']:>6.0%}   target 95%  "
              f"{'✓' if t['groundedness'] >= 0.95 else '✗'}   ({t['claims']} claims)")
        print(f"  cost per case            mean ${t['cost_mean_usd']:.4f} · max ${t['cost_max_usd']:.4f} · "
              f"this run ${t['cost_total_usd']:.2f}   ({t['agent_calls']} agent calls, "
              f"{t['tokens_in']} tokens in, {t['tokens_out']} out)")

    output = PROJECT / "outputs" / f"metrics_{deciders.label(args.decider)}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({
        "pairs": metrics.summarize(pairs), "paired": metrics.summarize(paired), "all": everything,
        "today": {"pairs": metrics.summarize(today_pairs), "paired": metrics.summarize(today_paired),
                  "all": metrics.summarize(today_pairs + today_paired), "days": today_days},
        "by_template": metrics.breakdown(pairs + paired, "template"),
        "by_trap": metrics.breakdown(pairs + paired, "trap"),
        "by_challenge": metrics.breakdown(pairs + paired, "challenge"),
        "by_patient_type": metrics.breakdown(pairs + paired, "patient_type"),
        "days": days, "minutes_saved": saved, "traces": trace_metrics, "rows": metrics.as_rows(pairs + paired),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf8")
    print(f"\nsaved to {output.relative_to(PROJECT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
