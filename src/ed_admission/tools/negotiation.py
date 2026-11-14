"""What a ward's Department Agent sees, and the code check on where its answer says a bed comes from.

  DepartmentView       one ward's report and its private notes — nothing about the patient beyond the request,
                       nothing about other wards (the Department Agent's department_view, tools/toolbox.py)
  bed_source_failure   a bed "from the report" must be one the report shows coming free; a bed "from a note" needs
                       the ward to have a note (answer_ward refuses a failure)

The request and the answer themselves are contracts (contracts/negotiation.py). The multi-agent system asks the
wards through its graph (orchestration/graph.py), which also enforces the request limits.

Owner: Lea Gur-Arie
"""
from dataclasses import dataclass

from ed_admission.contracts.negotiation import WardAnswer


@dataclass
class DepartmentView:
    """What one ward's Department Agent sees: its own report and its own private notes — nothing about the patient
    beyond the request, and nothing about other wards."""
    ward_id: str
    report: dict
    notes: list[str]


def beds_in_report(report: dict) -> set[int]:
    """The beds the ward's report shows coming free: free now, in cleaning, or expected to be discharged today."""
    return {entry["bed"] for key in ("free_beds", "cleaning_beds", "expected_discharges_today")
            for entry in report.get(key, [])}


def bed_source_failure(answer: WardAnswer, view: DepartmentView) -> str | None:
    """Where the ward said it knows the bed is free, checked in code (Lea, 2026-11-08). A bed "from the report" must
    be one the report shows coming free; a bed "from a note" needs the ward to have a note — whether the note really
    speaks of that bed needs language, so it stays with the environment and the shift manager. None: no problem."""
    if answer.bed_number is None:
        return None
    if answer.source == "report" and answer.bed_number not in beds_in_report(view.report):
        return "bed_not_in_report"
    if answer.source == "note" and not view.notes:
        return "no_ward_note"
    return None
