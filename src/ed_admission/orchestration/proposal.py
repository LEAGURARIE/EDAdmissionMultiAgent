"""Who is asked, and what is proposed — code, not a model (DECISIONS.md: the Coordinator is code).

  wards_on_rung   the wards the graph asks now, in parallel: every ward on the current level of the target ward's
                  ladder — not only the wards the report shows a bed in: a ward's note can free a bed its report
                  shows occupied (T19)
  bed_types       the bed types to ask for, in order: the bed the patient needs; for an observation-only patient,
                  then the isolation room as a last resort (rank_placements, rule 4) — the ladder is asked again
  write_proposal  after a rung has answered: the ward highest on the ladder that accepted — two on the same level:
                  the lower occupancy — or, on the target ward's rung, a wait for a bed the ward gives within the hour
                  (a non-severe patient only). None: nothing on this rung, ask the next
  no_bed          when the ladder is spent: a proposal of no bed — the code checks then decide if it may escalate
  to_decision     the proposal as the engine's Decision, with every flag of the assessment kept

The reason the shift manager reads is written here, from the wards' answers — never by a model.

Owner: Lea Gur-Arie
"""
from ed_admission import config
from ed_admission.contracts.agent_outputs import AssessmentWithConfidence, PlacementProposal, WardSummary
from ed_admission.contracts.negotiation import Answer
from ed_admission.environment.decision import Action, Decision, WaitFor
from ed_admission.tools.rank_placements import occupancy


def wards_on_rung(target: str, rung: int) -> list[str]:
    """Every ward on this level of the target ward's ladder; [] past its end."""
    levels = config.ladders()[target]
    return list(levels[rung - 1]) if 1 <= rung <= len(levels) else []


def rungs(target: str) -> int:
    return len(config.ladders()[target])


def bed_types(assessment: AssessmentWithConfidence) -> list[str]:
    """The bed types to ask for, in order. The isolation room is a last resort for an observation-only patient."""
    needed = assessment.bed_type
    return [needed, "isolation"] if needed == "observation" else [needed]


def _max_wait() -> int:
    return config.placement_rules()["waiting"]["non_severe_max_minutes"]


def write_proposal(assessment: AssessmentWithConfidence, target: str, rung: int, answers: list[WardSummary],
                   reports: dict[str, dict], every_answer: list[WardSummary],
                   rejected: frozenset[str] = frozenset()) -> PlacementProposal | None:
    """`answers`: the latest answer of each ward asked on this rung. `every_answer`: every ward asked so far, kept on
    the proposal for the shift manager. `rejected`: wards whose bed the code checks already blocked — the next option.
    None: no ward on this rung gives a bed — ask the next rung."""
    answers = [answer for answer in answers if answer.ward_id not in rejected]
    accepted = sorted((a for a in answers if a.answer == Answer.ACCEPT), key=lambda a: occupancy(reports[a.ward_id]))
    common = dict(rung=rung, wards=every_answer, low_confidence=assessment.low_confidence)
    if accepted:
        chosen = accepted[0]
        return PlacementProposal(kind="place", ward_id=chosen.ward_id, bed_number=chosen.bed_number,
                                 reason=f"{chosen.ward_id} accepted bed {chosen.bed_number}: {chosen.reason}",
                                 bed_from_note=chosen.source == "note", **common)
    if rung == 1 and not assessment.never_waits:
        soon = sorted((a for a in answers if a.answer == Answer.CONDITION and a.asks is None
                       and a.available_in_minutes is not None and a.available_in_minutes <= _max_wait()),
                      key=lambda a: a.available_in_minutes)
        if soon:
            chosen = soon[0]
            return PlacementProposal(kind="wait", ward_id=chosen.ward_id, bed_number=chosen.bed_number,
                                     reason=f"{chosen.ward_id} gives bed {chosen.bed_number} in about "
                                            f"{chosen.available_in_minutes} minutes: {chosen.reason}",
                                     bed_from_note=chosen.source == "note", **common)
    return None


def no_bed(assessment: AssessmentWithConfidence, target: str, every_answer: list[WardSummary]) -> PlacementProposal:
    """The ladder is spent and no ward gave a bed meeting the need."""
    return PlacementProposal(kind="none", rung=rungs(target), wards=every_answer,
                             low_confidence=assessment.low_confidence,
                             reason=f"no {assessment.bed_type} bed on the ladder of {target}: every ward asked")


def to_decision(proposal: PlacementProposal, patient_id: str, assessment: AssessmentWithConfidence,
                target: str) -> Decision:
    """The engine's Decision. Every flag of the assessment is on it; intensive care that the physician did not
    request is marked for a physician's approval."""
    requested = assessment.binding_request and assessment.target_ward == "intensive_care"
    common = dict(patient_id=patient_id, target_ward=target, flags=list(assessment.flags), reason=proposal.reason,
                  physician_approval_for_icu=assessment.intensive_care and not requested)
    if proposal.kind == "place" and proposal.ward_id == target:
        return Decision(action=Action.PLACE, ward_id=proposal.ward_id, bed_number=proposal.bed_number, **common)
    if proposal.kind == "place":
        return Decision(action=Action.TEMPORARY_PLACE, ward_id=proposal.ward_id, bed_number=proposal.bed_number,
                        awaiting_transfer_to=target, **common)
    if proposal.kind == "wait":
        return Decision(action=Action.WAIT, **common,
                        wait_for=WaitFor(ward_id=proposal.ward_id, bed_number=proposal.bed_number,
                                         because="ward_note"))
    return Decision(action=Action.ESCALATE, **{**common, "physician_approval_for_icu": False})
