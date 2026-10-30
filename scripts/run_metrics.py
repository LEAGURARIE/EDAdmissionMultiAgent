"""Run a decider on the whole evaluation and print its metrics next to the baseline's — the Code Coordinator,
today's procedure written as code — and against the charter's targets.

Run (from the project folder):
    .venv\\Scripts\\python scripts\\run_metrics.py                     (decider: code_coordinator — the baseline itself)
    .venv\\Scripts\\python scripts\\run_metrics.py --decider example_pipeline
    .venv\\Scripts\\python scripts\\run_metrics.py --decider my_package.my_module:my_decider   (your own decider)

What it runs, for the decider and for the baseline: the pairs of both files (one decision each), the paired cases (two
patients, one bed) and the three scripted days. Then it prints:
  - every metric: the decider · the baseline · the difference — and the charter's target
  - by challenge, trap and template: match and violations, the decider and the baseline
  - the scripted days, and the minutes saved for each patient against the baseline
  - the traces the decider handed (observability/tracing.py, collect): complete traces, groundedness, time and cost
    per case — none for the baseline, which keeps no traces
And it adds the run to the run history (database/run_log.py): one row in runs.csv, one row per patient in
admissions.csv, one row per case in costs.csv (every case the decider paid for — the days too), its traces in
traces/<run id>.jsonl. --label says what changed, e.g. --label "severity prompt v2".
The full rows are saved to outputs/metrics_<decider>.json (outputs/ is not uploaded to Git).
Owner: Lea Gur-Arie
"""
import argparse
import json
import sys
from pathlib import Path

from ed_admission import config
from ed_admission.agents.budget import RunStopped
from ed_admission.baselines import code_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import deciders, metrics, report, run_cases, run_paired, sets
from ed_admission.database import run_log
from ed_admission.observability import tracing

PROJECT = Path(__file__).resolve().parents[1]
DAYS = ["busy_day", "evening_night", "weekend"]
CONTENTION_DAY = "competing_for_a_bed"           # shown apart: not in the minutes to a bed

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
            ("mean_minutes_to_bed", "mean minutes to a bed"), ("mean_minutes_to_bed_severe", "  severe patients"),
            ("cases", "cases"), ("mean_minutes_in_queue", "mean minutes in the queue"),
            ("max_minutes_in_queue", "longest wait in the queue"), ("mean_minutes_per_case", "mean minutes per case"),
            ("most_open_cases", "most cases open at once"), ("conflicts", "conflicts"),
            ("bed_errors", "a bed chosen that was not free")]


def show(value, kind: str) -> str:
    if value is None:
        return "-"
    return f"{value:.0%}" if kind == "rate" else f"{value:.0f}" if isinstance(value, float) else str(value)


def difference(system, baseline, kind: str) -> str:
    if system is None or baseline is None:
        return "-"
    gap = system - baseline
    return f"{gap * 100:+.0f}%" if kind == "rate" else f"{gap:+.0f}"


def print_comparison(title: str, system: dict, baseline: dict) -> None:
    print(f"\n{title} — {system['decisions']} decisions, {system['errors']} errors")
    print(f"  {'':<26}{'decider':>9}{'baseline':>10}{'difference':>12}   charter target")
    for key, label, kind, target in ROWS:
        ok = ""
        if key in system["meets_charter"]:
            ok = "✓" if system["meets_charter"][key] else "✗"
        print(f"  {label:<26}{show(system[key], kind):>9}{show(baseline[key], kind):>10}"
              f"{difference(system[key], baseline[key], kind):>12}   {target} {ok}")
    rules = sorted(set(system["violations_by_rule"]) | set(baseline["violations_by_rule"]))
    for rule in rules:
        s, t = system["violations_by_rule"].get(rule, 0), baseline["violations_by_rule"].get(rule, 0)
        print(f"      {metrics.HARD_RULES[rule]}: {s} · baseline {t}")


def print_breakdown(by: str, system: list, baseline: list) -> None:
    mine, theirs = metrics.breakdown(system, by), metrics.breakdown(baseline, by)
    print(f"\nBy {by} — match: decider · baseline   violations: decider · baseline")
    for key, row in mine.items():
        other = theirs.get(key, {"match_rate": 0.0, "violations": 0})
        print(f"  {str(key):<6} {row['match_rate']:>5.0%} · {other['match_rate']:>4.0%}      "
              f"{row['violations']:>3} · {other['violations']:<3}   ({row['decisions']} decisions)")


