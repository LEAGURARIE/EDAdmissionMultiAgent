"""The run history — three CSV files that accumulate every run, so improvement can be seen run after run.

  runs.csv         one row per run: who decided (decider, agent), which version, a label saying what changed, and the
                   run's numbers — for the whole system (scripts/run_metrics.py) or for one agent alone
                   (scripts/run_agent_metrics.py). Answers "did it improve?".
  admissions.csv   one row per patient per run of the whole system: what was decided, where, why, how long — and,
                   for the evaluation pairs, whether it matched. Answers "what changed?": which patient was fixed,
                   which was broken.
  costs.csv        one row per case per run of a decider that calls a model: which system (single_agent ·
                   multi_agent), how the case ended, model calls, tokens and cost — in total and per agent. Answers
                   "what did it cost?"; scripts/compare_systems.py sets the single agent against the multi-agent system.
                   runs.csv carries the run's cost summary (metrics.cost_summary).

Both live in config.outputs_dir() — outputs/ locally, a Google Drive folder in Colab — and never go to Git.

The version is computed, never typed: a short fingerprint of the code, prompts, configuration and data. Change a
prompt and the version changes. A rerun of the same decider (or agent) at the same version and with the same label
replaces its earlier rows; a new version, or a new label, adds rows — so the repetitions of a test, each with its own
label, are all kept. A run's traces are saved as traces/<run id>.jsonl; admissions.csv points to them.

Owner: Lea Gur-Arie
"""
import csv
import hashlib
from datetime import datetime
from pathlib import Path

from ed_admission import config
from ed_admission.environment import load_hospital
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import to_clock

RUNS, ADMISSIONS, COSTS = "runs.csv", "admissions.csv", "costs.csv"
FINGERPRINTED = ("src", "config", "data")          # what makes a version: code, prompts, rules, data


def version() -> str:
    """A short fingerprint of everything that can change a result."""
    digest = hashlib.sha256()
    for folder in FINGERPRINTED:
        for path in sorted((config.PROJECT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(config.PROJECT).as_posix().encode())
                digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:10]


def new_run(kind: str, decider: str, label: str = "", agent: str = "") -> dict:
    """The fields every row of one run starts with. kind: system (the whole decision) or agent (one agent alone)."""
    now = datetime.now()
    who = f"{agent}-{decider.rsplit(':', 1)[-1]}" if agent else decider.rsplit(":", 1)[-1]
    return {"run_id": f"{now:%Y%m%d-%H%M%S}-{who}", "time": f"{now:%Y-%m-%d %H:%M}",
            "kind": kind, "decider": decider, "agent": agent, "version": version(), "label": label}


def _same_run(row: dict, run: dict) -> bool:
    """The same run again: the same decider at the same version with the same label. A different label is another
    run — the repetitions of a test (run_metrics.py --repeat) each have their own, so none replaces another."""
    return all(row.get(k, "") == run[k] for k in ("kind", "decider", "agent", "version", "label"))


