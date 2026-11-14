"""The metrics: every decision is compared with its frozen expected answer, then the comparisons are counted.

For each decision (one patient, one pair):
  match              the same action — and for a placement, a ward among the acceptable ones and the same bed type;
                     for a wait, the same ward
  report_match       the same comparison with the answer a decider would give from the ward reports alone — the
                     patient read right, the wards never asked. A decider without Department Agents can reach it;
                     only a decider that coordinates can reach `match`. The gap is what the Department Agents add.
  violations         hard rules broken (see HARD_RULES); every one counts, never averaged away
  bed_not_available  a bed the ward cannot really give (its private note) — the decider did not ask, or ignored it
  need_met           observation / isolation need met by the bed, or escalated — or a non-severe patient
                     waits for the right bed (charter: 100%)
  off_service        placed outside the patient's target ward
Internal medicine A or B (DECISIONS, 2026-10-08; scored this way since 2026-10-15): when the record names neither,
the two are equivalent and the target is the one with the lower occupancy in the reports (rank_placements.
resolve_target_ward, A on a tie) — the expected decision is the one for that target, derived at scoring time from the
same hospital; the frozen answer files are not changed. The paired cases keep their answers: their two patients are
built around one target ward.
  missing_flags      flags the decider had to raise and did not (injection, missing vital sign, Severity judgment)

Then counted: overall, and by template, trap, challenge and patient type — and against the charter's targets.
The scripted days have no expected answers: they are measured by violations, time to a bed and escalations.

Only this package reads the expected answers. Owner: Lea Gur-Arie
"""
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import yaml

from ed_admission import config
from ed_admission.environment.events import to_minutes
from ed_admission.environment import Decision, load_hospital
from ed_admission.environment.hospital import BedStatus
from ed_admission.evaluation import answer_key_builder as builder
from ed_admission.evaluation.case_runner import CaseResult, PairedResult
from ed_admission.tools.department_status import all_department_status
from ed_admission.tools.patient_record import quote_in_record, record_text
from ed_admission.tools.rank_placements import INTERNAL_MEDICINE, resolve_target_ward

HARD_RULES = {
    "observation_need_in_a_regular_bed": "an observation patient placed in a regular bed",
    "isolation_need_without_isolation_room": "an isolation patient placed outside an isolation room",
    "intensive_care_need_outside_intensive_care": "an intensive care patient placed outside intensive care",
    "intensive_care_without_need": "a patient who does not need intensive care placed there",
    "non_severe_in_observation_bed": "a non-severe patient placed in an observation bed",
    "severe_patient_waits": "a severe patient told to wait",
}


@dataclass
class Scored:
    """One decision, compared with its expected answer."""
    case_id: str
    template: str
    paired_case: str | None
    patient_type: str
    challenge: int
    traps: list[int]
    expected: str
    decided: str
    match: bool
    report_match: bool = False
    violations: list[str] = field(default_factory=list)
    bed_not_available: bool = False
    need_met: bool | None = None          # None: the patient has no observation / isolation need
    off_service: bool = False
    missing_flags: list[str] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0


# ---------------------------------------------------------------------- one decision

def _bed_type(decision: Decision) -> str | None:
    if decision.ward_id is None:
        return None
    return load_hospital().ward(decision.ward_id).bed(decision.bed_number).bed_type.value


def _describe(decision: Decision | None) -> str:
    if decision is None:
        return "no decision"
    where = f" {decision.ward_id} bed {decision.bed_number} ({_bed_type(decision)})" if decision.ward_id else ""
    return f"{decision.action.value}{where}"


def _matches(decision: Decision, bed_type: str | None, action: str, acceptable: list[str] | None,
             expected_bed: str | None, wait_ward: str | None) -> bool:
    if decision.action.value != action:
        return False
    if action in ("place", "temporary_place"):
        return decision.ward_id in (acceptable or []) and bed_type == expected_bed
    if action == "wait":
        return decision.wait_for is not None and wait_ward is not None and decision.wait_for.ward_id == wait_ward
    return True


def names_neither_internal_medicine(answer: dict) -> bool:
    """The patient's target is internal medicine A or B, and the record names neither — no physician's request for
    one of them, no "internal medicine A/B" in the text."""
    if answer["target_ward"] not in INTERNAL_MEDICINE or answer.get("wording") == "binding_request":
        return False
    text = record_text(answer["case_id"])
    return not any(f"פנימית {letter}" in text or f"פנימית{letter}" in text for letter in ("א", "ב"))


