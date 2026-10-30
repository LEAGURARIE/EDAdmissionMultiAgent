"""The toolbox each agent gets — permissions are enforced here, in code, never by a prompt.

    toolbox = Toolbox("patient", engine)                toolbox.compute_news2("P-001")   ✓
    toolbox = Toolbox("department", engine, ward_id="cardiology")
    toolbox.read_patient_record("P-001")                -> PermissionDenied: the Department Agent never sees a record

The roles as built (2026-10-10; the multi-agent system is being redesigned) — each sees what the others do not:

  patient       the record only: read_patient_record, compute_news2, clinical_lists
  department    its own ward only: department_view (its report and private notes); bound to one ward in advance, and
                it gets the patient's needs from the Coordinator's request — never an identifier
  coordinator   every ward's report, and the two agents' answers — never the record itself:
                assess_patient (runs the Patient Agent), all_department_status, rank_placements, ask_ward,
                escalate_to_human, and decide (the hard rules, checked in code — tools/decide.py)

No agent holds or releases a bed: the decision is the hold — the environment holds the bed when the case closes
(simulation_engine.py, hold_bed_when), for the shift manager's approval.

  single_agent  one agent doing every agent's job (the fair comparison, 2026-10-15): the record and its own
                assessment (record_assessment, in the Patient Agent's format, instead of assess_patient), every
                ward's report, and every ward's report and notes (department_view with a ward id) — it reads the
                notes itself, as the Department Agents do, so it has no wards to ask (no ask_ward); the ranking
                and decide.

PERMISSIONS lists which role may call which tool. The single agent of the comparison does every agent's job with
their tools, so the comparison tests the architecture, not access to information.
Every call is recorded in the trace when one is given (observability/tracing.py). Given the placement case
(orchestration/placement_case.py), the Coordinator's tools also move it through its steps — assess_patient, the
reports, the ranking, a ward asked, the rules checked, decided — so a step out of order is refused in code.

Owner: Lea Gur-Arie
"""
import time

from ed_admission import config
from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.contracts.negotiation import WardAnswer, WardRequest
from ed_admission.contracts.patient_record import Vitals
from ed_admission.environment.decision import Decision, Flag
from ed_admission.observability.tracing import Claim
from ed_admission.tools import department_status, escalation, patient_record
from ed_admission.tools import decide as decide_tool
from ed_admission.tools.negotiation import DepartmentView, Negotiation
from ed_admission.tools.news2 import compute_news2, level_of_care
from ed_admission.tools.rank_placements import PatientNeeds, Ranking, rank_placements, resolve_target_ward

ROLES = ("coordinator", "patient", "department", "single_agent")
PERMISSIONS = {
    "read_patient_record": {"patient", "single_agent"},
    "compute_news2": {"patient", "single_agent"},
    "clinical_lists": {"patient", "single_agent"},
    "assess_patient": {"coordinator"},
    "record_assessment": {"single_agent"},
    "decide": {"coordinator", "single_agent"},
    "all_department_status": {"coordinator", "single_agent"},
    "department_view": {"department", "single_agent"},
    "rank_placements": {"coordinator", "single_agent"},
    "ask_ward": {"coordinator"},
    "escalate_to_human": {"coordinator", "single_agent"},
}


class PermissionDenied(Exception):
    """A role called a tool it may not use."""