def _write(name: str, run: dict, rows: list[dict], keep_this_run: bool = False) -> Path:
    """Replace this decider's earlier rows at the same version, append the new ones; the header grows with new
    columns, so old rows keep their place. `keep_this_run`: the rows this very run already wrote stay — a run that
    writes one row at a time (append_costs)."""
    path = config.outputs_dir() / name
    kept, header = [], []
    if path.exists():
        with path.open(encoding="utf8", newline="") as file:
            reader = csv.DictReader(file)
            header = list(reader.fieldnames or [])
            kept = [r for r in reader if not _same_run(r, run)
                    or (keep_this_run and r.get("run_id") == run["run_id"])]
    for row in rows:
        header += [k for k in row if k not in header]
    with path.open("w", encoding="utf8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=header)
        writer.writeheader()
        writer.writerows(kept + rows)
    return path


def log_run(run: dict, numbers: dict) -> Path:
    """One row in runs.csv."""
    return _write(RUNS, run, [{**run, **numbers}])


def log_admissions(run: dict, rows: list[dict]) -> Path:
    """The run's rows in admissions.csv."""
    return _write(ADMISSIONS, run, [{**run, **row} for row in rows])


def log_costs(run: dict, rows: list[dict]) -> Path:
    """The run's rows in costs.csv — one per case."""
    return _write(COSTS, run, [{**run, **row} for row in rows])


def append_costs(run: dict, rows: list[dict]) -> Path:
    """Add rows to this run's rows in costs.csv, as each paid item ends — so a run stopped half way keeps the record
    of what it paid for. An earlier run with the same decider, version and label is still replaced."""
    return _write(COSTS, run, [{**run, **row} for row in rows], keep_this_run=True)


def cost_row(source: str, trace) -> dict:
    """One case's cost, from its trace. One column per agent (cost_<role>_usd): the header grows with new agents."""
    return {"source": source, "case_id": trace.case_id, "system": trace.system, "ended": trace.ended,
            "model_calls": trace.model_calls, "tokens_in": trace.tokens_in, "tokens_out": trace.tokens_out,
            "cost_usd": round(trace.cost_usd, 6),
            **{f"cost_{role}_usd": round(cost, 6) for role, cost in sorted(trace.cost_by_role.items())}}


# ---------------------------------------------------------------------- building the rows

def _bed_type(ward_id: str | None, bed_number: int | None) -> str:
    if ward_id is None:
        return ""
    return load_hospital().ward(ward_id).bed(bed_number).bed_type.value


def decision_row(decision: Decision | None) -> dict:
    """What was decided, in the log's columns."""
    if decision is None:
        return {"action": "no decision"}
    return {"action": decision.action.value, "target_ward": decision.target_ward or "",
            "ward_id": decision.ward_id or "", "bed_number": decision.bed_number or "",
            "bed_type": _bed_type(decision.ward_id, decision.bed_number),
            "ladder_level": decision.ladder_level or "",
            "temporary": decision.action.value == "temporary_place",
            "waits_for": decision.wait_for.ward_id if decision.wait_for else "",
            "flags": " ".join(f.value for f in decision.flags), "reason": decision.reason}


def scored_row(source: str, decision: Decision | None, scored, trace: str = "") -> dict:
    """One evaluation pair (or one patient of a paired case): the decision and how it scored."""
    return {"source": source, "case": scored.paired_case or scored.template, "patient_id": scored.case_id,
            **decision_row(decision), "expected": scored.expected, "match": scored.match,
            "violations": " ".join(scored.violations), "need_met": "" if scored.need_met is None else scored.need_met,
            "bed_not_available": scored.bed_not_available, "missing_flags": " ".join(scored.missing_flags),
            "decision_seconds": round(scored.seconds, 3), "error": scored.error, "trace": trace}


def day_rows(day: str, engine) -> list[dict]:
    """A scripted day after it ran: one row per patient — placed, escalated, or still waiting at the end — with its
    cases at the case desk: how many, minutes in the queue, conflicts."""
    rows = []
    for p in engine.placements:
        rows.append({"source": f"day:{day}", "patient_id": p.patient_id, "outcome": "placed",
                     "target_ward": p.target_ward or "", "ward_id": p.ward_id, "bed_number": p.bed_number,
                     "bed_type": p.bed_type, "temporary": p.awaiting_transfer_to is not None,
                     "ladder_level": config.ladder_level(p.awaiting_transfer_to, p.ward_id)
                     if p.awaiting_transfer_to else 1,
                     "reason": p.reason, "admission_decision_at": to_clock(p.decided_at),
                     "bed_at": to_clock(p.placed_at), "minutes_to_bed": p.wait_minutes,
                     "decider_minutes": p.deciding_minutes, "shift_manager": "approved"})
    for e in engine.escalations:
        rows.append({"source": f"day:{day}", "patient_id": e.patient_id, "outcome": "escalated",
                     "target_ward": e.target_ward or "", "reason": e.reason, "flags": " ".join(e.flags),
                     "admission_decision_at": to_clock(e.decided_at), "escalated_at": to_clock(e.escalated_at),
                     "minutes_to_shift_manager": e.wait_minutes})
    for w in engine.hospital.emergency_queue:
        rows.append({"source": f"day:{day}", "patient_id": w.patient_id, "outcome": "still waiting",
                     "admission_decision_at": to_clock(w.decided_at),
                     "minutes_waited": engine.clock - w.decided_at})
    for row in rows:
        cases = [c for c in engine.cases if row["patient_id"] in c["patients"]]
        row.update(cases=len(cases), minutes_in_queue=sum(c["opened_at"] - c["queued_at"] for c in cases),
                   conflicts=sum(row["patient_id"] in c["conflicts"] for c in cases))
    return rows