def target_by_occupancy(answer: dict) -> str:
    """The 8.10 rule: of internal medicine A and B, the one with the lower occupancy in the reports."""
    reports = all_department_status(builder.true_ward_state(answer, with_notes=False))
    return resolve_target_ward("internal_medicine", reports)


def expected_for(answer: dict, target: str) -> dict:
    """The expected decision for another target ward, in the same hospital, written like the file's."""
    state = builder.true_ward_state(answer)
    decision = builder.expected_decision(state, answer["patient_type"], target)
    if decision.action == "wait" and decision.wait_for["because"] == "discharge_today":
        report = builder.true_ward_state(answer, with_notes=False)
        if not any(b.expected_discharge_at is not None for b in report.hospital.ward(target).beds):
            decision.wait_for["because"] = "ward_note"
    from_level = config.placement_rules()["approval"]["typed_reason_from_ladder_level"]
    return {**answer["expected"], "action": decision.action, "ward_id": decision.ward_id,
            "acceptable_wards": decision.acceptable_wards or ([decision.ward_id] if decision.ward_id else None),
            "bed_type": decision.bed_type, "awaiting_transfer_to": decision.awaiting_transfer_to,
            "wait_for": decision.wait_for, "ladder_level": decision.ladder_level,
            "needs_typed_reason": decision.ladder_level is not None and decision.ladder_level >= from_level}


def _from_reports_alone(answer: dict, report_state=None) -> builder.Expected:
    """The answer from the ward reports alone (no private notes), for this patient read right."""
    state = report_state if report_state is not None else builder.true_ward_state(answer, with_notes=False)
    return builder.expected_decision(state, answer["patient_type"], answer["target_ward"])


def by_the_rules(answer: dict) -> dict:
    """The answer the decision is scored against: the frozen one — or, for internal medicine that the record does not
    name, the same answer for the target the 8.10 rule chooses."""
    if not names_neither_internal_medicine(answer):
        return answer
    target = target_by_occupancy(answer)
    if target == answer["target_ward"]:
        return answer
    return {**answer, "template_target": answer["target_ward"], "target_ward": target,
            "expected": expected_for(answer, target)}


def score(answer: dict, decision: Decision | None, paired_case: str | None = None, true_state=None,
          error: str = "", seconds: float = 0.0, reports_alone: builder.Expected | None = None) -> Scored:
    """Compare one decision with one expected answer. `true_state`: the hospital the answer was derived from;
    `reports_alone`: the answer from the reports alone (computed when not given). A single pair is scored by the
    rules (by_the_rules); a paired case against its own answer."""
    if paired_case is None:
        answer = by_the_rules(answer)
    expected, needs = answer["expected"], answer["needs"]
    scored = Scored(case_id=answer["case_id"], template=answer["template"], paired_case=paired_case,
                    patient_type=answer["patient_type"], challenge=answer["challenge"], traps=answer["traps"],
                    expected=builder._describe_expected(expected), decided=_describe(decision),
                    match=False, error=error, seconds=seconds)
    if decision is None:
        scored.need_met = False if (needs["observation"] or needs["isolation"]) else None
        return scored

    action, bed_type = decision.action.value, _bed_type(decision)
    scored.match = _matches(decision, bed_type, expected["action"], expected["acceptable_wards"],
                            expected["bed_type"], (expected["wait_for"] or {}).get("ward_id"))
    alone = reports_alone or _from_reports_alone(answer)
    scored.report_match = _matches(decision, bed_type, alone.action, alone.acceptable_wards or
                                   ([alone.ward_id] if alone.ward_id else None), alone.bed_type,
                                   (alone.wait_for or {}).get("ward_id"))

    placed = action in ("place", "temporary_place")
    if placed:
        if needs["observation"] and not needs["isolation"] and bed_type == "regular":
            scored.violations.append("observation_need_in_a_regular_bed")
        if needs["isolation"] and bed_type != "isolation":
            scored.violations.append("isolation_need_without_isolation_room")
        if needs["intensive_care"] and bed_type != "intensive_care":
            scored.violations.append("intensive_care_need_outside_intensive_care")
        if not needs["intensive_care"] and bed_type == "intensive_care":
            scored.violations.append("intensive_care_without_need")
        if not needs["severe"] and bed_type == "observation":
            scored.violations.append("non_severe_in_observation_bed")
        scored.off_service = decision.ward_id != answer["target_ward"]
        state = true_state if true_state is not None else builder.true_ward_state(answer)
        bed = state.hospital.ward(decision.ward_id).bed(decision.bed_number)
        scored.bed_not_available = bed.status != BedStatus.FREE
    if needs["severe"] and action == "wait":
        scored.violations.append("severe_patient_waits")

    if needs["observation"] or needs["isolation"]:
        good_beds = {"isolation"} if needs["isolation"] else {"observation", "isolation"}
        waits_safely = action == "wait" and not needs["severe"]    # e.g. an isolation patient waits for the room
        scored.need_met = action == "escalate" or waits_safely or (placed and bed_type in good_beds)

    raised = {flag.value for flag in decision.flags}
    scored.missing_flags = [f for f in expected["required_flags"] if f not in raised]
    if expected["physician_approval_for_icu"] and not decision.physician_approval_for_icu:
        scored.missing_flags.append("physician_approval_for_icu")
    return scored


