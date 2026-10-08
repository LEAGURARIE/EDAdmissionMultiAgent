"""Measuring one agent alone — so each owner knows where their agent stands before the whole system works.

Every agent is compared with the part of the frozen expected answers that is its job. The agent never sees them:
it gets a toolbox with its own role, or, for the Department Agent, only its ward's view and the request.

  clinical_problem   per record   target ward · flags prompt_injection
  severity           per record   severe · intensive care · flags missing_vital_sign, severity_judgment_note
  supervision        per record   observation · isolation
  department         per request  a bed now or not, as the ward really is (its notes included) · an accepted bed
                                  is really free. Requests: every ward on each pair's ladder, for the bed type the
                                  patient needs. `note_decides`: the requests where the report alone gives the
                                  wrong answer — what the Department Agent is there for.
  safety_critic      per decision the decisions of the three baselines on every pair; a decision with a hard-rule
                                  violation or a missing flag must be blocked, the others approved

Records: the 74 records of both pair files. Each check is one row; summarize() counts them by check, and by challenge,
trap or template. An agent that raises an error gets the row wrong, with the error kept.

Only the evaluation package reads the expected answers. Owner: integrator
"""
from collections import defaultdict
from dataclasses import dataclass, field

import yaml

from ed_admission import config
from ed_admission.baselines import first_free_bed, rules_only, simulated_coordinator
from ed_admission.contracts.negotiation import Answer, WardRequest
from ed_admission.environment import load_template
from ed_admission.environment.hospital import BedStatus, BedType
from ed_admission.environment.ward_truth import true_state
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation import metrics
from ed_admission.evaluation.case_runner import TEMPLATES, load_pairs, run_cases
from ed_admission.tools.negotiation import DepartmentView
from ed_admission.tools.toolbox import Toolbox

AGENTS = ("clinical_problem", "severity", "supervision", "department", "safety_critic")


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
    group: str = ""              # template (department) or decider (safety critic)
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
            "traps": answer["traps"], "template": answer["template"], "flags": set()})
        row["flags"] |= set(answer["expected"]["required_flags"])
    return truth


def _engine(template: str, target_ward: str):
    return load_template(TEMPLATES / f"{template}.yaml", target_ward=target_ward)


def _call(function, *arguments):
    try:
        return function(*arguments), ""
    except Exception as failure:                            # an agent's failure is a wrong row, not a crash
        return None, f"{type(failure).__name__}: {failure}"


# ---------------------------------------------------------------------- the patient-side agents

PATIENT_CHECKS = {
    "clinical_problem": lambda t: {"target_ward": t["target_ward"],
                                   "flag prompt_injection": "prompt_injection" in t["flags"]},
    "severity": lambda t: {"severe": t["needs"]["severe"], "intensive_care": t["needs"]["intensive_care"],
                           "flag missing_vital_sign": "missing_vital_sign" in t["flags"],
                           "flag severity_judgment_note": "severity_judgment_note" in t["flags"]},
    "supervision": lambda t: {"observation": t["needs"]["observation"], "isolation": t["needs"]["isolation"]},
}


def _got(output, check: str):
    if output is None:
        return None
    if check.startswith("flag "):
        return check[5:] in {getattr(f, "value", f) for f in getattr(output, "flags", [])}
    return getattr(output, check)


