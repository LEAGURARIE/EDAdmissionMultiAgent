r"""Measure one agent alone, against the part of the expected answers that is its job.

Run (from the project folder):
    .venv\Scripts\python scripts\run_agent_metrics.py --agent patient
        (no --function: the code example of that agent, evaluation/example_agents.py — the number to beat)
    .venv\Scripts\python scripts\run_agent_metrics.py --agent patient --function <module>:run   (any other function)
    .venv\Scripts\python scripts\run_agent_metrics.py --agent judge --real --paid --limit 3
        (the multi-agent system's real agent — paid: only after its estimated cost was approved)

The signatures (docs/design/agent_interfaces.md, "How to run and measure"):
    patient      function(toolbox, patient_id) -> PatientAssessment (contracts/agent_outputs.py)
    department   function(view, request) -> WardAnswer
    judge        function(engine, state) -> JudgeVerdict — on the Judge's cases (agent_metrics.JUDGE_CASES)
The Coordinator is the whole decision: scripts/run_metrics.py.

The real agents (--real): each item is one run of that agent of the multi-agent system — its loop, prompt and tools
(agents/orchestra_agents.py), the model of config/agents.yaml. Only with --paid. The run's budget is opened first (the
prices checked) and stops the measurement when it is spent. Every item is written to costs.csv, and its trace to
outputs/traces/<run id>.jsonl, the moment it ends — a run stopped half way (the budget, Ctrl+C, an error) keeps what
it paid for, and runs.csv marks it partial. The real agents are measured on the development set (--set dev, the
default); the test set is refused: it stays sealed for the final comparison.

Prints the rate of every check, next to the agent's code example (✓ better, = the same, ✗ worse) — the Judge has no
code example. With --details: the rate by challenge and trap (or by template, or by Judge item), and every wrong row.
The rows are saved to outputs/agent_<agent>_<function>.json (outputs/ is not uploaded to Git), and the run's rates
to the run history, runs.csv (database/run_log.py). --label says what changed, e.g. --label "prompt v2".
Owner: Lea Gur-Arie
"""
import argparse
import importlib
import json
import sys
from dataclasses import asdict

from ed_admission import config
from ed_admission.agents import budget
from ed_admission.agents.agent_loop import LangChainCaller
from ed_admission.agents.budget import RunStopped
from ed_admission.database import run_log
from ed_admission.evaluation import agent_metrics, metrics
from ed_admission.evaluation import example_agents


def load(path: str | None, agent: str):
    if path is None:
        return getattr(example_agents, agent, None)
    module, function = path.split(":", 1)
    return getattr(importlib.import_module(module), function)


