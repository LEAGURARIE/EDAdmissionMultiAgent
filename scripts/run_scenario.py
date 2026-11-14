"""Run one scripted scenario and print the hospital board whenever something changes.

Run (from the project folder):
    .venv\\Scripts\\python scripts\\run_scenario.py                         (test mode: instant)
    .venv\\Scripts\\python scripts\\run_scenario.py --display               (display mode: 1 real second = 5 simulated minutes)
    .venv\\Scripts\\python scripts\\run_scenario.py data\\scenarios\\busy_day.yaml
    .venv\\Scripts\\python scripts\\run_scenario.py --policy none           (nobody decides: the queue only grows)

Policies: code_coordinator (default — the baseline) · single_agent · multi_agent (paid) · none ·
module:function (your own decider).
Owner: Lea Gur-Arie
"""
import argparse
import sys
import time
from pathlib import Path

from ed_admission.agents.budget import RunStopped
from ed_admission.evaluation import deciders
from ed_admission.environment import BedStatus, BedType, load_scenario, to_clock

PROJECT = Path(__file__).resolve().parents[1]
STEP_MINUTES = 5


def board(engine) -> str:
    lines = [f"--- board at {to_clock(engine.clock)} ---"]
    for ward in engine.hospital.wards.values():
        parts = []
        for bed_type in BedType:
            total = ward.total(bed_type)
            if total:
                free = ward.count(bed_type, BedStatus.FREE)
                cleaning = ward.count(bed_type, BedStatus.CLEANING)
                held = ward.count(bed_type, BedStatus.HELD)
                extra = "".join([f", cleaning {cleaning}" if cleaning else "", f", held {held}" if held else ""])
                parts.append(f"{bed_type.value} free {free}/{total}{extra}")
        lines.append(f"  {ward.name:<20} " + " | ".join(parts))
    waiting = ", ".join(p.patient_id for p in engine.hospital.emergency_queue) or "-"
    lines.append(f"  Emergency Department queue: {waiting}")
    return "\n".join(lines)


def summary(engine) -> str:
    lines = ["", "--- placements ---",
             f"  {'patient':<8} {'decided':>8} {'placed':>8} {'wait':>6}  {'ward':<20} {'bed':>3}  bed type"]
    for p in engine.placements:
        lines.append(f"  {p.patient_id:<8} {to_clock(p.decided_at):>8} {to_clock(p.placed_at):>8} "
                     f"{p.wait_minutes:>4}m  {engine.hospital.ward(p.ward_id).name:<20} {p.bed_number:>3}  {p.bed_type}")
    for e in engine.escalations:
        lines.append(f"  {e.patient_id:<8} {to_clock(e.decided_at):>8} {'-':>8} "
                     f"{e.wait_minutes:>4}m  escalated to the shift manager at {to_clock(e.escalated_at)}")
    for p in engine.hospital.emergency_queue:
        lines.append(f"  {p.patient_id:<8} {to_clock(p.decided_at):>8} {'-':>8} "
                     f"{engine.clock - p.decided_at:>4}m  still waiting")
    if engine.placements:
        average = sum(p.wait_minutes for p in engine.placements) / len(engine.placements)
        lines.append(f"  placed {len(engine.placements)}, average wait {average:.0f} minutes")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default=str(PROJECT / "data" / "scenarios" / "busy_day.yaml"))
    parser.add_argument("--display", action="store_true", help="real-time display mode")
    parser.add_argument("--seconds-per-step", type=float, default=1.0)
    parser.add_argument("--policy", default="code_coordinator",
                        help=f"who decides placements: one of {sorted(deciders.DECIDERS)}, none, or module:function")
    parser.add_argument("--paid", action="store_true", help="run a decider that calls a model and is paid for — "
                        "only after its estimated cost was approved")
    args = parser.parse_args()

    engine = load_scenario(Path(args.scenario))
    try:
        engine.policy = None if args.policy == "none" else deciders.resolve(args.policy, paid=args.paid)
    except deciders.PaidWithoutApproval as refused:
        print(refused)
        return
    try:
        with deciders.paid_run(engine.policy):
            play(engine, args)
    except RunStopped as stop:
        print(f"STOPPED — {stop}.")


def play(engine, args) -> None:
    """The day, event by event (or step by step with --display)."""
    print(board(engine))

    printed = 0
    while engine.next_event_time() is not None:
        target = engine.clock + STEP_MINUTES if args.display else engine.next_event_time()
        applied = engine.advance_to(target)
        if applied:
            for entry in engine.log[printed:]:
                print(entry)
            printed = len(engine.log)
            print(board(engine))
        if args.display:
            time.sleep(args.seconds_per_step)

    print(f"\nscenario finished at {to_clock(engine.clock)} — {len(engine.triggers)} admission triggers recorded"
          f" — policy: {args.policy}")
    print(summary(engine))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