def measure_patient_agent(agent: str, function) -> list[Check]:
    """`function(toolbox, patient_id)` -> ClinicalProblemOutput / SeverityOutput / SupervisionOutput."""
    checks = []
    for patient_id, truth in sorted(records().items()):
        toolbox = Toolbox(agent, _engine(truth["template"], truth["target_ward"]))
        output, error = _call(function, toolbox, patient_id)
        for check, expected in PATIENT_CHECKS[agent](truth).items():
            got = _got(output, check)
            checks.append(Check(agent, check, patient_id, expected, got, got == expected,
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


def requests() -> list[dict]:
    """One request to every ward on each pair's ladder, for the bed type the patient needs; repeats counted once."""
    seen, out = set(), []
    for answer in _answers():
        bed_type, target = _bed_type(answer["needs"]), answer["target_ward"]
        for ward_id in (w for level in config.ladders()[target] for w in level):
            key = (answer["template"], target, ward_id, bed_type, answer["needs"]["severe"])
            if key not in seen:
                seen.add(key)
                out.append({"template": answer["template"], "target_ward": target, "ward_id": ward_id,
                            "bed_type": bed_type, "severe": answer["needs"]["severe"],
                            "observation": answer["needs"]["observation"], "isolation": answer["needs"]["isolation"]})
    return out


def measure_department(responder) -> list[Check]:
    """`responder(view, request)` -> WardAnswer — the same signature the negotiation uses."""
    checks = []
    for r in requests():
        engine = _engine(r["template"], r["target_ward"])
        real = _free(true_state(engine, patient_target=r["target_ward"]), r["ward_id"], r["bed_type"])
        shown = _free(engine, r["ward_id"], r["bed_type"])
        view = DepartmentView(r["ward_id"], engine.department_status(r["ward_id"]), engine.ward_notes(r["ward_id"]))
        request = WardRequest(bed_type=r["bed_type"], severe=r["severe"], observation=r["observation"],
                              isolation=r["isolation"], target_ward=r["target_ward"],
                              proposed_bed=min(shown) if shown else None)
        answer, error = _call(responder, view, request)
        item = f"{r['template']} {r['ward_id']} {r['bed_type']} for {r['target_ward']}"
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


# ---------------------------------------------------------------------- the Safety Critic Agent

BASELINES = {"first_free_bed": first_free_bed, "rules_only": rules_only, "simulated_coordinator": simulated_coordinator}


def decisions_to_review() -> list[tuple[str, dict, object, bool]]:
    """(decider, answer, decision, unsafe) for every baseline decision on every pair; the same decision on the same
    pair counted once. Unsafe: a hard-rule violation or a missing flag."""
    seen, out = set(), []
    for name, decider in BASELINES.items():
        for result in run_cases(decider) + run_cases(decider, evaluation_set="second"):
            if result.decision is None:
                continue
            pair = {"case_id": result.case_id, "template": result.template}
            answer = yaml.safe_load(builder.answer_path(pair).read_text(encoding="utf8"))
            key = (result.case_id, result.template, result.decision.model_dump_json(exclude={"reason"}))
            if key in seen:
                continue
            seen.add(key)
            scored = metrics.score(answer, result.decision)
            out.append((name, answer, result.decision, bool(scored.violations or scored.missing_flags)))
    return out


def measure_safety_critic(function) -> list[Check]:
    """`function(toolbox, patient_id, decision)` -> SafetyVerdict."""
    checks = []
    for decider, answer, decision, unsafe in decisions_to_review():
        toolbox = Toolbox("safety_critic", _engine(answer["template"], answer["target_ward"]))
        verdict, error = _call(function, toolbox, answer["case_id"], decision)
        blocked = verdict is not None and not verdict.approve
        check = "blocks an unsafe decision" if unsafe else "approves a safe decision"
        checks.append(Check("safety_critic", check, f"{answer['case_id']} {answer['template']} ({decider})",
                            "block" if unsafe else "approve", "block" if blocked else "approve",
                            blocked == unsafe, answer["challenge"], answer["traps"], group=decider, error=error))
    return checks


# ---------------------------------------------------------------------- one entry, and the counts

def measure(agent: str, function) -> list[Check]:
    if agent == "department":
        return measure_department(function)
    if agent == "safety_critic":
        return measure_safety_critic(function)
    if agent in PATIENT_CHECKS:
        return measure_patient_agent(agent, function)
    raise ValueError(f"unknown agent {agent!r}: one of {AGENTS}")


def summarize(checks: list[Check]) -> dict[str, dict]:
    """For each check: how many, how many right, the rate, and the errors."""
    out = {}
    for check, rows in _by(checks, lambda c: c.check).items():
        right = sum(c.ok for c in rows)
        out[check] = {"items": len(rows), "right": right, "rate": right / len(rows),
                      "errors": sum(1 for c in rows if c.error)}
    return out


def breakdown(checks: list[Check], by: str) -> dict[str, dict]:
    """The rate by challenge, trap or group (template / decider), over all checks."""
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