def run_evaluation(decider, sourced: list | None = None, only: set | None = None, with_paired: bool = True):
    """The pairs and paired cases, scored; the decision behind each score, for the admissions log. The traces the
    decider handed while running them are added to `sourced`, each with where it came from (pairs:first,
    pairs:second, paired) — as they come, so a run stopped halfway keeps what it paid for.
    only: the (case_id, template) pairs of a development or test set (None: every pair) · with_paired: the paired
    cases too (they belong to the test set)."""
    tracing.take()                                                  # a clean collection
    by_set, sourced = {}, ([] if sourced is None else sourced)
    for name in ("first", "second"):
        by_set[name] = run_cases(decider, evaluation_set=name, only=only)
        sourced += [(f"pairs:{name}", trace) for trace in tracing.take()]
    paired_results = run_paired(decider) if with_paired else []
    sourced += [("paired", trace) for trace in tracing.take()]
    pairs, rows = [], []
    for name, results in by_set.items():
        for result, scored in zip(results, metrics.score_cases(results)):
            pairs.append(scored)
            rows.append((f"pairs:{name}", result.decision, scored))
    paired = metrics.score_paired(paired_results)
    decided = {(r.case_id, d.patient_id): d for r in paired_results for d in (r.decisions or [])}
    rows += [("paired", decided.get((s.paired_case, s.case_id)), s) for s in paired]
    return pairs, paired, rows


def print_moments(moments: dict[str, list[dict]]) -> None:
    """Competing for a bed: every moment — who should have got the bed, and who did."""
    print(f"\n{'=' * 100}\nCompeting for a bed — each moment (decider · baseline)")
    for system, baseline in zip(moments["system"], moments["baseline"]):
        cells = []
        for m in (system, baseline):
            if not m["as_designed"]:
                cells.append(f"not reached: {m['why_not']}")
            else:
                cells.append(f"{'right' if m['right'] else 'wrong'} — should get {m['should_get']}, got {m['got'] or 'nobody'}")
        print(f"  {system['time']}  {system['name']}\n      {cells[0]}\n      {cells[1]}")


