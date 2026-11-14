r"""The demo replay: a placement case played step by step on one screen — the admission, the Patient Agent reading the
record, the Department Agent going ward by ward, the code checks, the Judge Agent and the shift manager.

Run (from the project folder):
    .venv\Scripts\python scripts\build_demo_replay.py

Where the cases come from — nothing is called, nothing is invented; every step was logged in a real run: the frozen
multi-agent system's development run in the run history (outputs/runs.csv, admissions.csv and its trace) — every
development pair, exactly as it was measured. The run is the latest complete development run of the version the
comparison report reads on the test set; side checks (the Opus arm …) are never taken. Only development pairs: the
test set is sealed. Next to each decision: the expected answer, and the single agent's decision on the same pair from its frozen
development run. The page: scripts/demo_replay_page.html (the data goes in its CASES placeholder); the result:
presentation/demo_replay.html — open it in a browser, no server, no internet.

What a run in the history does not keep: the code checks one by one. The Judge Agent runs only on a proposal that
passed every code check, so a case with a verdict shows those checks as passed, without their one-line reasons.

Owner: Lea Gur-Arie
"""
import csv
import json
import sys
from pathlib import Path

from ed_admission import config
from ed_admission.database import run_log
from ed_admission.environment import load_template
from ed_admission.evaluation import comparison_report, metrics, sets
from ed_admission.evaluation.agent_metrics import _answers
from ed_admission.evaluation.case_runner import TEMPLATES, load_pairs
from ed_admission.tools.patient_record import read_patient_record

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).with_name("demo_replay_page.html")
OUT = ROOT / "presentation" / "demo_replay.html"
NEEDS = ("severe", "intensive_care", "observation", "isolation")


# ============ the development pairs and their hospital ============
def dev_pairs() -> list[dict]:
    """The development pairs, with the pairs file each one is in."""
    dev = set(sets.pairs_of("dev"))
    return [{**pair, "pairs_file": name} for name in ("first", "second") for pair in load_pairs(name)
            if (pair["case_id"], pair["template"]) in dev]


def hospital_of(pair: dict):
    """The hospital at the moment of the decision, from the pair's frozen ward-state template."""
    return load_template(TEMPLATES / f"{pair['template']}.yaml", target_ward=pair["target_ward"])


def wards_of(engine) -> dict:
    """Every ward's report and private notes, at the moment of the decision."""
    return {ward_id: {"report": engine.department_status(ward_id), "notes": engine.ward_notes(ward_id)}
            for ward_id in engine.hospital.wards}


def expected_answer(pair: dict) -> dict | None:
    return next((a for a in _answers() if (a["case_id"], a["template"]) == (pair["case_id"], pair["template"])), None)


# ============ the run history ============
def _rows(name: str) -> list[dict]:
    with (config.outputs_dir() / name).open(encoding="utf8") as file:
        return list(csv.DictReader(file))


def frozen_dev_run(runs: list[dict]) -> str | None:
    """The multi-agent system's latest complete development run of the version measured on the test set."""
    chosen = comparison_report.latest_test_runs(runs)["multi_agent"]
    if not chosen:
        return None
    version = chosen[0]["version"]
    dev = [r for r in runs if r.get("system") == "multi_agent" and r.get("set") == "dev"
           and r.get("version") == version and comparison_report._complete(r) and not comparison_report._side_check(r)]
    return max((r["run_id"] for r in dev), default=None)


def latest_dev_run(runs: list[dict], system: str) -> str | None:
    dev = [r for r in runs if r.get("system") == system and r.get("set") == "dev" and comparison_report._complete(r)]
    return max((r["run_id"] for r in dev), default=None)


def decisions_of(admissions: list[dict], run_id: str | None) -> dict:
    return {(r["case"], r["patient_id"]): r for r in admissions if r["run_id"] == run_id}


def traces_of(run_id: str) -> dict[str, dict]:
    path = config.outputs_dir() / "traces" / f"{run_id}.jsonl"
    with path.open(encoding="utf8") as file:
        return {trace["case_id"]: trace for trace in map(json.loads, file)}


