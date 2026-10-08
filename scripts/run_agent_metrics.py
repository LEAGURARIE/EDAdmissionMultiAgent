r"""Measure one agent alone, against the part of the expected answers that is its job.

Run (from the project folder):
    .venv\Scripts\python scripts\run_agent_metrics.py --agent severity
        (no --function: the code example of that agent, orchestration/example_agents.py — the number to beat)
    .venv\Scripts\python scripts\run_agent_metrics.py --agent severity --function ed_admission.agents.severity_agent:run

The signatures (docs/design/agent_interfaces.md, "How to run and measure"):
    clinical_problem, severity, supervision   function(toolbox, patient_id) -> an output of contracts/agent_outputs.py
    department                                function(view, request) -> WardAnswer
    safety_critic                             function(toolbox, patient_id, decision) -> SafetyVerdict

Prints the rate of every check, next to the agent's code example (✓ better, = the same, ✗ worse). With --details:
the rate by challenge and trap (or by template / decider), and every wrong row.
The rows are saved to outputs/agent_<agent>_<function>.json (outputs/ is not uploaded to Git), and the run's rates
to the run history, runs.csv (database/run_log.py). --label says what changed, e.g. --label "prompt v2".
Owner: integrator
"""
import argparse
import importlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ed_admission import config
from ed_admission.database import run_log
from ed_admission.evaluation import agent_metrics
from ed_admission.orchestration import example_agents

PROJECT = Path(__file__).resolve().parents[1]


def load(path: str | None, agent: str):
    if path is None:
        return getattr(example_agents, agent)
    module, function = path.split(":", 1)
    return getattr(importlib.import_module(module), function)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=agent_metrics.AGENTS, required=True)
    parser.add_argument("--function", help="module:function; default: the code example of the agent")
    parser.add_argument("--label", default="", help="what changed in this run, for the run history")
    parser.add_argument("--details", action="store_true", help="by challenge and trap, and every wrong row")
    parser.add_argument("--show-wrong", type=int, default=30, help="with --details: how many wrong rows to print")
    args = parser.parse_args()

    checks = agent_metrics.measure(args.agent, load(args.function, args.agent))
    mine = agent_metrics.summarize(checks)
    name = args.function.rsplit(":", 1)[-1] if args.function else "example"
    # Next to the agent: its code example — the number to beat.
    example = None
    if args.function:
        example = agent_metrics.summarize(agent_metrics.measure(args.agent, load(None, args.agent)))

    print(f"{args.agent} — {args.function or 'the code example'}\n")
    first = "your agent" if example else "code example"
    print(f"  {'':<34}{first:>14}" + (f"{'code example':>14}" if example else ""))
    print(f"  {'':<34}{'right':>14}" + (f"{'right':>14}" if example else ""))
    for check, row in mine.items():
        cells = f"{row['rate']:>14.0%}" + (f"{example[check]['rate']:>14.0%}" if example else "")
        mark = ""
        if example:
            better, same = row["rate"] > example[check]["rate"], row["rate"] == example[check]["rate"]
            mark = "  ✓" if better else "  =" if same else "  ✗"
        errors = f"   {row['errors']} errors" if row["errors"] else ""
        print(f"  {check:<34}{cells}{mark}{errors}")
    wrong = agent_metrics.wrong(checks)
    print(f"\n  {len(wrong)} wrong of {len(checks)} checks")
    print("\n  right: how often the answer matched the expected answer (target 100%)")
    if example:
        print("  code example: the same checks for a code version that reads only the structured fields —")
        print("  ✓ your agent is better · = the same · ✗ worse")
    else:
        print("  code example: a code version that reads only the structured fields — the number to beat")

    if args.details:
        by = {"department": ("group",), "safety_critic": ("challenge", "group")}.get(args.agent, ("challenge", "trap"))
        for key in by:
            print(f"\nBy {key if key != 'group' else ('template' if args.agent == 'department' else 'decider')}:")
            for group, row in agent_metrics.breakdown(checks, key).items():
                print(f"  {str(group):<22} {row['rate']:>5.0%}   ({row['items']} checks)")
        print(f"\nWrong: {len(wrong)}")
        for c in wrong[:args.show_wrong]:
            print(f"  {c.item:<45} {c.check:<28} expected {c.expected}, got {c.got}"
                  f"{'  ERROR ' + c.error if c.error else ''}")

    output = config.outputs_dir() / f"agent_{args.agent}_{name}.json"
    output.write_text(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2, default=str),
                      encoding="utf8")
    run = run_log.new_run("agent", args.function or "example", args.label, agent=args.agent)
    numbers = {check: round(row["rate"], 3) for check, row in mine.items()}
    numbers["errors"] = sum(row["errors"] for row in mine.values())
    path = run_log.log_run(run, numbers)
    print(f"\n  Saved to your history: {path.parent}  (runs.csv)  — run {run['run_id']}")
    if not args.details:
        print("  By challenge and trap, and every wrong row: --details")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
