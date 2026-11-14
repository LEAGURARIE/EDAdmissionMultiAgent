"""The toolbox each agent gets — permissions are enforced here, in code, never by a prompt.

    toolbox = Toolbox("patient", engine)                toolbox.compute_news2("P-001")   ✓
    toolbox = Toolbox("department", engine, ward_id="cardiology")
    toolbox.read_patient_record("P-001")                -> PermissionDenied: the Department Agent never sees a record

The roles (2026-10-10; since 2026-11-08 the Coordinator is code — the role of the graph's code, no model) —
each sees what the others do not:

  patient       the record only: read_patient_record, compute_news2, clinical_lists — and record_assessment, its
                answer (the multi-agent system, 2026-11-08)
  department    its own ward only: department_view (its report and private notes); bound to one ward in advance, and
                it gets the patient's needs from the Coordinator's request — never an identifier; answer_ward hands in
                its answer, checked in code: the bed is one of its ward's, and where it knows the bed is free
  judge         the case up to the proposal, as data — read_case (2026-11-08): the findings with the sentence of the
                record each quote came from, the wards' answers with the notes of the wards that answered, the
                proposal and the code checks. Never the record itself, never a ward that was not asked —
                and give_verdict, its answer on items 2, 8 and 9
The Coordinator has no toolbox: it is the graph's code (orchestration/graph.py), which routes on the agents' closed
answers and checks the proposal in code (orchestration/code_checks.py).

No agent holds or releases a bed: the decision is the hold — the environment holds the bed when the case closes
(simulation_engine.py, hold_bed_when), for the shift manager's approval.

  single_agent  one agent doing every agent's job (the fair comparison, 2026-10-15): the record and its own
                assessment (record_assessment, in the Patient Agent's format), every ward's report, and every
                ward's report and notes (department_view with a ward id) — it reads the notes itself, as the
                Department Agents do; the ranking, escalate_to_human, and decide (the hard rules, checked in code —
                tools/decide.py).

PERMISSIONS lists which role may call which tool. The single agent of the comparison does every agent's job with
their tools, so the comparison tests the architecture, not access to information.
Every call is recorded in the trace when one is given (observability/tracing.py).

Owner: Lea Gur-Arie
"""
import time

from ed_admission import config
from ed_admission.contracts.agent_outputs import PatientAssessment
from ed_admission.contracts.agent_outputs import JudgeVerdict, WardSummary
from ed_admission.contracts.negotiation import WardAnswer
from ed_admission.contracts.patient_record import Vitals
from ed_admission.environment.decision import Decision, Flag
from ed_admission.observability.tracing import Claim
from ed_admission.tools import department_status, escalation, patient_record
from ed_admission.tools import decide as decide_tool
from ed_admission.tools.negotiation import DepartmentView, bed_source_failure
from ed_admission.tools.news2 import compute_news2, level_of_care
from ed_admission.tools.rank_placements import PatientNeeds, Ranking, rank_placements, resolve_target_ward

ROLES = ("patient", "department", "judge", "single_agent")
PERMISSIONS = {
    "read_patient_record": {"patient", "single_agent"},
    "compute_news2": {"patient", "single_agent"},
    "clinical_lists": {"patient", "single_agent"},
    "record_assessment": {"patient", "single_agent"},
    "decide": {"single_agent"},
    "all_department_status": {"single_agent"},
    "department_view": {"department", "single_agent"},
    "answer_ward": {"department"},
    "read_case": {"judge"},
    "give_verdict": {"judge"},
    "rank_placements": {"single_agent"},
    "escalate_to_human": {"single_agent"},
}


class PermissionDenied(Exception):
    """A role called a tool it may not use."""