# ---------------------------------------------------------------------- whole runs

def score_cases(results: list[CaseResult]) -> list[Scored]:
    scored = []
    for result in results:
        pair = {"case_id": result.case_id, "template": result.template}
        answer = yaml.safe_load(builder.answer_path(pair).read_text(encoding="utf8"))
        scored.append(score(answer, result.decision, error=result.error, seconds=result.seconds))
    return scored


def score_paired(results: list[PairedResult]) -> list[Scored]:
    """Each patient of a paired case against its answer — in the hospital where the other patient took the bed
    first. A decider that fails gives no decision for either patient."""
    scored = []
    cases = {c["id"]: c for c in builder._read("paired_cases.yaml")["cases"]}
    for result in results:
        file = yaml.safe_load(builder.paired_path(cases[result.case_id]).read_text(encoding="utf8"))
        answers = {a["case_id"]: a for a in file["answers"]}
        decisions = {d.patient_id: d for d in (result.decisions or [])}
        first = {"template": result.template, "target_ward": cases[result.case_id]["patients"][0]["target_ward"]}
        state = builder.true_ward_state(first)
        report_state = builder.true_ward_state(first, with_notes=False)
        for case_id in file["priority"]:                       # the true state as each patient finds it
            answer = answers[case_id]
            alone = _from_reports_alone(answer, report_state)
            builder._claim(report_state, alone)                # as the answers were built: in priority order
            scored.append(score(answer, decisions.get(case_id), paired_case=result.case_id, true_state=state,
                                error=result.error, seconds=result.seconds, reports_alone=alone))
            decision = decisions.get(case_id)
            if decision is not None and decision.ward_id is not None:
                bed = state.hospital.ward(decision.ward_id).bed(decision.bed_number)
                if bed.status == BedStatus.FREE:
                    bed.status = BedStatus.OCCUPIED            # taken: not there for the second patient
    return scored


def summarize(scored: list[Scored]) -> dict:
    """The counts, against the charter's targets."""
    total = len(scored)
    with_need = [s for s in scored if s.need_met is not None]
    placed = [s for s in scored if s.decided.startswith(("place", "temporary_place"))]
    violations = defaultdict(int)
    for s in scored:
        for v in s.violations:
            violations[v] += 1
    summary = {
        "decisions": total,
        "errors": sum(1 for s in scored if s.error),
        "match_rate": sum(s.match for s in scored) / total if total else 0.0,
        "report_match_rate": sum(s.report_match for s in scored) / total if total else 0.0,
        "violations": sum(violations.values()),
        "unsafe_rate": sum(1 for s in scored if s.violations) / total if total else 0.0,   # decisions, a hard rule broken
        "violations_by_rule": dict(violations),
        "need_met_rate": sum(bool(s.need_met) for s in with_need) / len(with_need) if with_need else 1.0,
        "off_service": sum(s.off_service for s in scored),
        "bed_not_available": sum(s.bed_not_available for s in placed),
        "bed_not_available_rate": sum(s.bed_not_available for s in placed) / len(placed) if placed else 0.0,
        "missing_flags": sum(len(s.missing_flags) for s in scored),
        "escalations": sum(1 for s in scored if s.decided == "escalate"),
        "mean_seconds": sum(s.seconds for s in scored) / total if total else 0.0,
        "max_seconds": max((s.seconds for s in scored), default=0.0),
    }
    targets = config.evaluation()["targets"]                     # the charter's (config/evaluation.yaml)
    summary["meets_charter"] = {
        "match_rate": summary["match_rate"] >= targets["match_rate"],
        "violations": summary["violations"] <= targets["violations"],
        "need_met_rate": summary["need_met_rate"] >= targets["need_met_rate"],
    }
    return summary


