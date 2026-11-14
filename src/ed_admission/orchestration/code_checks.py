"""The Judge's layer 1 — the rubric items code checks (config/judge_rubric.yaml: 1, 3–7, 10, 11). They run on every
proposal before the Judge Agent and they block: a proposal that fails goes back to the next option, and the Judge
Agent never sees it (DECISIONS.md: the Judge in two layers).

  1   hard rules             tools/decide.py — the same check the single agent's decisions pass
  3   no flag dropped        every flag of the assessment is on the decision; intensive care the physician did not
                             request is marked for a physician's approval
  4   target ward kept       a place is in the target ward; elsewhere a temporary placement awaiting transfer to it
  5   ladder order           no ward closer on the ladder — or on the same level with a lower occupancy — accepted
                             (a ward whose offer these checks blocked does not count)
  6   bed really offered     the ward's latest answer gave this bed (accept, or the condition of a wait); the bed is the
                             ward's and of the type asked for — the need, or the isolation room as a last resort
  7   waiting rule           only a non-severe patient waits, in the target ward, up to the hour
  10  typed reason, level 3+ not blocking: marked for the shift manager
  11  escalation             only when every ward on the ladder was asked for the need — and, for an observation-only
                             patient, for the isolation room too

Owner: Lea Gur-Arie
"""
from ed_admission import config
from ed_admission.contracts.agent_outputs import (AssessmentWithConfidence, CodeChecks, PlacementProposal, RubricItem,
                                                  WardSummary)
from ed_admission.contracts.negotiation import Answer
from ed_admission.environment import load_hospital
from ed_admission.orchestration.proposal import bed_types, rungs, to_decision, wards_on_rung
from ed_admission.tools.decide import broken_rules
from ed_admission.tools.rank_placements import occupancy

FLAGS_MISSING = "flags raised by the Patient Agent are missing"
TARGET_CHANGED = "the target ward is"


def _item(number: int, problems: list[str], passed_reason: str) -> RubricItem:
    reason = "; ".join(problems) if problems else passed_reason
    return RubricItem(number=number, passed=not problems, reason=reason[:config.text_limits()["reason"]])


def code_checks(proposal: PlacementProposal, assessment: AssessmentWithConfidence, target: str, patient_id: str,
                answers: dict[str, WardSummary], asked: list, reports: dict[str, dict],
                rejected: frozenset[str] = frozenset(), taken: frozenset = frozenset()) -> CodeChecks:
    """`answers`: each ward's latest answer, by ward. `asked`: every (ward, bed type) asked — state.Asked.
    `rejected`: wards whose bed these checks already blocked — an offer that did not meet the need is not one the
    proposal skipped (item 5). `taken`: (ward, bed) given to another patient of the case — not an offer either."""
    decision = to_decision(proposal, patient_id, assessment, target)
    placing = proposal.kind in ("place", "wait")
    level = config.ladder_level(target, proposal.ward_id) if placing else None
    rules = broken_rules(decision, assessment)

    # 1 — the hard rules, except the two that are items of their own (3, 4)
    one = [r for r in rules if not r.startswith(FLAGS_MISSING) and not r.startswith(TARGET_CHANGED)]

    # 3 — every flag kept; intensive care without a physician's request marked
    three = [r for r in rules if r.startswith(FLAGS_MISSING)]
    requested = assessment.binding_request and assessment.target_ward == "intensive_care"
    if placing and assessment.intensive_care and not requested and not decision.physician_approval_for_icu:
        three.append("intensive care without a physician's request is not marked for a physician's approval")

    # 4 — the target ward kept
    four = [r for r in rules if r.startswith(TARGET_CHANGED)]
    if placing and level is None:
        four.append(f"{proposal.ward_id} is not on the ladder of {target}")

    # 5 — the ladder order: nothing closer, or as close with a lower occupancy, accepted
    five = []
    accepted = [a for a in answers.values() if a.answer == Answer.ACCEPT and a.ward_id not in rejected
                and (a.ward_id, a.bed_number) not in taken]
    for answer in accepted:
        theirs = config.ladder_level(target, answer.ward_id)
        if not placing:
            five.append(f"{answer.ward_id} accepted bed {answer.bed_number}, and no bed is proposed")
        elif theirs is not None and theirs < level:
            five.append(f"{answer.ward_id} (level {theirs}) accepted, closer than {proposal.ward_id} (level {level})")
        elif (proposal.kind == "place" and theirs == level and answer.ward_id != proposal.ward_id
              and occupancy(reports[answer.ward_id]) < occupancy(reports[proposal.ward_id])):
            five.append(f"{answer.ward_id} accepted on the same level with a lower occupancy")
        elif proposal.kind == "wait" and theirs == 1:
            five.append(f"{answer.ward_id} accepted a bed now in the target ward: no reason to wait")

    # 6 — the bed really offered, the ward's, of the type asked for
    six = []
    if placing:
        given = answers.get(proposal.ward_id)
        wanted = Answer.ACCEPT if proposal.kind == "place" else Answer.CONDITION
        if given is None or given.answer != wanted or given.bed_number != proposal.bed_number:
            six.append(f"{proposal.ward_id} did not give bed {proposal.bed_number}")
        ward = load_hospital().ward(proposal.ward_id)
        bed = next((b for b in ward.beds if b.number == proposal.bed_number), None)
        if bed is None:
            six.append(f"{proposal.ward_id} has no bed {proposal.bed_number}")
        elif bed.bed_type.value not in bed_types(assessment):
            six.append(f"bed {proposal.bed_number} is {bed.bed_type.value}; the patient needs {assessment.bed_type}")

    # 7 — the waiting rule
    seven = []
    if proposal.kind == "wait":
        if assessment.never_waits:
            seven.append("a severe patient never waits")
        if proposal.ward_id != target:
            seven.append("a patient waits only for a bed in the target ward")
        minutes = answers[proposal.ward_id].available_in_minutes if proposal.ward_id in answers else None
        limit = config.placement_rules()["waiting"]["non_severe_max_minutes"]
        if minutes is None or minutes > limit:
            seven.append(f"the wait is not within {limit} minutes")

    # 10 — a typed reason from level 3 on: not blocking, marked
    typed_from = config.placement_rules()["approval"]["typed_reason_from_ladder_level"]
    ten_reason = (f"level {level}: marked for the shift manager's typed reason" if level and level >= typed_from
                  else "below level 3: no typed reason needed")

    # 11 — an escalation only when no one is left to ask
    eleven = []
    if proposal.kind == "none":
        asked_pairs = {(a.ward_id, a.bed_type) for a in asked}
        for bed_type in bed_types(assessment):
            for rung in range(1, rungs(target) + 1):
                for ward in wards_on_rung(target, rung):
                    if (ward, bed_type) not in asked_pairs:
                        eleven.append(f"{ward} was not asked for a {bed_type} bed")

    return CodeChecks(items=[
        _item(1, one, "no hard rule broken"),
        _item(3, three, "every flag kept"),
        _item(4, four, "the target ward kept" if placing else "not a placement"),
        _item(5, five, "the ladder order kept"),
        _item(6, six, "the ward gave this bed, of the right type" if placing else "not a placement"),
        _item(7, seven, "the waiting rule kept" if proposal.kind == "wait" else "no wait"),
        _item(10, [], ten_reason),
        _item(11, eleven, "every ward on the ladder was asked" if proposal.kind == "none" else "not an escalation"),
    ])