class Toolbox:
    def __init__(self, role: str, engine, ward_id: str | None = None, trace=None):
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        if (role == "department") != (ward_id is not None):
            raise ValueError("a Department Agent's toolbox is bound to one ward, and only it is")
        self.role, self._engine, self._ward_id, self._trace = role, engine, ward_id, trace
        self._assessments: dict[str, PatientAssessment] = {}       # what the Patient Agent found, by patient

    def _check(self, tool: str, arguments: dict) -> float:
        if self.role not in PERMISSIONS[tool]:
            raise PermissionDenied(f"{self.role} may not use {tool}")
        return time.perf_counter()

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

    def answer_ward(self, answer: WardAnswer) -> WardSummary:
        """The Department Agent hands in its ward's answer. Checked in code before it leaves the ward: a bed must be
        one of this ward's beds, and its source must hold — a bed "from the report" the report shows coming free, a
        bed "from a note" in a ward that has a note (tools/negotiation.py). A failure is refused with the reason."""
        started = self._check("answer_ward", {})
        if answer.bed_number is not None:
            if answer.bed_number not in {bed.number for bed in self._engine.hospital.ward(self._ward_id).beds}:
                raise ValueError(f"bed_not_in_ward: {self._ward_id} has no bed {answer.bed_number}")
            failure = bed_source_failure(answer, department_status.department_view(self._engine, self._ward_id))
            if failure == "bed_not_in_report":
                raise ValueError(f"bed_not_in_report: the report does not show bed {answer.bed_number} free, in "
                                 "cleaning or expected to be discharged — if a note frees it, the source is note")
            if failure == "no_ward_note":
                raise ValueError("no_ward_note: this ward has no note — a bed can come from the report only")
        summary = WardSummary(ward_id=self._ward_id, **answer.model_dump())
        self._record("answer_ward", {"ward_id": self._ward_id}, summary.model_dump(mode="json"), started)
        return summary

    def read_case(self, state) -> dict:
        """The Judge Agent's case file, built by code from the case state (orchestration/state.py) up to a proposal
        that passed the code checks. Each finding comes with the sentence of the record its quote was taken from —
        a quote can be found and still be cut short; each ward that answered comes with its notes — so the Judge can
        see a bed "from a note" is in the note. No patient identifier, never the whole record."""
        started = self._check("read_case", {})
        if state.assessment is None or state.proposal is None:
            raise ValueError("read_case needs the assessment and a proposal")
        patient_id, assessment, proposal = state.task.patient_id, state.assessment, state.proposal
        result = {
            "assessment": assessment.model_dump(mode="json", include={
                "target_ward", "binding_request", "severe", "intensive_care", "observation", "isolation", "flags",
                "note"}),
            "findings": [{**finding.model_dump(mode="json"),
                          "sentence": patient_record.sentence_around(patient_id, finding.quote)}
                         for finding in assessment.findings],
            "wards": [{**answer.model_dump(mode="json"), "notes": self._engine.ward_notes(answer.ward_id)}
                      for answer in state.latest_ward_answers.values()],
            "proposal": proposal.model_dump(mode="json", include={"kind", "ward_id", "bed_number", "rung",
                                                                   "reason"}),
            "code_checks": [item.model_dump(mode="json")
                            for item in (proposal.code_checks.items if proposal.code_checks else [])],
        }
        self._record("read_case", {}, "case file", started)
        return result

    def give_verdict(self, verdict: JudgeVerdict) -> JudgeVerdict:
        """The Judge Agent hands in its verdict — already checked by its contract: every one of its items answered,
        valid only with no blocking item failed, and a correction when not valid."""
        started = self._check("give_verdict", {})
        self._record("give_verdict", {}, verdict.model_dump(mode="json"), started)
        return verdict

    def rank_placements(self, needs: PatientNeeds, taken: set = frozenset()) -> Ranking:
        started = self._check("rank_placements", {})
        result = rank_placements(department_status.all_department_status(self._engine), needs, taken)
        self._record("rank_placements", {"needs": needs.__dict__}, [o.__dict__ for o in result.options], started)
        return result

    # ---------------------------------------------------------------- acting
    def decide(self, decision: Decision) -> Decision:
        """Hand in the decision. The hard rules are checked in code against the kept assessment
        (tools/decide.py): a decision that breaks one is refused with the reasons (RuleViolation)."""
        started = self._check("decide", {})
        assessment = self._assessments.get(decision.patient_id)
        if assessment is None:
            raise ValueError(f"assess {decision.patient_id} first: there is nothing to check the decision against")
        try:
            result = decide_tool.decide(decision, assessment)
        except decide_tool.RuleViolation as violation:
            self._record("decide", decision.model_dump(mode="json"), {"refused": violation.reasons}, started)
            raise
        self._record("decide", decision.model_dump(mode="json"), "accepted", started)
        return result

    def escalate_to_human(self, patient_id: str, reason: str, flags: list[Flag] | None = None) -> Decision:
        started = self._check("escalate_to_human", {})
        assessment = self._assessments.get(patient_id)
        raised = list(dict.fromkeys((flags or []) + (assessment.flags if assessment else [])))   # no flag is dropped
        result = escalation.escalate_to_human(patient_id, reason, raised)
        if assessment is not None and assessment.target != "internal_medicine":
            result.target_ward = assessment.target
        self._record("escalate_to_human", {"patient_id": patient_id, "reason": reason}, "escalated", started)
        return result
