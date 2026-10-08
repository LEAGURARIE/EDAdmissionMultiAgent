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
And it adds the run to the run history (database/run_log.py): one row in runs.csv, one row per patient in
admissions.csv, its traces in traces/<run id>.jsonl. --label says what changed, e.g. --label "severity prompt v2".
The full rows are saved to outputs/metrics_<decider>.json (outputs/ is not uploaded to Git).
Owner: integrator
"""
import argparse
import json
import sys
from pathlib import Path

from ed_admission import config
from ed_admission.baselines import simulated_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import deciders, metrics, report, run_cases, run_paired
from ed_admission.database import run_log
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
    """The pairs and paired cases, scored; the decision behind each score, for the admissions log; and the traces
    the decider handed while running them."""
    tracing.take()                                                  # a clean collection
    sets = {name: run_cases(decider, evaluation_set=name) for name in ("first", "second")}
    paired_results = run_paired(decider)
    pairs, rows = [], []
    for name, results in sets.items():
        for result, scored in zip(results, metrics.score_cases(results)):
            pairs.append(scored)
            rows.append((f"pairs:{name}", result.decision, scored))
    paired = metrics.score_paired(paired_results)
    decided = {(r.case_id, d.patient_id): d for r in paired_results for d in (r.decisions or [])}
    rows += [("paired", decided.get((s.paired_case, s.case_id)), s) for s in paired]
    return pairs, paired, tracing.take(), rows


def print_details(pairs, paired, today_pairs, today_paired, days, today_days, saved, everything, trace_metrics):
    print("\n" + "=" * 100 + "\nDetails")
    print_comparison("Pairs", metrics.summarize(pairs), metrics.summarize(today_pairs))
    print_comparison("Paired cases", metrics.summarize(paired), metrics.summarize(today_paired))
    print_comparison("All decisions", everything, metrics.summarize(today_pairs + today_paired))
    for by in ("challenge", "trap", "template"):
        print_breakdown(by, pairs + paired, today_pairs + today_paired)

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", default="first_free_bed",
                        help=f"one of {sorted(deciders.DECIDERS)}, or module:function")
    parser.add_argument("--label", default="", help="what changed in this run, for the run history")
    parser.add_argument("--details", action="store_true", help="everything: by rule, challenge, trap, template, "
                        "each day, minutes saved, cost")
    args = parser.parse_args()
    decider = deciders.resolve(args.decider)

    pairs, paired, traces, rows = run_evaluation(decider)
    today_pairs, today_paired, _, _ = run_evaluation(simulated_coordinator)
    days, today_days, saved, day_engines = {}, {}, {}, {}
    for name in DAYS:
        engines = {}
        for label, policy in (("system", decider), ("today", simulated_coordinator)):
            engines[label] = load_scenario(PROJECT / "data" / "scenarios" / f"{name}.yaml")
            engines[label].policy = policy
            engines[label].run_to_end()
        days[name], today_days[name] = metrics.score_day(engines["system"]), metrics.score_day(engines["today"])
        saved[name] = metrics.minutes_saved(engines["today"], engines["system"])
        day_engines[name] = engines["system"]
    tracing.take()                                                  # the days' traces are not part of the trace metrics
    everything = metrics.summarize(pairs + paired)
    trace_metrics = metrics.trace_summary(traces)

    name = deciders.label(args.decider)
    columns = {name: report.column(pairs + paired, days, trace_metrics),
               "today": report.column(today_pairs + today_paired, today_days)}
    print(f"{name}  vs  today (the simulated coordinator)\n")
    print(report.render(columns, judged=name))
    if args.details:
        print_details(pairs, paired, today_pairs, today_paired, days, today_days, saved, everything, trace_metrics)

    output = config.outputs_dir() / f"metrics_{deciders.label(args.decider)}.json"
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

    # The run history: one row in runs.csv, one row per patient in admissions.csv, the traces by run id.
    run = run_log.new_run("system", args.decider, args.label)
    trace_file = ""
    if traces:
        for trace in traces:
            trace.save(config.outputs_dir() / "traces", name=run["run_id"])
        trace_file = f"traces/{run['run_id']}.jsonl"
    admissions = [run_log.scored_row(source, decision, scored, trace_file) for source, decision, scored in rows]
    for name, engine in day_engines.items():
        admissions += run_log.day_rows(name, engine)
    run_log.log_admissions(run, admissions)
    today = metrics.summarize(today_pairs + today_paired)
    numbers = {key: round(everything[key], 3) for key, *_ in ROWS}
    numbers.update({f"today {key}": round(today[key], 3) for key in ("match_rate", "violations", "need_met_rate")})
    for name in DAYS:
        mean_saved, to_bed = saved[name]["mean_saved"], days[name]["mean_minutes_to_bed"]
        numbers[f"{name} minutes saved"] = None if mean_saved is None else round(mean_saved, 1)
        numbers[f"{name} minutes to bed"] = None if to_bed is None else round(to_bed, 1)
    if trace_metrics:
        numbers.update({k: round(trace_metrics[k], 4) for k in ("complete_trace_rate", "groundedness",
                                                                "cost_mean_usd", "cost_total_usd")})
    numbers["mean_seconds"] = round(everything["mean_seconds"], 3)
    path = run_log.log_run(run, numbers)
    print(f"\n  Saved to your history: {path.parent}  (runs.csv · admissions.csv)  — run {run['run_id']}")
    if not args.details:
        print("  For the full details: --details")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