# ============ one case, in one shape, from either source ============
def _decision_from_row(row: dict) -> dict:
    return {"action": row["action"], "ward_id": row["ward_id"] or None,
            "bed_number": int(row["bed_number"]) if row["bed_number"] else None,
            "wait_for": {"ward_id": row["waits_for"]} if row["waits_for"] else None,
            "awaiting_transfer_to": row["target_ward"] if row["temporary"] == "True" else None,
            "flags": row["flags"].split(), "reason": row["reason"]}


def from_history(pair: dict, row: dict, trace: dict) -> dict:
    """A case of the frozen development run: the agents' outputs from its trace, the decision from admissions.csv,
    the record and the wards (their reports and private notes) rebuilt from the frozen files — no model."""
    outputs = lambda name: [s["output"] for s in trace["steps"] if s["kind"] == "tool_call" and s["name"] == name]
    assessments, verdicts = outputs("record_assessment"), outputs("give_verdict")
    engine = hospital_of(pair)
    any_report = engine.department_status(next(iter(engine.hospital.wards)))
    decision = _decision_from_row(row)
    return {
        "pair": pair, "record": read_patient_record(pair["case_id"]), "wards": wards_of(engine),
        "decided": any_report["time"], "target": row["target_ward"],
        "assessment": assessments[-1] if assessments else None,
        "visits": outputs("answer_ward"),
        "code_passed": bool(verdicts),               # the Judge Agent runs only on a proposal every code check passed
            "verdict": verdicts[-1] if verdicts else None,
        "corrections": [v["correction"] for v in verdicts[:-1] if not v["valid"] and v.get("correction")],
        "decision": decision, "escalation": row["reason"] if row["action"] == "escalate" else None,
        "expected": expected_answer(pair),
        "cost": sum(s["cost_usd"] for s in trace["steps"] if s["kind"] == "agent_call"),
        "seconds": float(row["decision_seconds"] or 0), "source": f"run {row['run_id']}"}


# ============ what the page shows ============
def ward_name(case: dict, ward_id: str | None) -> str | None:
    if not ward_id:
        return None
    return case["wards"].get(ward_id, {}).get("report", {}).get("name", ward_id.replace("_", " ").title())


def describe(case: dict, decision: dict | None) -> str:
    """One line for a decision: the action, the ward and the bed — or the ward waited for."""
    if not decision:
        return "—"
    action = decision["action"].replace("_", " ")
    if decision.get("ward_id"):
        return f"{action} · {ward_name(case, decision['ward_id'])}, bed {decision['bed_number']}"
    if decision.get("wait_for"):
        return f"{action} · for {ward_name(case, decision['wait_for'].get('ward_id'))}"
    return action


def expected(case: dict) -> dict:
    """The expected answer the decision is scored against — by the occupancy rule when the record names neither
    internal medicine A nor B, as in every measured run."""
    answer = case["expected"]
    scored = metrics.by_the_rules(answer)["expected"]
    ward = scored.get("ward_id") or (scored.get("wait_for") or {}).get("ward_id")
    return {"action": scored["action"].replace("_", " "), "ward": ward_name(case, ward),
            "rationale": answer.get("rationale", ""), "by_occupancy": metrics.names_neither_internal_medicine(answer)}


def checks(case: dict) -> list[dict]:
    rubric = config.judge_rubric()
    if case["code_passed"]:
        return [{"number": n, "name": item["name"], "passed": True, "reason": ""}
                for n, item in rubric.items() if item["checked_by"] == "code"]
    return []