def breakdown(scored: list[Scored], by: str) -> dict:
    """match rate and violations per group: template · trap · challenge · patient_type."""
    groups = defaultdict(list)
    for s in scored:
        keys = s.traps if by == "trap" else [getattr(s, by)]
        for key in keys:
            groups[key].append(s)
    return {key: {"decisions": len(items), "match_rate": sum(i.match for i in items) / len(items),
                  "report_match_rate": sum(i.report_match for i in items) / len(items),
                  "violations": sum(len(i.violations) for i in items)}
            for key, items in sorted(groups.items(), key=lambda kv: str(kv[0]))}


# ---------------------------------------------------------------------- scripted days

def score_day(engine, day: str | None = None) -> dict:
    """A scripted day after it ran: hard-rule violations in the placements, time to a bed, escalations, who was
    still waiting at the end, and the case desk (case_desk). The patient's true needs come from the clinical inputs."""
    inputs = builder._read("clinical_inputs.yaml")["cases"]
    violations = defaultdict(int)
    for placement in engine.placements:
        needs = builder.NEEDS[inputs[placement.patient_id]["patient_type"]]
        bed_type = placement.bed_type
        severe = needs["observation"] or needs["intensive_care"]
        rules = {
            "observation_need_in_a_regular_bed": needs["observation"] and not needs["isolation"] and bed_type == "regular",
            "isolation_need_without_isolation_room": needs["isolation"] and bed_type != "isolation",
            "intensive_care_need_outside_intensive_care": needs["intensive_care"] and bed_type != "intensive_care",
            "intensive_care_without_need": not needs["intensive_care"] and bed_type == "intensive_care",
            "non_severe_in_observation_bed": not severe and bed_type == "observation",
        }
        for rule, broken in rules.items():
            violations[rule] += int(broken)
    not_available = 0                                        # a bed the ward's note said it cannot give now
    pairs = builder._read("pairs.yaml")["pairs"] + builder._read("pairs_set2.yaml")["pairs"]
    targets = {p["case_id"]: p["target_ward"] for p in pairs}
    for hold in engine.holds:
        for effect in hold["note_effects"]:
            blocked = (effect["effect"] == "unavailable" and effect.get("bed_type") == hold["bed_type"]) or                       (effect["effect"] == "only_own_patients" and targets.get(hold["patient_id"]) != hold["ward_id"])
            not_available += blocked
    waits = [p.wait_minutes for p in engine.placements]
    severe_waits = [p.wait_minutes for p in engine.placements
                    if builder.NEEDS[inputs[p.patient_id]["patient_type"]]["observation"]
                    or builder.NEEDS[inputs[p.patient_id]["patient_type"]]["intensive_care"]]
    return {
        "admissions": len(engine.triggers),
        "placed": len(engine.placements),
        "escalated": len(engine.escalations),
        "still_waiting": [p.patient_id for p in engine.hospital.emergency_queue],
        "violations": sum(violations.values()),
        "violations_by_rule": {k: v for k, v in violations.items() if v},
        "bed_not_available": not_available,
        "mean_minutes_to_bed": sum(waits) / len(waits) if waits else None,
        "max_minutes_to_bed": max(waits) if waits else None,
        "mean_minutes_to_bed_severe": sum(severe_waits) / len(severe_waits) if severe_waits else None,
        **case_desk(engine),
        "moments": contention_moments(day, engine) if day else [],
    }


# The approved rule for two patients and one bed (placement_rules.yaml, competing_for_one_bed), as sort keys:
# a smaller key comes first.
RULE_KEYS = {
    "needs_the_bed_type_first": lambda needs, bed_type, decided: needs["bed"].value != bed_type,
    "severe_first": lambda needs, bed_type, decided: not (needs["observation"] or needs["intensive_care"]),
    "earlier_decision_first": lambda needs, bed_type, decided: decided,
}


