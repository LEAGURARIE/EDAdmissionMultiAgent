"""Measuring one agent alone — so it is clear where each agent stands before the whole system works.

Every agent is compared with the part of the frozen expected answers that is its job. The agent never sees them:
it gets a toolbox with its own role, or, for the Department Agent, only its ward's view and the request.

  patient       per record   everything the Patient Agent decides, each as its own check:
                             target ward — internal medicine that the record does not name as A or B:
                             "internal_medicine" is right (and A or B too), the Coordinator chooses by occupancy
                             binding request · severe · intensive care · observation · isolation
                             flags prompt_injection, missing_vital_sign, severity_judgment_note
  department    per request  a bed now or not, as the ward really is (its notes included) · an accepted bed is
                             really free. Requests: every ward on each pair's ladder, for the bed type the patient
                             needs. `the note decides`: the requests where the report alone gives the wrong answer
                             — what the Department Agent is there for.

  judge         per case     the Judge Agent on cases built for it (data/answer_key/judge_cases.yaml): each of its
                             items (2, 8, 9) passed or not as expected · a wrong placement caught (not valid) · a
                             right placement let through (valid) — what it catches, and what it rejects for nothing

The real agents (RealAgents, below): each item runs one agent of the multi-agent system — its own loop, prompt and
tools (agents/orchestra_agents.py) — with a fresh trace; after each item `on_item(source, trace)` is called, so the
script writes the item's cost at once. The run's budget stops the measurement (RunStopped is never a wrong row).
Sets: the real agents are measured on the development set only — the test set stays sealed for the final
comparison (docs/design/comparison_protocol.md); the code examples, which cost nothing, on every record.

The Coordinator is measured on the whole decision (scripts/run_metrics.py).
Records: the 74 records of both pair files. Each check is one row; summarize() counts them by check, and by challenge,
trap or template. An agent that raises an error gets the row wrong, with the error kept.

Only the evaluation package reads the expected answers. Owner: Lea Gur-Arie
"""
from collections import defaultdict
from dataclasses import dataclass, field

import re

import yaml

from ed_admission import config
from ed_admission.agents import orchestra_agents
from ed_admission.agents.budget import RunStopped
from ed_admission.contracts.agent_outputs import (AssessmentWithConfidence, PatientTask, PlacementProposal,
                                                  WardSummary, WardTask)
from ed_admission.evaluation import sets
from ed_admission.observability.tracing import Trace
from ed_admission.orchestration.state import CaseState
from ed_admission.tools.patient_record import quote_in_record
from ed_admission.contracts.negotiation import Answer, WardRequest
from ed_admission.environment import load_template
from ed_admission.environment.hospital import BedStatus, BedType
from ed_admission.environment.ward_truth import true_state
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation.case_runner import TEMPLATES, load_pairs
from ed_admission.tools.negotiation import DepartmentView
from ed_admission.tools.patient_record import read_patient_record
from ed_admission.tools.toolbox import Toolbox

AGENTS = ("patient", "department", "judge")
JUDGE_CASES = config.PROJECT / "data" / "answer_key" / "judge_cases.yaml"
JUDGE_ITEMS = (2, 8, 9)


@dataclass
class Check:
    """One comparison of an agent's output with the expected answer."""
    agent: str
    check: str                   # e.g. target_ward, severe, bed_now
    item: str                    # a record, a request, or a decision
    expected: object
    got: object
    ok: bool
    challenge: int | None = None
    traps: list[int] = field(default_factory=list)
    group: str = ""              # the template (department)
    error: str = ""


# ---------------------------------------------------------------------- the answers

def _answers() -> list[dict]:
    """Every pair's expected answer, in both pair files."""
    pairs = load_pairs("first") + load_pairs("second")
    return [yaml.safe_load(builder.answer_path(p).read_text(encoding="utf8")) for p in pairs]


def records() -> dict[str, dict]:
    """One truth per record: target ward, needs, challenge, traps, the template of its first pair, and the flags
    any of its pairs requires."""
    truth = {}
    for answer in _answers():
        row = truth.setdefault(answer["case_id"], {
            "target_ward": answer["target_ward"], "needs": answer["needs"], "challenge": answer["challenge"],
            "traps": answer["traps"], "template": answer["template"], "wording": answer["wording"],
            "flags": set()})
        row["flags"] |= set(answer["expected"]["required_flags"])
    for patient_id, row in truth.items():
        row["names_internal_medicine"] = names_internal_medicine(patient_id)
    return truth


INTERNAL = ("internal_medicine_a", "internal_medicine_b")
# "Internal medicine" in Hebrew followed by the letter A or B as a word of its own (alone, or with a geresh) — not the
# first letter of the next word, as in "admit to internal medicine in isolation".
NAMED_INTERNAL = re.compile("פנימית\\s*[אב](?:['׳]|(?![֐-׿]))")


