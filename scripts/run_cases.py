r"""Run a decider on the evaluation pairs (every written record in its template) and print one line per case.

Run (from the project folder):
    .venv\Scripts\python scripts\run_cases.py                       (decider: code_coordinator, the baseline)
    .venv\Scripts\python scripts\run_cases.py --cases P-001 P-007   (only these cases)
    .venv\Scripts\python scripts\run_cases.py --set second          (the second pairs file; or: paired)
    .venv\Scripts\python scripts\run_cases.py --decider single_agent --paid   (a model decides: paid)
    .venv\Scripts\python scripts\run_cases.py --decider my_package.my_module:my_decider   (your own decider)

The results are also saved to outputs/cases_<decider>.json (outputs/ is not uploaded to Git).
No comparison with the expected answers here — that is scripts/run_metrics.py.
Owner: Lea Gur-Arie
"""
import argparse
import json
import sys

from ed_admission import config
from ed_admission.agents.budget import RunStopped
from ed_admission.evaluation import deciders
from ed_admission.evaluation import run_cases, run_paired


def describe(decision) -> str:
    if decision is None:
        return "-"
    where = f" {decision.ward_id} bed {decision.bed_number}" if decision.ward_id else ""
    extra = f", awaiting transfer to {decision.awaiting_transfer_to}" if decision.awaiting_transfer_to else ""
    return f"{decision.action.value}{where}{extra}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", default="code_coordinator",
                        help=f"one of {sorted(deciders.DECIDERS)}, or module:function")
    parser.add_argument("--cases", nargs="*", help="case ids, e.g. P-001 P-007; default: every written record")
    parser.add_argument("--set", choices=["first", "second", "paired"], default="first")
    parser.add_argument("--paid", action="store_true", help="run a decider that calls a model and is paid for — "
                        "only after its estimated cost was approved")
    args = parser.parse_args()
    try:
        decider = deciders.resolve(args.decider, paid=args.paid)
    except deciders.PaidWithoutApproval as refused:
        print(refused)
        return

    try:
        with deciders.paid_run(decider):
            if args.set == "paired":
                for result in run_paired(decider):
                    decided = " · ".join(describe(d) for d in result.decisions or [])
                    print(f"{result.case_id:<6} {result.template:<5} {decided}"
                          f"{'  ERROR ' + result.error if result.error else ''}")
                return
            results = run_cases(decider, case_ids=args.cases, evaluation_set=args.set)
    except RunStopped as stop:
        print(f"STOPPED — {stop}. Nothing is saved here; run_metrics.py keeps a partial run in the history.")
        return
    print(f"{'case':<7} {'template':<9} {'seconds':>8}  decision")
    for result in results:
        print(f"{result.case_id:<7} {result.template:<9} {result.seconds:>8.4f}  {describe(result.decision)}"
              f"{'  ERROR ' + result.error if result.error else ''}")
    print(f"\n{len(results)} cases, {sum(1 for r in results if r.error)} errors — decider: {args.decider}")

    output = config.outputs_dir() / f"cases_{deciders.label(args.decider)}.json"
    output.write_text(json.dumps([
        {"case_id": r.case_id, "template": r.template, "seconds": r.seconds, "error": r.error,
         "decision": r.decision.model_dump(mode="json") if r.decision else None} for r in results
    ], ensure_ascii=False, indent=2), encoding="utf8")
    print(f"saved to {output}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
