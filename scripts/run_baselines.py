r"""The three baselines side by side — the system without agents — in the short report's format: safe, suitable, fast.

Run (from the project folder):
    .venv\Scripts\python scripts\run_baselines.py

  first free bed   the safety floor: the first free bed in a regular ward, nothing else
  rules only       the system without agents (the course requires it): structured fields + rank_placements
  today            the simulated coordinator: today's procedure — reads like rules only, but phones the wards

Each baseline runs on every pair, the paired cases and the three scripted days. For one baseline in full detail:
scripts/run_metrics.py --decider <name> --details.
Owner: integrator
"""
import sys
from pathlib import Path

from ed_admission.baselines import first_free_bed, rules_only, simulated_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import metrics, report, run_cases, run_paired

PROJECT = Path(__file__).resolve().parents[1]
DAYS = ["busy_day", "evening_night", "weekend"]
BASELINES = {"first free bed": first_free_bed, "rules only": rules_only, "today": simulated_coordinator}


def measure(decider) -> dict:
    scored = metrics.score_cases(run_cases(decider) + run_cases(decider, evaluation_set="second"))
    scored += metrics.score_paired(run_paired(decider))
    days = {}
    for name in DAYS:
        engine = load_scenario(PROJECT / "data" / "scenarios" / f"{name}.yaml")
        engine.policy = decider
        engine.run_to_end()
        days[name] = metrics.score_day(engine)
    return report.column(scored, days)


def main() -> None:
    columns = {name: measure(decider) for name, decider in BASELINES.items()}
    print("The baselines — the system without agents\n")
    print(report.render(columns))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
