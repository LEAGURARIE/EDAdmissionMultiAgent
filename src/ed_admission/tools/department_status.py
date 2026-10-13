"""get_department_status — a ward's report: beds by type and state, free beds by number, cleaning with the time
ready, discharges expected today (likely within the hour or not), the day, the shift and the weekend. Never patient
identifiers, never the private notes.

Two bindings, both enforced in tool code (tools/toolbox.py):
  Coordinator Agent    — every ward's report (all_department_status), never the private notes.
  Department Agent     — bound in advance to its own ward; takes no ward name. Gets its report and its notes
                         (department_view).

Owner: Lea Gur-Arie
"""
from ed_admission.tools.negotiation import DepartmentView


def all_department_status(engine) -> dict[str, dict]:
    return {ward_id: engine.department_status(ward_id) for ward_id in engine.hospital.wards}


def department_view(engine, ward_id: str) -> DepartmentView:
    return DepartmentView(ward_id, engine.department_status(ward_id), engine.ward_notes(ward_id))