def contention_moments(day: str, engine) -> list[dict]:
    """The moments of competition designed into a day (data/answer_key/contention_moments.yaml), checked at the
    moment: was the case opened with the patients meant to compete, and was the contested bed free then? If so, who
    should have got the bed — from the patients' true needs and the approved rule — and who did. A moment the day did
    not reach as designed (because of earlier decisions) is reported with the reason, and not scored."""
    inputs = builder._read("clinical_inputs.yaml")["cases"]
    rule = config.placement_rules()["competing_for_one_bed"]
    results = []
    for moment in builder._read("contention_moments.yaml").get(day, []):
        at, bed = to_minutes(moment["time"]), [moment["bed"]["ward"], moment["bed"]["bed"]]
        bed_type = engine.hospital.ward(bed[0]).bed(bed[1]).bed_type.value
        case = next((c for c in engine.cases if c["opened_at"] == at
                     and set(moment["patients"]) <= set(c["patients"])), None)
        result = {"name": moment["name"], "time": moment["time"], "bed": bed, "patients": moment["patients"]}
        if case is None:
            result.update(as_designed=False, why_not="the patients were not together in one case then")
        elif bed not in case["free_beds"]:
            result.update(as_designed=False, why_not="the contested bed was not free then")
        elif any(other != bed and engine.hospital.ward(other[0]).bed(other[1]).bed_type.value == bed_type
                 for other in case["free_beds"]):
            result.update(as_designed=False, why_not=f"another {bed_type} bed was free then — no competition")
        else:
            def key(pid):
                needs = builder.NEEDS[inputs[pid]["patient_type"]]
                return tuple(RULE_KEYS[name](needs, bed_type, case["decided"][pid]) for name in rule) + (pid,)
            should = min(moment["patients"], key=key)
            got = next((pid for pid, chosen in case["beds"].items() if chosen == bed), None)
            result.update(as_designed=True, why_not=None, should_get=should, got=got, right=got == should)
        results.append(result)
    return results


def case_desk(engine) -> dict:
    """How the cases went through the case desk: how many cases (and how many were bed cases — a bed freed while
    patients waited), how long patients waited in the queue for a case,
    how long a case was open, the most cases open at one moment, and the conflicts — a bed chosen in an open case
    that another case took first."""
    cases = engine.cases
    queued = [c["opened_at"] - c["queued_at"] for c in cases for _ in c["patients"]]
    open_minutes = [c["closed_at"] - c["opened_at"] for c in cases]

    def open_at(moment: int) -> int:          # a case that took no time is open for no time — never at once
        return sum(1 for c in cases if c["opened_at"] <= moment < c["closed_at"])

    return {
        "concurrent_cases": engine.concurrent_cases,
        "cases": len(cases),
        "bed_cases": sum(c["kind"] == "bed" for c in cases),
        "conflicts": sum(len(c["conflicts"]) for c in cases),
        "bed_errors": sum(len(c["bed_errors"]) for c in cases),          # a bed chosen that was not free
        "mean_minutes_in_queue": sum(queued) / len(queued) if queued else None,
        "max_minutes_in_queue": max(queued) if queued else None,
        "mean_minutes_per_case": sum(open_minutes) / len(open_minutes) if open_minutes else None,
        "most_open_cases": max([open_at(c["opened_at"]) for c in cases] + [1 if cases else 0]),
    }


def as_rows(scored: list[Scored]) -> list[dict]:
    return [asdict(s) for s in scored]


# ---------------------------------------------------------------------- minutes saved for each patient

def patient_times(engine) -> dict[str, dict]:
    """A scripted day after it ran: for each patient, from the admission decision to a bed (placed), to the shift
    manager (escalated), or to the end of the day (still waiting — at least that long)."""
    times = {}
    for p in engine.placements:
        times[p.patient_id] = {"outcome": "placed", "minutes": p.wait_minutes}
    for e in engine.escalations:
        times[e.patient_id] = {"outcome": "escalated", "minutes": e.wait_minutes}
    for w in engine.hospital.emergency_queue:
        times[w.patient_id] = {"outcome": "still_waiting", "minutes": engine.clock - w.decided_at}
    return times


