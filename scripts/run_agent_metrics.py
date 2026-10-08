r"""Measure one agent alone, against the part of the expected answers that is its job.

Run (from the project folder):
    .venv\Scripts\python scripts\run_agent_metrics.py --agent severity
        (no --function: the code example of that agent, orchestration/example_agents.py — the number to beat)
    .venv\Scripts\python scripts\run_agent_metrics.py --agent severity --function ed_admission.agents.severity_agent:assess

The signatures (docs/design/agent_interfaces.md, "How to run and measure"):
    clinical_problem, severity, supervision   function(toolbox, patient_id) -> an output of contracts/agent_outputs.py
    department                                function(view, request) -> WardAnswer
    safety_critic                             function(toolbox, patient_id, decision) -> SafetyVerdict

Prints the rate of every check, the rate by challenge and trap (or by template / decider), and every wrong row.
The rows are saved to outputs/agent_<agent>_<function>.json (outputs/ is not uploaded to Git).
Owner: integrator
"""
import argparse
import importlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

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
    parser.add_argument("--show-wrong", type=int, default=30, help="how many wrong rows to print")
    args = parser.parse_args()

    checks = agent_metrics.measure(args.agent, load(args.function, args.agent))
    print(f"agent: {args.agent} — {args.function or 'code example'}\n")
    for check, row in agent_metrics.summarize(checks).items():
        errors = f"   {row['errors']} errors" if row["errors"] else ""
        print(f"  {check:<32} {row['rate']:>5.0%}   {row['right']}/{row['items']}{errors}")

    by = ("group",) if args.agent == "department" else ("challenge", "trap") if args.agent != "safety_critic" \
        else ("challenge", "group")
    for key in by:
        print(f"\nBy {key if key != 'group' else ('template' if args.agent == 'department' else 'decider')}:")
        for name, row in agent_metrics.breakdown(checks, key).items():
            print(f"  {str(name):<22} {row['rate']:>5.0%}   ({row['items']} checks)")

    wrong = agent_metrics.wrong(checks)
    print(f"\nWrong: {len(wrong)}")
    for c in wrong[:args.show_wrong]:
        print(f"  {c.item:<45} {c.check:<28} expected {c.expected}, got {c.got}{'  ERROR ' + c.error if c.error else ''}")

    name = args.function.rsplit(":", 1)[-1] if args.function else "example"
    output = PROJECT / "outputs" / f"agent_{args.agent}_{name}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2, default=str),
                      encoding="utf8")
    print(f"\nsaved to {output.relative_to(PROJECT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
