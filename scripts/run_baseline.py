r"""The baseline — the Code Coordinator: today's procedure, written as code. No agents, no language model.

Run (from the project folder):
    .venv\Scripts\python scripts\run_baseline.py

It reads the record's structured fields — not the physician's free text — looks at the wards' reports, and phones each
ward to ask what it can really give (src/ed_admission/baselines/code_coordinator.py). The system is judged against
it: safe, suitable, fast. It runs on every case, the paired cases and the three scripted days. In full detail:
scripts/run_metrics.py --decider code_coordinator --details.
Owner: Lea Gur-Arie
"""
import sys
from pathlib import Path

from ed_admission.baselines import code_coordinator
from ed_admission.environment import load_scenario
from ed_admission.evaluation import metrics, report, run_cases, run_paired

PROJECT = Path(__file__).resolve().parents[1]
DAYS = ["busy_day", "evening_night", "weekend"]
CONTENTION_DAY = "competing_for_a_bed"           # shown apart: not in the minutes to a bed


def main() -> None:
    scored = metrics.score_cases(run_cases(code_coordinator) + run_cases(code_coordinator, evaluation_set="second"))
    scored += metrics.score_paired(run_paired(code_coordinator))
    days = {}
    for name in DAYS:
        engine = load_scenario(PROJECT / "data" / "scenarios" / f"{name}.yaml")
        engine.policy = code_coordinator
        engine.run_to_end()
        days[name] = metrics.score_day(engine, name)
    print("The baseline — the Code Coordinator: today's procedure, written as code\n")
    print(report.render({"code coordinator": report.column(scored, days)}))
    engine = load_scenario(PROJECT / "data" / "scenarios" / f"{CONTENTION_DAY}.yaml")
    engine.policy = code_coordinator
    engine.run_to_end()
    print(report.moments_line("code coordinator", metrics.contention_moments(CONTENTION_DAY, engine)))
    summary = metrics.summarize(scored)
    with_need = [s for s in scored if s.need_met is not None]
    placed = [s for s in scored if s.decided.startswith(("place", "temporary_place"))]

    def share(count: int, of: int) -> str:
        return f"{count} of {of} ({count / of:.0%})" if of else "0"

    print("\n  In numbers:")
    print(f"    a hard rule broken        {share(sum(1 for s in scored if s.violations), len(scored))} decisions")
    print(f"    needs met or escalated    {share(sum(bool(s.need_met) for s in with_need), len(with_need))} "
          "patients who need observation or isolation")
    print(f"    bed the ward can't give   {share(summary['bed_not_available'], len(placed))} placements")
    print(f"    match                     {share(sum(s.match for s in scored), len(scored))} decisions")
    print(f"    escalated                 {share(summary['escalations'], len(scored))} decisions")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