def names_internal_medicine(patient_id: str) -> bool:
    """Does the record itself say Internal Medicine A or B — a physician's request, or the Hebrew for it in the text?"""
    record = read_patient_record(patient_id)
    text = " ".join([record["physician_note"], record.get("triage_note") or ""])
    return record["physician_request"]["ward"] in INTERNAL or NAMED_INTERNAL.search(text) is not None


def _target_ward(truth: dict) -> tuple[str, set[str]]:
    """The expected target ward as shown, and every answer counted right."""
    target = truth["target_ward"]
    if target in INTERNAL and not truth["names_internal_medicine"]:
        return "internal_medicine", {"internal_medicine", *INTERNAL}
    return target, {target}


def _engine(template: str, target_ward: str):
    return load_template(TEMPLATES / f"{template}.yaml", target_ward=target_ward)


def _call(function, *arguments):
    try:
        return function(*arguments), ""
    except RunStopped:                                      # the run's money is spent: the measurement stops
        raise
    except Exception as failure:                            # an agent's failure is a wrong row, not a crash
        return None, f"{type(failure).__name__}: {failure}"


def in_set(patient_id: str, template: str, set_name: str) -> bool:
    """Is this pair in the set? "all": every pair."""
    chosen = sets.pairs_of(set_name)
    return chosen is None or (patient_id, template) in chosen


# ---------------------------------------------------------------------- the real agents

class AgentEndedWithoutAnswer(Exception):
    """The agent's loop ended without its closing tool — no_final, refused, failed or its own budget."""


class RealAgents:
    """The multi-agent system's real agents, one item at a time, each with a fresh trace. `on_item(source, trace)`
    runs after every item — the script writes its cost at once. `run_budget`: the run's money; `caller`: one
    LangChainCaller for the run."""

    def __init__(self, run_budget=None, caller=None, on_item=None):
        self.run_budget, self.caller, self.on_item = run_budget, caller, on_item or (lambda source, trace: None)

    def _run(self, role: str, engine, item: str, call):
        trace = Trace(case_id=item, decider="multi_agent")
        agents = orchestra_agents.build(engine, trace, self.run_budget, self.caller)
        answer = {}
        try:
            answer, _ = call(getattr(agents, role))
        finally:                                            # paid for, answer or not — a stop included
            statuses = [s.output.get("status") for s in trace.steps
                        if s.kind == "agent_call" and isinstance(s.output, dict)]
            trace.ended = next((x for x in statuses if x != "final"), "final")
            self.on_item(f"agent:{role}", trace)
        if answer == {}:
            raise AgentEndedWithoutAnswer(f"the {role} agent ended without its answer: {trace.ended}")
        return answer

    def patient(self, engine, patient_id: str):
        return self._run("patient", engine, patient_id,
                         lambda agent: agent(PatientTask(patient_id=patient_id, decided_at=engine.clock)))

    def department(self, engine, task: WardTask, item: str):
        return self._run("department", engine, item, lambda agent: agent(task))

    def judge(self, engine, state: CaseState, item: str):
        return self._run("judge", engine, item, lambda agent: agent(state))


# ---------------------------------------------------------------------- the Patient Agent

def patient_checks(truth: dict) -> dict:
    """Every check of the Patient Agent for one record: the expected value, or (shown value, every right answer)."""
    needs, flags = truth["needs"], truth["flags"]
    return {"target_ward": _target_ward(truth), "binding_request": truth["wording"] == "binding_request",
            "severe": needs["severe"], "intensive_care": needs["intensive_care"],
            "observation": needs["observation"], "isolation": needs["isolation"],
            "flag prompt_injection": "prompt_injection" in flags,
            "flag missing_vital_sign": "missing_vital_sign" in flags,
            "flag severity_judgment_note": "severity_judgment_note" in flags}


def _got(output, check: str):
    if output is None:
        return None
    if check.startswith("flag "):
        return check[5:] in {getattr(f, "value", f) for f in getattr(output, "flags", [])}
    return getattr(output, check)


def patients_in(set_name: str) -> list[str]:
    """The records of a set: a record is in it when one of its pairs is."""
    chosen = sets.pairs_of(set_name)
    if chosen is None:
        return sorted(records())
    return sorted({patient_id for patient_id, _ in chosen})