def print_details(pairs, paired, baseline_pairs, baseline_paired, days, baseline_days, saved, everything, trace_metrics):
    print("\n" + "=" * 100 + "\nDetails")
    print_comparison("Pairs", metrics.summarize(pairs), metrics.summarize(baseline_pairs))
    print_comparison("Paired cases", metrics.summarize(paired), metrics.summarize(baseline_paired))
    print_comparison("All decisions", everything, metrics.summarize(baseline_pairs + baseline_paired))
    for by in ("challenge", "trap", "template"):
        print_breakdown(by, pairs + paired, baseline_pairs + baseline_paired)

    print("\nScripted days — decider · baseline")
    print(f"  {'':<26}" + "".join(f"{name:>22}" for name in days))
    for key, label in DAY_ROWS:
        cells = []
        for name in days:
            s, t = days[name][key], baseline_days[name][key]
            s, t = (len(s), len(t)) if isinstance(s, list) else (s, t)
            cells.append(f"{show(s, 'count')} · {show(t, 'count')}")
        print(f"  {label:<26}" + "".join(f"{cell:>22}" for cell in cells))

    print("\nMinutes saved for each patient, from the admission decision to a bed — against the baseline:")
    for name, day in saved.items():
        mean = "-" if day["mean_saved"] is None else f"{day['mean_saved']:+.0f}"
        median = "-" if day["median_saved"] is None else f"{day['median_saved']:+.0f}"
        print(f"  {name:<14} mean {mean} min · median {median} min · placed in both {day['placed_in_both']}"
              f"/{day['patients']} · placed only by this decider {len(day['placed_by_system_only'])}"
              f" · placed only by the baseline {len(day['placed_by_baseline_only'])}")

    print("\nTraces, time and cost per case:")
    print(f"  time per case            mean {everything['mean_seconds']:.2f} s · max {everything['max_seconds']:.2f} s")
    if trace_metrics is None:
        print("  no traces — this decider keeps none (the baseline), so complete traces, groundedness and cost"
              " are not measured")
    else:
        t = trace_metrics
        print(f"  complete traces          {t['complete_trace_rate']:>6.0%}   target 100% "
              f"{'✓' if t['complete_trace_rate'] == 1 else '✗'}   ({t['cases']} cases)")
        print(f"  groundedness             {t['groundedness']:>6.0%}   target 95%  "
              f"{'✓' if t['groundedness'] >= 0.95 else '✗'}   ({t['claims']} claims)")
        print(f"  cost per case            mean ${t['cost_mean_usd']:.4f} · max ${t['cost_max_usd']:.4f} · "
              f"these cases ${t['cost_total_usd']:.2f}   ({t['agent_calls']} agent calls, "
              f"{t['tokens_in']} tokens in, {t['tokens_out']} out)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", default="code_coordinator",
                        help=f"one of {sorted(deciders.DECIDERS)}, or module:function")
    parser.add_argument("--label", default="", help="what changed in this run, for the run history")
    parser.add_argument("--details", action="store_true", help="everything: by rule, challenge, trap, template, "
                        "each day, minutes saved, cost")
    parser.add_argument("--paid", action="store_true", help="run a decider that calls a model and is paid for "
                        "(single_agent) — only after its estimated cost was approved")
    parser.add_argument("--set", choices=["all", "dev", "test", "none"], default="all",
                        help="all: every pair and the paired cases · dev: the development pairs · test: the test pairs "
                             "and the paired cases (evaluation/sets.py, frozen 2026-10-15) · none: no pairs — "
                             "with --days, a run of the scripted days alone")
    parser.add_argument("--days", default=None, choices=["all", "none", *DAYS, CONTENTION_DAY],
                        help="the scripted days to run: all, none, or one day (default: all with --set all, none "
                             "with dev or test — the days are a run of their own, once per system)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run it this many times — the test set 3 times per system (comparison protocol, "
                             "section 7); each repetition is its own run with its own budget, labelled "
                             "'<label> · repetition <n>'")
    args = parser.parse_args()
    args.days = args.days or ("all" if args.set == "all" else "none")
    try:
        decider = deciders.resolve(args.decider, paid=args.paid)
    except deciders.PaidWithoutApproval as refused:
        print(refused)
        return

    label = args.label
    for repetition in range(1, args.repeat + 1):
        if args.repeat > 1:
            args.label = f"{label} · repetition {repetition}".lstrip(" ·")
            print(f"\n{'=' * 100}\nRepetition {repetition} of {args.repeat}\n")
        if not run_once(args, decider):
            return                                                  # the run's budget was spent: no more runs


def run_once(args, decider) -> bool:
    """One run, from the cases to the history. False when the run's budget stopped it."""
    sourced = []                                                    # every case the decider paid for, by source
    try:
        with deciders.paid_run(decider):                            # a paid decider: its run budget, prices checked
            evaluated = run_everything(decider, sourced, args.set, args.days)
    except RunStopped as stop:
        save_partial_run(args, sourced, stop)
        return False
    except Exception as failure:                                    # whatever stops it, what was paid for is kept
        save_partial_run(args, sourced, failure)
        raise
    try:
        pairs, paired, rows, baseline_pairs, baseline_paired, days, baseline_days, saved, day_engines, moments = evaluated
        traces = [trace for source, trace in sourced if not source.startswith("day:")]   # trace metrics: pairs, paired
        everything = metrics.summarize(pairs + paired)
        trace_metrics = metrics.trace_summary(traces)
        report_and_log(args, decider, evaluated, sourced, everything, trace_metrics)
    except Exception as failure:                                    # a scoring error after the cases were paid for
        save_partial_run(args, sourced, failure)
        raise
    return True


