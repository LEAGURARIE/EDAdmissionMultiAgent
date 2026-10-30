r"""The comparison report: the single agent against the multi-agent system — quality, latency and cost.

Run (from the project folder), after the test runs of both systems (run_metrics.py --set test --repeat 3 --paid):
    .venv\Scripts\python scripts\compare_systems.py
    .venv\Scripts\python scripts\compare_systems.py --admissions-per-month 900      (adds the monthly cost)

It takes each system's latest 3 test runs and its latest run of each scripted day from the run history, prints the
table and the protocol's reading, and saves comparison_report.csv and comparison_report.md next to the run history
(outputs/, never in Git). Runs that did not decide the same cases are not compared.
Code: src/ed_admission/evaluation/comparison_report.py. Rules: docs/design/comparison_protocol.md.
Owner: Lea Gur-Arie
"""
import argparse
import sys

from ed_admission.evaluation import comparison_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admissions-per-month", type=int,
                        help="admissions in a month, for the monthly cost (no default: never assumed)")
    args = parser.parse_args()
    try:
        report = comparison_report.build(args.admissions_per_month)
    except comparison_report.NotComparable as problem:
        print(f"Not compared: {problem}")
        return 1
    print(comparison_report.markdown(report))
    csv_path, md_path = comparison_report.save(report)
    print(f"Saved: {csv_path} · {md_path}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