class PaidItems:
    """The real agents' items as they end: each one's cost in costs.csv and its trace in traces/<run id>.jsonl, at
    once; and every trace kept for the run's cost summary."""

    def __init__(self, run: dict):
        self.run, self.traces = run, []

    def __call__(self, source: str, trace) -> None:
        trace.save(config.outputs_dir() / "traces", name=self.run["run_id"])
        run_log.append_costs(self.run, [run_log.cost_row(source, trace)])
        self.traces.append(trace)

    def summary(self) -> dict:
        found = metrics.cost_summary(self.traces) or {}
        return {k: round(v, 6) if isinstance(v, float) else v for k, v in found.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=agent_metrics.AGENTS, required=True)
    parser.add_argument("--function", help="module:function; default: the code example of the agent")
    parser.add_argument("--real", action="store_true", help="the multi-agent system's real agent (paid: with --paid)")
    parser.add_argument("--paid", action="store_true", help="a real agent calls a model and every item is paid for — "
                                                            "only after its estimated cost was approved")
    parser.add_argument("--set", choices=["all", "dev", "test"], default=None,
                        help="the pairs measured; default: dev for --real (test is refused), all otherwise")
    parser.add_argument("--label", default="", help="what changed in this run, for the run history")
    parser.add_argument("--details", action="store_true", help="by challenge and trap, and every wrong row")
    parser.add_argument("--show-wrong", type=int, default=30, help="with --details: how many wrong rows to print")
    parser.add_argument("--cases", nargs="*", help="patient only: just these records, e.g. P-007 P-043")
    parser.add_argument("--limit", type=int, help="only this many records, requests or Judge cases — a first try")
    args = parser.parse_args()

    if args.real and not args.paid:
        print(f"The real {args.agent} agent calls a model and every item is paid for. Run it only after its "
              "estimated cost was approved, with --paid.")
        return 1
    if args.agent == "judge" and not (args.real or args.function):
        print("The Judge has no code example: measure the real Judge Agent (--real --paid) or a --function.")
        return 1
    if args.real and args.set == "test":
        print("The test set is sealed until the final comparison: measure the real agents on dev.")
        return 1
    set_name = args.set or ("dev" if args.real else "all")
    part = {"cases": args.cases, "limit": args.limit, "set_name": set_name}

    paid, run = None, None
    if args.real:
        run = run_log.new_run("agent", "multi_agent", args.label, agent=args.agent)
        paid = PaidItems(run)
        real = agent_metrics.RealAgents(budget.start_run("multi_agent"), LangChainCaller(), paid)
        try:
            checks = agent_metrics.measure(args.agent, None, real=real, **part)
        except (RunStopped, KeyboardInterrupt) as stop:
            path = run_log.log_run(run, {"set": set_name, **paid.summary(), "partial_run": True})
            print(f"STOPPED — {type(stop).__name__}: {stop}. No rates for a partial run.")
            print(f"  Paid for {len(paid.traces)} items, ${paid.summary().get('cost_total_usd', 0):.2f}. "
                  f"Kept in {path.parent} (runs.csv marked partial · costs.csv · traces/{run['run_id']}.jsonl)")
            return 1
    else:
        checks = agent_metrics.measure(args.agent, load(args.function, args.agent), **part)
    mine = agent_metrics.summarize(checks)
    name = "real" if args.real else args.function.rsplit(":", 1)[-1] if args.function else "example"
    # Next to the agent: its code example — the number to beat. The Judge has none.
    example = None
    if (args.function or args.real) and load(None, args.agent) is not None:
        example = agent_metrics.summarize(agent_metrics.measure(args.agent, load(None, args.agent), **part))

    who = "the real agent" if args.real else args.function or "the code example"
    print(f"{args.agent} — {who} · set {set_name}\n")
    if args.agent == "judge" and agent_metrics.judge_cases().get("status") != "approved":
        print(f"  The Judge's cases are a DRAFT — not yet approved clinically ({agent_metrics.JUDGE_CASES.name})\n")
    first = "your agent" if (args.function or args.real) else "code example"
    print(f"  {'':<34}{first:>14}" + (f"{'code example':>14}" if example else ""))
    print(f"  {'':<34}{'right':>14}" + (f"{'right':>14}" if example else ""))
    for check, row in mine.items():
        theirs = example.get(check) if example else None
        cells = f"{row['rate']:>14.0%}" + (f"{theirs['rate']:>14.0%}" if theirs else (" " * 14 if example else ""))
        mark = ""
        if theirs:
            better, same = row["rate"] > theirs["rate"], row["rate"] == theirs["rate"]
            mark = "  ✓" if better else "  =" if same else "  ✗"
        errors = f"   {row['errors']} errors" if row["errors"] else ""
        print(f"  {check:<34}{cells}{mark}{errors}")
    wrong = agent_metrics.wrong(checks)
    print(f"\n  {len(wrong)} wrong of {len(checks)} checks")
    print("\n  right: how often the answer matched the expected answer (target 100%)")
    if example:
        print("  code example: the same checks for a code version that reads only the structured fields —")
        print("  ✓ your agent is better · = the same · ✗ worse")
    elif args.agent != "judge":
        print("  code example: a code version that reads only the structured fields — the number to beat")
    if paid:
        cost = paid.summary()
        print(f"\n  Paid: {cost.get('cost_cases', 0)} items, ${cost.get('cost_total_usd', 0):.4f} "
              f"(mean ${cost.get('cost_mean_usd', 0):.4f}, max ${cost.get('cost_max_usd', 0):.4f})")

    if args.details:
        by = {"department": ("group",), "judge": ("group",)}.get(args.agent, ("challenge", "trap"))
        for key in by:
            label = {"department": "template", "judge": "Judge item"}.get(args.agent) if key == "group" else key
            print(f"\nBy {label}:")
            for group, row in agent_metrics.breakdown(checks, key).items():
                print(f"  {str(group):<22} {row['rate']:>5.0%}   ({row['items']} checks)")
        print(f"\nWrong: {len(wrong)}")
        for c in wrong[:args.show_wrong]:
            print(f"  {c.item:<45} {c.check:<28} expected {c.expected}, got {c.got}"
                  f"{'  ERROR ' + c.error if c.error else ''}")

    output = config.outputs_dir() / f"agent_{args.agent}_{name}.json"
    output.write_text(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2, default=str),
                      encoding="utf8")
    if (args.cases or args.limit) and not args.real:
        print("  a part of the evaluation only — not added to the run history")
        return 0
    run = run or run_log.new_run("agent", args.function or "example", args.label, agent=args.agent)
    numbers = {"set": set_name, **{check: round(row["rate"], 3) for check, row in mine.items()}}
    numbers["errors"] = sum(row["errors"] for row in mine.values())
    if paid:                                    # paid for: always in the history, a part of the evaluation too
        numbers.update(paid.summary())
        numbers["part_only"] = bool(args.cases or args.limit)
    path = run_log.log_run(run, numbers)
    print(f"\n  Saved to your history: {path.parent}  (runs.csv{' · costs.csv' if paid else ''})  — run {run['run_id']}")
    if not args.details:
        print("  By challenge and trap, and every wrong row: --details")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
