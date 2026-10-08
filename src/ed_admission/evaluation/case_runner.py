"""The case runner: puts each record in its ward-state template and asks a decider for one decision.
Paired cases (two patients, one bed): run_paired, one decision for each patient.

For every pair in data/answer_key/pairs.yaml whose record is written:
  1. build a fresh hospital from the pair's template — relative templates use the pair's true target ward
  2. put the patient in the emergency department queue (patient id and decision time only)
  3. ask the decider once (case mode) and time how long it takes
  4. keep the decision — or the error, if the decider failed — and throw the hospital away
Each case starts from nothing: no state and no memory carry over from the previous case.

The true target ward comes from the answer key, so only this evaluation code may read it.
The runner does not judge the decisions — comparing them with the expected answers is the metrics' job.

Owner: integrator
"""
import time
from dataclasses import dataclass

import yaml

from ed_admission import config
from ed_admission.environment import Decision, Policy, load_template

ANSWER_KEY = config.PROJECT / "data" / "answer_key"
RECORDS = config.PROJECT / "data" / "records"
TEMPLATES = config.PROJECT / "data" / "templates"


@dataclass
class CaseResult:
    case_id: str
    template: str
    decision: Decision | None       # None when the decider failed
    seconds: float                  # the decider's response time, in real seconds
    error: str = ""


SETS = {"first": "pairs.yaml", "second": "pairs_set2.yaml"}     # first: 60 pairs · second: 44 pairs (T13-T22, P-061-P-074)


def load_pairs(evaluation_set: str = "first") -> list[dict]:
    """The pairs of one evaluation set: case_id, template, target_ward."""
    return yaml.safe_load((ANSWER_KEY / SETS[evaluation_set]).read_text(encoding="utf8"))["pairs"]


def run_cases(policy: Policy, case_ids: list[str] | None = None, evaluation_set: str = "first") -> list[CaseResult]:
    """Run the decider on every pair of the set whose record exists (or only on `case_ids`), in file order."""
    results = []
    for pair in load_pairs(evaluation_set):
        if case_ids is not None and pair["case_id"] not in case_ids:
            continue
        if not (RECORDS / f"{pair['case_id']}.yaml").exists():
            continue                                      # record not written yet
        results.append(_run_one(pair, policy))
    return results


def _run_one(pair: dict, policy: Policy) -> CaseResult:
    engine = load_template(TEMPLATES / f"{pair['template']}.yaml", target_ward=pair["target_ward"])
    started = time.perf_counter()
    try:
        decision = engine.decide_case(pair["case_id"], policy)
        error = ""
    except Exception as failure:                          # a failing decider is a result, not a crash of the run
        decision, error = None, f"{type(failure).__name__}: {failure}"
    return CaseResult(case_id=pair["case_id"], template=pair["template"], decision=decision,
                      seconds=time.perf_counter() - started, error=error)


# ---------------------------------------------------------------------- paired cases: two patients, one bed

@dataclass
class PairedResult:
    case_id: str                      # PC-1 ... — the paired case
    template: str
    decisions: list[Decision] | None  # in the order of data/answer_key/paired_cases.yaml; None when the decider failed
    seconds: float
    error: str = ""


def load_paired() -> list[dict]:
    """The paired cases: id, template, and the two patients (case_id, target_ward, decided_minutes_before)."""
    return yaml.safe_load((ANSWER_KEY / "paired_cases.yaml").read_text(encoding="utf8"))["cases"]


def run_paired(policy: Policy) -> list[PairedResult]:
    """Run the decider on every paired case: a fresh hospital, both patients in the queue, one decision each."""
    results = []
    for case in load_paired():
        engine = load_template(TEMPLATES / f"{case['template']}.yaml", target_ward=case["patients"][0]["target_ward"])
        patients = [(p["case_id"], p["decided_minutes_before"]) for p in case["patients"]]
        started = time.perf_counter()
        try:
            decisions, error = engine.decide_cases(patients, policy), ""
        except Exception as failure:
            decisions, error = None, f"{type(failure).__name__}: {failure}"
        results.append(PairedResult(case_id=case["id"], template=case["template"], decisions=decisions,
                                    seconds=time.perf_counter() - started, error=error))
    return results
