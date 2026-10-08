"""The toolbox each agent gets — permissions are enforced here, in code, never by a prompt.

    toolbox = Toolbox("severity", engine)              toolbox.compute_news2("P-001")   ✓
    toolbox = Toolbox("department", engine, ward_id="cardiology")
    toolbox.read_patient_record("P-001")                -> PermissionDenied: the Department Agent never sees a record

PERMISSIONS lists which role may call which tool. The single agent of the comparison (30.10) gets every tool the
system has — the Coordinator's and the patient-side agents' — and asks the wards through the same negotiation, so
the comparison tests the architecture, not access to information. The Department Agent is bound to one ward in advance: it sees only
that ward's report and notes, and gets the patient's needs from the Coordinator's request — never an identifier.
Every call is recorded in the trace when one is given (observability/tracing.py).

Owner: Lea (integrator)
"""
import time

from ed_admission import config
from ed_admission.contracts.negotiation import WardAnswer, WardRequest
from ed_admission.contracts.patient_record import Vitals
from ed_admission.environment.decision import Decision, Flag
from ed_admission.tools import bed_holds, department_status, escalation, patient_record
from ed_admission.tools.negotiation import DepartmentView, Negotiation
from ed_admission.tools.news2 import compute_news2, level_of_care
from ed_admission.tools.rank_placements import PatientNeeds, Ranking, rank_placements

ROLES = ("coordinator", "clinical_problem", "severity", "supervision", "department", "safety_critic", "single_agent")
PERMISSIONS = {
    "read_patient_record": {"coordinator", "clinical_problem", "severity", "supervision", "safety_critic", "single_agent"},
    "compute_news2": {"coordinator", "severity", "supervision", "safety_critic", "single_agent"},
    "clinical_lists": {"clinical_problem", "supervision", "safety_critic", "single_agent"},
    "all_department_status": {"coordinator", "safety_critic", "single_agent"},
    "department_view": {"department"},
    "rank_placements": {"coordinator", "single_agent"},
    "ask_ward": {"coordinator", "single_agent"},
    "hold_bed": {"coordinator", "single_agent"},
    "release_hold": {"coordinator", "single_agent"},
    "escalate_to_human": {"coordinator", "single_agent"},
}


class PermissionDenied(Exception):
    """A role called a tool it may not use."""


class Toolbox:
    def __init__(self, role: str, engine, ward_id: str | None = None, negotiation: Negotiation | None = None,
                 trace=None):
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        if (role == "department") != (ward_id is not None):
            raise ValueError("a Department Agent's toolbox is bound to one ward, and only it is")
        self.role, self._engine, self._ward_id, self._negotiation, self._trace = role, engine, ward_id, negotiation, trace

    def _check(self, tool: str, arguments: dict) -> float:
        if self.role not in PERMISSIONS[tool]:
            raise PermissionDenied(f"{self.role} may not use {tool}")
        return time.perf_counter()

    def _record(self, tool: str, arguments: dict, result, started: float) -> None:
        if self._trace is not None:
            self._trace.tool_call(self.role, tool, arguments, result, time.perf_counter() - started)

    # ---------------------------------------------------------------- the patient
    def read_patient_record(self, patient_id: str) -> dict:
        started = self._check("read_patient_record", {"patient_id": patient_id})
        result = patient_record.read_patient_record(patient_id)
        self._record("read_patient_record", {"patient_id": patient_id}, "record", started)
        return result

    def compute_news2(self, patient_id: str) -> dict:
        started = self._check("compute_news2", {"patient_id": patient_id})
        vitals = Vitals(**patient_record.read_patient_record(patient_id)["vitals"])
        news2 = compute_news2(vitals)
        result = {"points": news2.points, "missing": news2.missing, "total": news2.total,
                  "known_total": news2.known_total, "level_of_care": level_of_care(news2)}
        self._record("compute_news2", {"patient_id": patient_id}, result, started)
        return result

    def clinical_lists(self) -> dict:
        """The closed lists: observation conditions, isolation conditions, chief complaints."""
        started = self._check("clinical_lists", {})
        result = {"observation_conditions": config.observation_conditions(),
                  "isolation_conditions": config.isolation_conditions(),
                  "chief_complaints": config.chief_complaints()}
        self._record("clinical_lists", {}, "lists", started)
        return result

    # ---------------------------------------------------------------- the wards
    def all_department_status(self) -> dict[str, dict]:
        started = self._check("all_department_status", {})
        result = department_status.all_department_status(self._engine)
        self._record("all_department_status", {}, "reports", started)
        return result

    def department_view(self) -> DepartmentView:
        started = self._check("department_view", {})
        result = department_status.department_view(self._engine, self._ward_id)
        self._record("department_view", {"ward_id": self._ward_id}, "report and notes", started)
        return result

    def rank_placements(self, needs: PatientNeeds, taken: set = frozenset()) -> Ranking:
        started = self._check("rank_placements", {})
        result = rank_placements(department_status.all_department_status(self._engine), needs, taken)
        self._record("rank_placements", {"needs": needs.__dict__}, [o.__dict__ for o in result.options], started)
        return result

    def ask_ward(self, ward_id: str, request: WardRequest) -> WardAnswer:
        started = self._check("ask_ward", {"ward_id": ward_id})
        if self._negotiation is None:
            raise ValueError("ask_ward needs the case's Negotiation")
        result = self._negotiation.ask(ward_id, request)
        self._record("ask_ward", {"ward_id": ward_id, "request": request.model_dump()}, result.model_dump(mode="json"),
                     started)
        return result

    # ---------------------------------------------------------------- acting
    def hold_bed(self, ward_id: str, bed_number: int, patient_id: str) -> None:
        started = self._check("hold_bed", {})
        bed_holds.hold_bed(self._engine, ward_id, bed_number, patient_id)
        self._record("hold_bed", {"ward_id": ward_id, "bed": bed_number, "patient_id": patient_id}, "held", started)

    def release_hold(self, ward_id: str, bed_number: int) -> None:
        started = self._check("release_hold", {})
        bed_holds.release_hold(self._engine, ward_id, bed_number)
        self._record("release_hold", {"ward_id": ward_id, "bed": bed_number}, "released", started)

    def escalate_to_human(self, patient_id: str, reason: str, flags: list[Flag] | None = None) -> Decision:
        started = self._check("escalate_to_human", {})
        result = escalation.escalate_to_human(patient_id, reason, flags)
        self._record("escalate_to_human", {"patient_id": patient_id, "reason": reason}, "escalated", started)
        return result