class Toolbox:
    def __init__(self, role: str, engine, ward_id: str | None = None, negotiation: Negotiation | None = None,
                 trace=None, patient_agent=None, case=None):
        """`patient_agent`: for the Coordinator — the Patient Agent's `run(toolbox, patient_id)`, which
        assess_patient calls. `case`: the placement case; its negotiation and trace are used, and the tools move it
        through its steps."""
        if case is not None:
            negotiation, trace = negotiation or case.negotiation, trace or case.trace
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        if (role == "department") != (ward_id is not None):
            raise ValueError("a Department Agent's toolbox is bound to one ward, and only it is")
        self.role, self._engine, self._ward_id, self._negotiation, self._trace = role, engine, ward_id, negotiation, trace
        self._patient_agent = patient_agent
        self._case = case
        self._assessments: dict[str, PatientAssessment] = {}       # what the Patient Agent found, by patient

    def _check(self, tool: str, arguments: dict) -> float:
        if self.role not in PERMISSIONS[tool]:
            raise PermissionDenied(f"{self.role} may not use {tool}")
        return time.perf_counter()

    def _reach(self, step: str, output=None) -> None:
        if self._case is not None:
            self._case.reach(step, output)

    def _record(self, tool: str, arguments: dict, result, started: float, claims=()) -> None:
        if self._trace is not None:
            self._trace.tool_call(self.role, tool, arguments, result, time.perf_counter() - started, claims=claims)

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
        """The closed lists: observation conditions, isolation conditions, chief complaints, and the approved
        severity judgments (when the record raises the level, keeps it with a note, or which data counts)."""
        started = self._check("clinical_lists", {})
        result = {"observation_conditions": config.observation_conditions(),
                  "isolation_conditions": config.isolation_conditions(),
                  "chief_complaints": config.chief_complaints(),
                  "severity_judgment": config.severity_judgment()}
        self._record("clinical_lists", {}, "lists", started)
        return result

    def assess_patient(self, patient_id: str) -> PatientAssessment:
        """Run the Patient Agent on this patient — with its own toolbox, so only it reads the record — and return
        what it found. Every finding's quote is checked against the record (`grounded`), and the assessment is kept:
        `decide` checks the decision against it, so the Coordinator cannot change what the Patient Agent found."""
        started = self._check("assess_patient", {"patient_id": patient_id})
        if self._patient_agent is None:
            raise ValueError("assess_patient needs the Patient Agent")
        assessment = self._patient_agent(Toolbox("patient", self._engine, trace=self._trace), patient_id)
        for finding in assessment.findings:
            finding.grounded = patient_record.quote_in_record(patient_id, finding.quote)
        self._assessments[patient_id] = assessment
        if self._trace is not None:
            self._trace.agent_call("patient", patient_id, assessment.model_dump(mode="json"),
                                   model=getattr(assessment, "model", "-"),
                                   prompt_version=getattr(assessment, "prompt_version", "-"),
                                   tokens_in=getattr(assessment, "tokens_in", 0),
                                   tokens_out=getattr(assessment, "tokens_out", 0),
                                   cost_usd=getattr(assessment, "cost_usd", 0.0),
                                   seconds=time.perf_counter() - started,
                                   claims=[Claim(f.claim, f.quote) for f in assessment.findings])
        self._record("assess_patient", {"patient_id": patient_id}, "assessment", started)
        self._reach("patient_assessed", {"target_ward": assessment.target_ward, "bed_type": assessment.bed_type,
                                         "never_waits": assessment.never_waits})
        return assessment

    def record_assessment(self, patient_id: str, assessment: PatientAssessment) -> PatientAssessment:
        """The single agent hands in its own assessment, in the Patient Agent's format. Every finding's quote is
        checked against the record (`grounded`) — a quote not found is kept and marked, never dropped, so the
        groundedness measured is the real one. The last assessment of a patient counts."""
        started = self._check("record_assessment", {"patient_id": patient_id})
        for finding in assessment.findings:
            finding.grounded = patient_record.quote_in_record(patient_id, finding.quote)
        self._assessments[patient_id] = assessment
        self._record("record_assessment", {"patient_id": patient_id}, assessment.model_dump(mode="json"), started,
                     claims=[Claim(f.claim, f.quote) for f in assessment.findings])
        self._reach("patient_assessed", {"target_ward": assessment.target_ward, "bed_type": assessment.bed_type,
                                         "never_waits": assessment.never_waits})
        return assessment

    def assessed(self) -> dict[str, PatientAssessment]:
        """Every assessment kept so far, by patient — for the single agent's case state."""
        return dict(self._assessments)

    def assessment_of(self, patient_id: str) -> PatientAssessment:
        """The assessment kept for this patient — nothing about the patient is searched or decided before it."""
        if patient_id not in self._assessments:
            raise ValueError(f"assess {patient_id} first (record_assessment): there are no needs to search by")
        return self._assessments[patient_id]

    def needs_of(self, patient_id: str) -> PatientNeeds:
        """The needs the bed search uses, from the kept assessment: the target is intensive care whenever the patient
        needs it (PatientAssessment.target); "internal_medicine" becomes A or B by occupancy, as the ranking does."""
        assessment = self.assessment_of(patient_id)
        target = resolve_target_ward(assessment.target, department_status.all_department_status(self._engine))
        return PatientNeeds(target, assessment.bed_type, severe=assessment.never_waits)

    # ---------------------------------------------------------------- the wards
    def all_department_status(self) -> dict[str, dict]:
        started = self._check("all_department_status", {})
        result = department_status.all_department_status(self._engine)
        self._record("all_department_status", {}, "reports", started)
        self._reach("availability_collected", "reports")
        return result

    def department_view(self, ward_id: str | None = None) -> DepartmentView:
        """One ward's report and its private notes. The Department Agent: its own ward only, named in advance. The
        single agent: any ward, by its id."""
        started = self._check("department_view", {})
        if self.role == "department":
            if ward_id not in (None, self._ward_id):
                raise PermissionDenied(f"the Department Agent of {self._ward_id} sees no other ward")
            ward_id = self._ward_id
        elif ward_id not in self._engine.hospital.wards:
            raise ValueError(f"{ward_id!r} is not a ward: one of {sorted(self._engine.hospital.wards)}")
        result = department_status.department_view(self._engine, ward_id)
        self._record("department_view", {"ward_id": ward_id}, "report and notes", started)
        return result

    def rank_placements(self, needs: PatientNeeds, taken: set = frozenset()) -> Ranking:
        started = self._check("rank_placements", {})
        result = rank_placements(department_status.all_department_status(self._engine), needs, taken)
        self._record("rank_placements", {"needs": needs.__dict__}, [o.__dict__ for o in result.options], started)
        self._reach("availability_collected", "reports")           # the ranking reads every ward's report
        self._reach("ranked", [o.__dict__ for o in result.options])
        return result

    def ask_ward(self, ward_id: str, request: WardRequest) -> WardAnswer:
        started = self._check("ask_ward", {"ward_id": ward_id})
        if self._negotiation is None:
            raise ValueError("ask_ward needs the case's Negotiation")
        self._reach("negotiating", {"ward": ward_id})
        result = self._negotiation.ask(ward_id, request)
        self._record("ask_ward", {"ward_id": ward_id, "request": request.model_dump()}, result.model_dump(mode="json"),
                     started)
        return result

    # ---------------------------------------------------------------- acting
    def decide(self, decision: Decision) -> Decision:
        """Hand in the decision. The hard rules are checked in code against what the Patient Agent found
        (tools/decide.py): a decision that breaks one is refused with the reasons (RuleViolation)."""
        started = self._check("decide", {})
        assessment = self._assessments.get(decision.patient_id)
        if assessment is None:
            raise ValueError(f"assess {decision.patient_id} first: there is nothing to check the decision against")
        try:
            result = decide_tool.decide(decision, assessment)
        except decide_tool.RuleViolation as violation:
            self._record("decide", decision.model_dump(mode="json"), {"refused": violation.reasons}, started)
            if self._case is not None:
                self._case.refuse(violation.reasons)
            raise
        self._record("decide", decision.model_dump(mode="json"), "accepted", started)
        if self._case is not None:
            if result.action.value != "escalate":
                self._case.move("rules_checked", "accepted")
            self._case.move("decided", result)
        return result

    def escalate_to_human(self, patient_id: str, reason: str, flags: list[Flag] | None = None) -> Decision:
        started = self._check("escalate_to_human", {})
        assessment = self._assessments.get(patient_id)
        raised = list(dict.fromkeys((flags or []) + (assessment.flags if assessment else [])))   # no flag is dropped
        result = escalation.escalate_to_human(patient_id, reason, raised)
        if assessment is not None and assessment.target != "internal_medicine":
            result.target_ward = assessment.target
        self._record("escalate_to_human", {"patient_id": patient_id, "reason": reason}, "escalated", started)
        if self._case is not None:
            if self._case.step in ("ranked", "negotiating", "rules_checked"):
                self._case.move("rules_checked", "an escalation always passes")
            self._case.move("decided", result)
        return result