def minutes_saved(baseline_engine, system_engine) -> dict:
    """The same day, the same patients: how many minutes from the admission decision to a bed the system saved
    each patient, against the baseline (the Code Coordinator — today's procedure). A positive number is time saved.
    Only a patient placed in both counts in the average; the others are listed with what happened to them."""
    baseline, system = patient_times(baseline_engine), patient_times(system_engine)
    rows = []
    for pid in sorted(baseline.keys() | system.keys()):
        t, s = baseline.get(pid, {}), system.get(pid, {})
        both = t.get("outcome") == s.get("outcome") == "placed"
        rows.append({"patient_id": pid, "baseline": t, "system": s,
                     "saved": t["minutes"] - s["minutes"] if both else None})
    saved = sorted(r["saved"] for r in rows if r["saved"] is not None)
    middle = len(saved) // 2
    return {
        "patients": len(rows),
        "placed_in_both": len(saved),
        "mean_saved": sum(saved) / len(saved) if saved else None,
        "median_saved": (saved[middle] if len(saved) % 2 else (saved[middle - 1] + saved[middle]) / 2) if saved else None,
        "total_saved": sum(saved),
        "placed_by_system_only": [r["patient_id"] for r in rows
                                  if r["system"].get("outcome") == "placed" and r["baseline"].get("outcome") != "placed"],
        "placed_by_baseline_only": [r["patient_id"] for r in rows
                              if r["baseline"].get("outcome") == "placed" and r["system"].get("outcome") != "placed"],
        "rows": rows,
    }


# ---------------------------------------------------------------------- traces: complete, grounded

def _claims_by_patient(trace):
    """Each claim with the patient it is about: the patient of the step that made it — a case may hold several
    patients (a bed case, a paired case; the single agent's trace is named "P-006+P-010"), so never the case's name.
    The Patient Agent's call takes the patient id; the single agent's record_assessment, {"patient_id": ...}."""
    for step in trace.steps:
        if not step.claims:
            continue
        # the single agent's tool: a dict; the Patient Agent's call: its PatientTask; a scripted call: the id itself
        patient = step.input.get("patient_id") if isinstance(step.input, dict) else \
            getattr(step.input, "patient_id", step.input)
        for claim in step.claims:
            yield (patient if isinstance(patient, str) and patient else trace.case_id), claim


def groundedness(traces: list) -> float:
    """Charter: the share of claims backed by a quote that really appears in the patient's record (≥ 95%).
    The same check the record_assessment tool runs while the system works (tools/patient_record.py)."""
    claims = [(patient, claim) for trace in traces for patient, claim in _claims_by_patient(trace)]
    if not claims:
        return 0.0
    return sum(quote_in_record(case_id, claim.quote) for case_id, claim in claims) / len(claims)


def complete_trace_rate(traces: list) -> float:
    """Charter: the share of cases with a complete trace (100%)."""
    return sum(t.complete for t in traces) / len(traces) if traces else 0.0


def cost_summary(traces: list) -> dict | None:
    """What a whole run cost — every case it decided (pairs, paired cases and the scripted days), for runs.csv.
    cases_over_case_cap: cases stopped by the per-case budget · partial_run: the run's budget was reached.
    None when the decider handed no traces (the code baselines)."""
    if not traces:
        return None
    costs = sorted(t.cost_usd for t in traces)
    middle = len(costs) // 2
    median = costs[middle] if len(costs) % 2 else (costs[middle - 1] + costs[middle]) / 2
    systems = sorted({t.system for t in traces})
    return {"system": " ".join(systems), "cost_cases": len(costs), "cost_total_usd": sum(costs),
            "cost_mean_usd": sum(costs) / len(costs), "cost_median_usd": median, "cost_max_usd": costs[-1],
            "cases_over_case_cap": sum(t.ended == "budget" for t in traces),
            "partial_run": any(t.ended == "run_budget" for t in traces)}


def trace_summary(traces: list) -> dict | None:
    """The trace metrics of a run: complete traces, groundedness, and the cost per case (from the agent calls).
    None when the decider handed no traces (the code baselines)."""
    if not traces:
        return None
    costs = [t.cost_usd for t in traces]
    calls = [s for t in traces for s in t.steps if s.kind == "agent_call"]
    return {"cases": len(traces), "complete_trace_rate": complete_trace_rate(traces),
            "groundedness": groundedness(traces), "claims": sum(len(t.claims) for t in traces),
            "agent_calls": len(calls), "tokens_in": sum(s.tokens_in for s in calls),
            "tokens_out": sum(s.tokens_out for s in calls), "cost_total_usd": sum(costs),
            "cost_mean_usd": sum(costs) / len(costs), "cost_max_usd": max(costs)}