def p95(values: list[float]) -> float | None:
    """The 95th percentile (nearest rank)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]


def run_everything(decider, sourced: list, set_name: str = "all", which_days: str = "all"):
    """The decider and the baseline on the same cases: the set's pairs, the paired cases (all and test), and the
    chosen days. The decider's traces are added to `sourced` as they come."""
    only = set() if set_name == "none" else sets.pairs_of(set_name)
    with_paired = set_name in ("all", "test")
    pairs, paired, rows = run_evaluation(decider, sourced, only, with_paired)
    baseline_pairs, baseline_paired, _ = run_evaluation(code_coordinator, None, only, with_paired)
    chosen = {"all": [*DAYS, CONTENTION_DAY], "none": []}.get(which_days, [which_days])
    days, baseline_days, saved, day_engines = {}, {}, {}, {}
    for name in [day for day in DAYS if day in chosen]:
        engines = {}
        for label, policy in (("system", decider), ("baseline", code_coordinator)):
            engines[label] = load_scenario(PROJECT / "data" / "scenarios" / f"{name}.yaml")
            engines[label].policy = policy
            engines[label].run_to_end()
            sourced += [(f"day:{name}", trace) for trace in tracing.take() if label == "system"]
        days[name] = metrics.score_day(engines["system"], name)
        baseline_days[name] = metrics.score_day(engines["baseline"], name)
        saved[name] = metrics.minutes_saved(engines["baseline"], engines["system"])
        day_engines[name] = engines["system"]
    moments = {"system": [], "baseline": []}
    for label, policy in ((("system", decider), ("baseline", code_coordinator)) if CONTENTION_DAY in chosen else ()):
        engine = load_scenario(PROJECT / "data" / "scenarios" / f"{CONTENTION_DAY}.yaml")
        engine.policy = policy
        engine.run_to_end()
        sourced += [(f"day:{CONTENTION_DAY}", trace) for trace in tracing.take() if label == "system"]
        moments[label] = metrics.contention_moments(CONTENTION_DAY, engine)
        if label == "system":
            day_engines[CONTENTION_DAY] = engine
    return pairs, paired, rows, baseline_pairs, baseline_paired, days, baseline_days, saved, day_engines, moments


def save_partial_run(args, sourced: list, stop: Exception) -> None:
    """The run stopped before its end — the run's budget was spent, or anything failed: no metrics (half an evaluation
    would mislead), but every case paid for is kept — its cost in costs.csv, its trace — and the run is marked partial
    in runs.csv."""
    sourced += [("stopped", trace) for trace in tracing.take()]    # the case that reached the budget
    run = run_log.new_run("system", args.decider, args.label)
    run_cost = metrics.cost_summary([trace for _, trace in sourced]) or {}
    if sourced:
        for _, trace in sourced:
            trace.save(config.outputs_dir() / "traces", name=run["run_id"])
        run_log.log_costs(run, [run_log.cost_row(source, trace) for source, trace in sourced])
    numbers = {k: round(v, 6) if isinstance(v, float) else v for k, v in run_cost.items()}
    numbers["partial_run"] = True
    path = run_log.log_run(run, numbers)
    print(f"STOPPED — {type(stop).__name__}: {stop}. No metrics for a partial run.")
    print(f"  Paid for {len(sourced)} cases, ${run_cost.get('cost_total_usd', 0):.2f}. Kept in {path.parent} "
          f"(runs.csv marked partial · costs.csv · traces/{run['run_id']}.jsonl) — run {run['run_id']}")