def measure_patient(function, cases: list[str] | None = None, real: RealAgents | None = None) -> list[Check]:
    """`function(toolbox, patient_id)` -> PatientAssessment — or, with `real`, the real Patient Agent. `cases`: only
    these records. The real agent is also checked on its quotes: every one in the record."""
    checks = []
    for patient_id, truth in sorted(records().items()):
        if cases is not None and patient_id not in cases:
            continue
        engine = _engine(truth["template"], truth["target_ward"])
        if real is None:
            output, error = _call(function, Toolbox("patient", engine), patient_id)
        else:
            output, error = _call(real.patient, engine, patient_id)
        for check, expected in patient_checks(truth).items():
            got = _got(output, check)
            expected, right = expected if isinstance(expected, tuple) else (expected, {expected})
            checks.append(Check("patient", check, patient_id, expected, got, got in right,
                                truth["challenge"], truth["traps"], error=error))
        if real is not None:
            quotes = [f.quote for f in getattr(output, "findings", [])]
            grounded = output is not None and all(quote_in_record(patient_id, q) for q in quotes)
            checks.append(Check("patient", "quotes in the record", patient_id, True, grounded, grounded,
                                truth["challenge"], truth["traps"], error=error))
    return checks


# ---------------------------------------------------------------------- the Department Agent

def _bed_type(needs: dict) -> str:
    if needs["intensive_care"]:
        return "intensive_care"
    if needs["isolation"]:
        return "isolation"
    return "observation" if needs["observation"] else "regular"


def _free(engine, ward_id: str, bed_type: str) -> set[int]:
    return {b.number for b in engine.hospital.ward(ward_id).beds
            if b.bed_type == BedType(bed_type) and b.status == BedStatus.FREE}


def _stable(r: dict) -> bool:
    """Code's answer to a ward's question "stable" — the same reading as AssessmentWithConfidence.stable: not severe,
    no observation, no intensive care."""
    return not (r["severe"] or r["observation"] or r["bed_type"] == "intensive_care")


def requests(set_name: str = "all") -> list[dict]:
    """One request to every ward on each pair's ladder, for the bed type the patient needs; repeats counted once.
    `set_name`: only the pairs of that set."""
    seen, out = set(), []
    for answer in _answers():
        if not in_set(answer["case_id"], answer["template"], set_name):
            continue
        bed_type, target = _bed_type(answer["needs"]), answer["target_ward"]
        for ward_id in (w for level in config.ladders()[target] for w in level):
            key = (answer["template"], target, ward_id, bed_type, answer["needs"]["severe"])
            if key not in seen:
                seen.add(key)
                out.append({"template": answer["template"], "target_ward": target, "ward_id": ward_id,
                            "bed_type": bed_type, "severe": answer["needs"]["severe"],
                            "observation": answer["needs"]["observation"], "isolation": answer["needs"]["isolation"]})
    return out


def measure_department(responder, limit: int | None = None, agents: RealAgents | None = None,
                       set_name: str = "all") -> list[Check]:
    """`responder(view, request)` -> WardAnswer — the same signature the negotiation uses — or, with `real`, the
    real Department Agent (`agents`), given the ward's task (WardTask). `limit`: only the first requests — the ones the note
    decides first, so a short try still tests what the agent is there for."""
    checks = []
    for r in _ordered(requests(set_name), limit):
        engine = _engine(r["template"], r["target_ward"])
        real = _free(true_state(engine, patient_target=r["target_ward"]), r["ward_id"], r["bed_type"])
        shown = _free(engine, r["ward_id"], r["bed_type"])
        view = DepartmentView(r["ward_id"], engine.department_status(r["ward_id"]), engine.ward_notes(r["ward_id"]))
        request = WardRequest(bed_type=r["bed_type"], severe=r["severe"], observation=r["observation"],
                              isolation=r["isolation"], target_ward=r["target_ward"],
                              proposed_bed=min(shown) if shown else None)
        item = f"{r['template']} {r['ward_id']} {r['bed_type']} for {r['target_ward']}"
        if agents is None:
            answer, error = _call(responder, view, request)
        else:
            answer, error = _call(agents.department, engine, WardTask(ward_id=r["ward_id"], request=request), item)
            if answer is not None and answer.answer == Answer.CONDITION and answer.asks == "stable":
                # as the graph does: code answers the closed question "stable" and the ward is asked again, once
                answer, error = _call(agents.department, engine,
                                      WardTask(ward_id=r["ward_id"], request=request,
                                               patient_answer=_stable(r)), item)
        bed_now = answer is not None and answer.answer == Answer.ACCEPT
        note_decides = bool(real) != bool(shown)
        checks.append(Check("department", "bed_now", item, bool(real), bed_now, bed_now == bool(real),
                            group=r["template"], error=error))
        if note_decides:
            checks.append(Check("department", "bed_now, the note decides", item, bool(real), bed_now,
                                bed_now == bool(real), group=r["template"], error=error))
        if bed_now:
            checks.append(Check("department", "accepted bed really free", item, True, answer.bed_number in real,
                                answer.bed_number in real, group=r["template"], error=error))
    return checks


