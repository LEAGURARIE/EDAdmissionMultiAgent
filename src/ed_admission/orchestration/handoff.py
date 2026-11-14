"""validate_handoff — the check in code after every agent, before its answer goes on (DECISIONS.md: validate_handoff).

An agent's answer can carry text the model was led into writing while it worked — the record, a ward's note. So
whatever an agent hands on is checked here, in code, whatever its own closing tool already checked:

  schema              the contract: every field there, no extra field, the right types
  value_not_allowed   a closed field with a value outside its list (a ward, an answer, a confidence, a rubric item)
  too_long            free text over its limit (config/agents.yaml, text_limits)
  quote_not_in_record the Patient Agent: a finding's quote is not in the record
  bed_not_in_ward     a ward's answer: the bed is not one of the ward's beds
  bed_not_in_report   a ward's answer: a bed "from the report" the report does not show coming free
  no_ward_note        a ward's answer: a bed "from a note" in a ward with no note
  and tags            a tool_output tag inside free text is neutralized (a backslash after its "<") — kept, never
                      removed: an injection attempt stays visible. Not a failure.

Returns the HandoffCheck (kept in the case state and the trace) and the answer, cleaned — or None when it failed.
The graph routes on `passed` only: a failure gets one more try of that agent, then the case is escalated.

Owner: Lea Gur-Arie
"""
import re

from pydantic import BaseModel, ValidationError

from ed_admission.contracts.agent_outputs import (AssessmentWithConfidence, HandoffCheck, HandoffFailure, JudgeVerdict,
                                                  WardSummary)
from ed_admission.tools import patient_record
from ed_admission.tools.negotiation import DepartmentView, bed_source_failure

CONTRACT = {"patient": AssessmentWithConfidence, "wards": WardSummary, "judge": JudgeVerdict}
FREE_TEXT = ("claim", "note", "reason", "condition", "correction")       # quotes stay as written: they match the record
_TAG = re.compile(r"<(/?)(tool_output)", re.IGNORECASE)


def _failure(error: dict) -> HandoffFailure:
    """One Pydantic error, as a closed failure."""
    kind, message = error["type"], error.get("msg", "")
    if kind == "string_too_long":
        return HandoffFailure.TOO_LONG
    if kind in ("literal_error", "enum") or "is not a ward" in message or "not checked by" in message:
        return HandoffFailure.VALUE_NOT_ALLOWED
    return HandoffFailure.SCHEMA


def _neutralize(value):
    """A tool_output tag inside free text -> "<\\tool_output": still readable, never a tag. Walks dicts and lists."""
    if isinstance(value, dict):
        return {key: (_TAG.sub(lambda m: ("<\\/" if m.group(1) else "<\\") + m.group(2), text)
                      if key in FREE_TEXT and isinstance(text, str) else _neutralize(text))
                for key, text in value.items()}
    if isinstance(value, list):
        return [_neutralize(item) for item in value]
    return value


def validate_handoff(stage: str, answer, attempt: int = 1, patient_id: str | None = None,
                     view: DepartmentView | None = None, ward_beds: set[int] | None = None
                     ) -> tuple[HandoffCheck, BaseModel | None]:
    """`stage`: patient · wards · judge. `answer`: what the agent handed in (its contract object, or a dict).
    The Patient Agent's answer needs `patient_id` (its quotes are looked up in the record); a ward's answer needs
    the ward's `view` (its report and notes) and `ward_beds` (the numbers of its beds)."""
    raw = answer.model_dump(mode="json", exclude={"grounded"}) if isinstance(answer, BaseModel) else dict(answer)
    if stage == "patient":
        raw["findings"] = [{k: v for k, v in f.items() if k != "grounded"} for f in raw.get("findings", [])]
    try:
        checked = CONTRACT[stage].model_validate(_neutralize(raw))
    except ValidationError as error:
        failures = list(dict.fromkeys(_failure(e) for e in error.errors()))
        return HandoffCheck(stage=stage, passed=False, failures=failures, attempt=attempt), None

    failures, ungrounded = [], []
    if stage == "patient":
        for finding in checked.findings:
            finding.grounded = patient_record.quote_in_record(patient_id, finding.quote)
        ungrounded = [finding.quote for finding in checked.findings if not finding.grounded]
        if ungrounded:
            failures.append(HandoffFailure.QUOTE_NOT_IN_RECORD)
    if stage == "wards" and checked.bed_number is not None:
        if checked.bed_number not in ward_beds:
            failures.append(HandoffFailure.BED_NOT_IN_WARD)
        else:
            source = bed_source_failure(checked, view)
            if source is not None:
                failures.append(HandoffFailure(source))
    check = HandoffCheck(stage=stage, passed=not failures, failures=failures, ungrounded_quotes=ungrounded,
                         attempt=attempt)
    return check, (checked if check.passed else None)