def report_and_log(args, decider, evaluated, sourced, everything, trace_metrics) -> None:
    """Print the metrics next to the baseline's and add the run to the history."""
    pairs, paired, rows, baseline_pairs, baseline_paired, days, baseline_days, saved, day_engines, moments = evaluated
    name = deciders.label(args.decider)
    columns = {name: report.column(pairs + paired, days, trace_metrics),
               "code coordinator": report.column(baseline_pairs + baseline_paired, baseline_days)}
    print(f"{name}  vs  the baseline — the Code Coordinator (today's procedure, written as code)\n")
    print(report.render(columns, judged=name))
    print(report.moments_line(name, moments["system"]))
    print(report.moments_line("code coordinator", moments["baseline"]))
    if args.details:
        print_moments(moments)
        print_details(pairs, paired, baseline_pairs, baseline_paired, days, baseline_days, saved, everything, trace_metrics)

    output = config.outputs_dir() / f"metrics_{deciders.label(args.decider)}.json"
    output.write_text(json.dumps({
        "pairs": metrics.summarize(pairs), "paired": metrics.summarize(paired), "all": everything,
        "baseline": {"pairs": metrics.summarize(baseline_pairs), "paired": metrics.summarize(baseline_paired),
                  "all": metrics.summarize(baseline_pairs + baseline_paired), "days": baseline_days},
        "by_template": metrics.breakdown(pairs + paired, "template"),
        "by_trap": metrics.breakdown(pairs + paired, "trap"),
        "by_challenge": metrics.breakdown(pairs + paired, "challenge"),
        "by_patient_type": metrics.breakdown(pairs + paired, "patient_type"),
        "days": days, "minutes_saved": saved, "competing_for_a_bed": moments, "traces": trace_metrics, "rows": metrics.as_rows(pairs + paired),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf8")

    # The run history: one row in runs.csv, one row per patient in admissions.csv, the traces by run id.
    run = run_log.new_run("system", args.decider, args.label)
    trace_file = ""
    if sourced:
        for _, trace in sourced:                                    # every case paid for, the days too
            trace.save(config.outputs_dir() / "traces", name=run["run_id"])
        trace_file = f"traces/{run['run_id']}.jsonl"
    admissions = [run_log.scored_row(source, decision, scored, trace_file) for source, decision, scored in rows]
    for name, engine in day_engines.items():
        admissions += run_log.day_rows(name, engine)
    run_log.log_admissions(run, admissions)
    run_cost = metrics.cost_summary([trace for _, trace in sourced])     # every case paid for, the days too
    if run_cost:
        run_log.log_costs(run, [run_log.cost_row(source, trace) for source, trace in sourced])
    baseline = metrics.summarize(baseline_pairs + baseline_paired)
    numbers = {"set": args.set, "days": args.days}
    numbers.update({key: round(everything[key], 3) for key, *_ in ROWS})
    numbers.update({f"baseline {key}": round(baseline[key], 3)
                    for key in ("match_rate", "violations", "need_met_rate", "bed_not_available", "mean_seconds")})
    seconds = p95([s.seconds for s in pairs + paired])
    numbers["seconds_p95"] = None if seconds is None else round(seconds, 3)
    for name in days:                                               # the scripted days, for the comparison report
        numbers[f"{name} violations"] = days[name]["violations"]
        numbers[f"{name} bed_not_available"] = days[name]["bed_not_available"]
        numbers[f"baseline {name} minutes to bed"] = (None if baseline_days[name]["mean_minutes_to_bed"] is None
                                                      else round(baseline_days[name]["mean_minutes_to_bed"], 1))
    for name in days:
        mean_saved, to_bed = saved[name]["mean_saved"], days[name]["mean_minutes_to_bed"]
        numbers[f"{name} minutes saved"] = None if mean_saved is None else round(mean_saved, 1)
        numbers[f"{name} minutes to bed"] = None if to_bed is None else round(to_bed, 1)
        numbers[f"{name} max minutes in queue"] = days[name]["max_minutes_in_queue"]
        numbers[f"{name} conflicts"] = days[name]["conflicts"]
    if trace_metrics:
        numbers.update({k: round(trace_metrics[k], 4) for k in ("complete_trace_rate", "groundedness")})
    if run_cost:
        numbers.update({k: round(v, 6) if isinstance(v, float) else v for k, v in run_cost.items()})
    reached = [m for m in moments["system"] if m["as_designed"]]
    numbers["competing moments as designed"] = len(reached)
    numbers["competing moments right"] = sum(m["right"] for m in reached)
    numbers["mean_seconds"] = round(everything["mean_seconds"], 3)
    path = run_log.log_run(run, numbers)
    if run_cost:
        print(f"\n  The whole run cost ${run_cost['cost_total_usd']:.2f} for {run_cost['cost_cases']} cases "
              f"(mean ${run_cost['cost_mean_usd']:.4f}, max ${run_cost['cost_max_usd']:.4f})"
              f"{' — PARTIAL: the run budget was reached' if run_cost['partial_run'] else ''}")
    saved_files = "runs.csv · admissions.csv" + (" · costs.csv" if run_cost else "")
    print(f"\n  Saved to your history: {path.parent}  ({saved_files})  — run {run['run_id']}")
    if not args.details:
        print("  For the full details: --details")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