# ---------------------------------------------------------------------- one entry, and the counts

def _ordered(all_requests: list[dict], limit: int | None) -> list[dict]:
    if limit is None:
        return all_requests

    def note_decides(r: dict) -> bool:
        engine = _engine(r["template"], r["target_ward"])
        real = _free(true_state(engine, patient_target=r["target_ward"]), r["ward_id"], r["bed_type"])
        return bool(real) != bool(_free(engine, r["ward_id"], r["bed_type"]))
    return sorted(all_requests, key=lambda r: not note_decides(r))[:limit]


def measure(agent: str, function, cases: list[str] | None = None, limit: int | None = None,
            real: RealAgents | None = None, set_name: str | None = None) -> list[Check]:
    """`cases`: only these records (patient). `limit`: only this many records, requests or Judge cases — a first try
    on a few. `real`: the real agent instead of `function`. `set_name`: the pairs measured — the real agents never
    on the test set; by default the development set for them and every pair for a code function."""
    set_name = set_name or ("dev" if real is not None else "all")
    if real is not None and set_name == "test":
        raise ValueError("the test set is sealed until the final comparison: measure the real agents on dev")
    if agent == "patient":
        allowed = patients_in(set_name)
        cases = [c for c in (cases or allowed) if c in allowed]
        if limit is not None:
            cases = cases[:limit]
        return measure_patient(function, cases, real)
    if agent == "department":
        return measure_department(function, limit, real, set_name)
    if agent == "judge":
        return measure_judge(function, limit, real)
    raise ValueError(f"unknown agent {agent!r}: one of {AGENTS}")


# ---------------------------------------------------------------------- the Judge Agent

def judge_cases() -> dict:
    """The Judge's cases and their status (draft until Lea approves them)."""
    return yaml.safe_load(JUDGE_CASES.read_text(encoding="utf8"))


def judge_state(case: dict) -> CaseState:
    """One Judge case as the graph would hand it over: the assessment, every ward that answered, the proposal."""
    answers = [WardSummary(**answer) for answer in case["wards"]]
    return CaseState(task=PatientTask(patient_id=case["patient_id"], decided_at=0),
                     assessment=AssessmentWithConfidence(**case["assessment"]), ward_answers=answers,
                     proposal=PlacementProposal(**case["proposal"], wards=answers))


def measure_judge(function, limit: int | None = None, real: RealAgents | None = None) -> list[Check]:
    """`function(engine, state)` -> JudgeVerdict, or the real Judge Agent. Per case: each of its items as expected;
    then, for a wrong placement (item 2 or 8 expected to fail), was it caught — and for a right one, was it let
    through."""
    checks = []
    for case in judge_cases()["cases"][:limit]:
        engine = _engine(case["template"], case["target_ward"])
        state = judge_state(case)
        if real is None:
            verdict, error = _call(function, engine, state)
        else:
            verdict, error = _call(real.judge, engine, state, case["case"])
        got = {item.number: item.passed for item in getattr(verdict, "items", [])}
        for number in JUDGE_ITEMS:
            expected = case["expected"][number]
            checks.append(Check("judge", f"item {number}", case["case"], expected, got.get(number),
                                got.get(number) == expected, group=f"item {case['item']}", error=error))
        should_pass = case["expected"][2] and case["expected"][8]
        valid = getattr(verdict, "valid", None)
        name = "right placement let through" if should_pass else "wrong placement caught"
        checks.append(Check("judge", name, case["case"], should_pass, valid, valid == should_pass,
                            group=f"item {case['item']}", error=error))
    return checks


def summarize(checks: list[Check]) -> dict[str, dict]:
    """For each check: how many, how many right, the rate, and the errors."""
    out = {}
    for check, rows in _by(checks, lambda c: c.check).items():
        right = sum(c.ok for c in rows)
        out[check] = {"items": len(rows), "right": right, "rate": right / len(rows),
                      "errors": sum(1 for c in rows if c.error)}
    return out


def breakdown(checks: list[Check], by: str) -> dict[str, dict]:
    """The rate by challenge, trap or group (the template), over all checks."""
    keys = {"challenge": lambda c: [c.challenge], "trap": lambda c: c.traps, "group": lambda c: [c.group]}[by]
    groups = defaultdict(list)
    for c in checks:
        for key in keys(c):
            if key not in (None, ""):
                groups[key].append(c)
    return {key: {"items": len(rows), "rate": sum(c.ok for c in rows) / len(rows)}
            for key, rows in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def wrong(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok]


def _by(checks: list[Check], key) -> dict:
    groups = defaultdict(list)
    for c in checks:
        groups[key(c)].append(c)
    return groups