def shown(case: dict, single: dict) -> dict:
    """What the page plays for one case."""
    record, assessment, verdict, decision = case["record"], case["assessment"], case["verdict"], case["decision"]
    rubric = config.judge_rubric()
    single_row = single.get((case["pair"]["template"], case["pair"]["case_id"]))
    return {
        "id": case["pair"]["case_id"], "template": case["pair"]["template"], "source": case["source"],
        "patient": f"{record['patient']['sex']}, {record['patient']['age']}", "triage": record["triage_level"],
        "complaint": record["chief_complaint"].replace("_", " "), "decided": case["decided"],
        "day": next(iter(case["wards"].values()))["report"]["day"],
        "notes": {"triage": record["triage_note"], "physician": record["physician_note"]},
        "assessment": None if not assessment else {
            "target": ward_name(case, case["target"]), "binding": assessment["binding_request"],
            "needs": {n: assessment[n] for n in NEEDS}, "flags": [f.replace("_", " ") for f in assessment["flags"]],
            "findings": [{"claim": f["claim"], "quote": f["quote"], "confidence": f["confidence"]}
                         for f in assessment["findings"]]},
        "wards": [{"id": ward_id, "name": ward_name(case, ward_id), "rung": config.ladder_level(case["target"], ward_id),
                   "free": [f"bed {b['bed']} ({b['bed_type']})" for b in ward["report"].get("free_beds", [])],
                   "notes": ward.get("notes", [])} for ward_id, ward in case["wards"].items()],
        "visits": [{"ward": v["ward_id"], "answer": v["answer"], "bed": v.get("bed_number"), "reason": v["reason"],
                    "condition": v.get("condition"), "minutes": v.get("available_in_minutes")}
                   for v in case["visits"]],
        "proposal": None if not case["code_passed"] else {
            "line": describe(case, decision), "checks": checks(case)},
        "verdict": None if not verdict else {
            "valid": verdict["valid"], "corrections": case["corrections"],
            "items": [{"number": i["number"], "name": rubric[i["number"]]["name"], "passed": i["passed"],
                       "reason": i["reason"]} for i in verdict["items"]]},
        "decision": {"line": describe(case, decision), "reason": decision["reason"],
                     "awaiting": ward_name(case, decision.get("awaiting_transfer_to")),
                     "flags": [f.replace("_", " ") for f in decision.get("flags", [])],
                     "escalation": case["escalation"]},
        "expected": expected(case),
        "single_agent": None if single_row is None else {
            "line": describe(case, _decision_from_row(single_row)), "reason": single_row["reason"]},
        "cost": round(case["cost"], 3), "seconds": round(case["seconds"])}


def matches(case: dict) -> bool:
    """The system's decision is the expected one: the same action, and the same ward when one is expected."""
    line, want = case["decision"]["line"], case["expected"]
    return line.split(" · ")[0] == want["action"] and (not want["ward"] or want["ward"] in line)


def main() -> int:
    runs, admissions = _rows(run_log.RUNS), _rows(run_log.ADMISSIONS)
    multi_run, single_run = frozen_dev_run(runs), latest_dev_run(runs, "single_agent")
    print(f"The multi-agent system: run {multi_run} · the single agent: run {single_run}")
    single = decisions_of(admissions, single_run)
    cases = []
    if multi_run:
        rows, traces = decisions_of(admissions, multi_run), traces_of(multi_run)
        for pair in dev_pairs():
            row = rows.get((pair["template"], pair["case_id"]))
            if row is not None and pair["case_id"] in traces:
                cases.append(from_history(pair, row, traces[pair["case_id"]]))
    if not cases:
        print("No case: no development run of the frozen system in the run history.")
        return 1
    page_cases = [shown(case, single) for case in cases]
    for case in page_cases:
        case["matches"] = matches(case)
    # the order on the screen: decisions that match the expected answer first, a placement before an escalation, the
    # longest walk through the wards first — the process at its clearest — then the misses
    page_cases.sort(key=lambda c: (not c["matches"], c["decision"]["line"] == "escalate", -len(c["visits"]), c["id"]))
    page = PAGE.read_text(encoding="utf8")
    if page.count("/*CASES*/[]") != 1:
        raise SystemExit(f"{PAGE.name}: the CASES placeholder is missing")
    OUT.parent.mkdir(exist_ok=True)
    data = json.dumps(page_cases, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(page.replace("/*CASES*/[]", data), encoding="utf8")
    print(f"{len(page_cases)} cases ({sum(c['matches'] for c in page_cases)} match the expected answer) → {OUT}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
