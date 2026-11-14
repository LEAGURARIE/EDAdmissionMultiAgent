"""What the agents return — the contracts between each agent and the rest of the system, and what each is measured on.

Shared with the single agent (unchanged for it, 2026-10-28: the single agent stays as it is):
  Finding                  one claim about the patient and the exact words of the record that back it
  PatientAssessment        the full assessment of the patient

The multi-agent system (approved by Lea 2026-11-08 — the Coordinator is code, the LangGraph graph):
  PatientTask              what the graph gives the Patient Agent: the patient id and the decision minute, nothing else
  FindingWithConfidence    a Finding with high / medium / low confidence next to its quote
  AssessmentWithConfidence the Patient Agent's assessment: no extra fields, every finding with its confidence
  WardTask                 what the graph gives one run of the Department Agent: one ward, the needs only
  WardSummary              one ward's answer and the ward's id, never its private notes
  HandoffCheck             the code check after every agent: passed or not, and which closed failures
  CodeChecks               the Judge's layer 1: the rubric items code checks — they block
  PlacementProposal        written by code: the highest-ranked ward that accepted, a wait, or none
  JudgeVerdict             the Judge's layer 2, the Judge Agent: only the rubric items the model checks
  RubricItem               one rubric item: passed or not, and why

Routing in the graph reads closed fields only (an answer, a kind, valid, passed) — never free text. Free text is
shown, quoted and checked, and is limited in length.

Owner: Lea Gur-Arie
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ed_admission import config
from ed_admission.contracts.negotiation import WardAnswer, WardRequest
from ed_admission.environment.decision import Flag

LIMIT = config.text_limits()             # the longest free text of each handoff field (config/agents.yaml)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class Finding(BaseModel):
    """One thing the Patient Agent states about the patient, and the exact words of the record that back it."""
    claim: str
    quote: str
    grounded: bool | None = None         # set by code: the quote really appears in the record


class PatientAssessment(AgentOutput):
    """What the Patient Agent returns: everything the rest of the system needs to know about the patient — nothing
    else reads the record. One agent reads the record once and answers all of it."""
    target_ward: str                     # the ward the patient is admitted to (level 1 of the ladder) — or
                                         # "internal_medicine" when the record names neither A nor B: code
                                         # chooses by occupancy (tools/rank_placements.py)
    binding_request: bool = False        # the physician requested this ward: never changed
    severe: bool                         # needs observation or intensive care: never waits
    intensive_care: bool
    observation: bool
    isolation: bool
    flags: list[Flag] = []               # prompt_injection, missing_vital_sign, severity_judgment_note
    note: str | None = None              # the judgment note: the level is kept, but the record argues for less
    findings: list[Finding] = []

    @property
    def bed_type(self) -> str:
        """The bed the patient needs: intensive care; isolation (also with observation); observation; regular."""
        if self.intensive_care:
            return "intensive_care"
        if self.isolation:
            return "isolation"
        return "observation" if self.observation or self.severe else "regular"

    @property
    def target(self) -> str:
        """The target ward the bed search and the hard rules use: intensive care whenever the patient needs intensive
        care — the physician's request or NEWS2 7 or more (a system rule, in code for both systems, 2026-10-15) —
        else target_ward as assessed."""
        return "intensive_care" if self.intensive_care else self.target_ward

    @property
    def never_waits(self) -> bool:
        return self.severe or self.observation or self.intensive_care


# ---------------------------------------------------------------- the multi-agent system (2026-11-08)

class Strict(BaseModel):
    """A handoff contract: an extra field is a failure of the handoff check, never silently kept."""
    model_config = ConfigDict(extra="forbid")


# ---- the Patient Agent

class PatientTask(Strict):
    """What the graph gives the Patient Agent — only what the environment reveals: no hint, no expected answer. On the
    one correction round after the Judge Agent failed item 2 (a quote read wrong), the Judge's correction too."""
    patient_id: str = Field(pattern=r"^P-\d{3}$")
    decided_at: int = Field(ge=0)        # simulated minute of the admission decision
    judge_feedback: str | None = Field(None, max_length=LIMIT["correction"])   # the one correction round


Need = Literal["severe", "intensive_care", "observation", "isolation"]
NEEDS: tuple[Need, ...] = ("severe", "intensive_care", "observation", "isolation")


class FindingWithConfidence(Finding):
    """A finding of the multi-agent system: the quote stays, and the confidence stands next to it, never instead of
    it. The confidence is how well the quote backs the claim, never how sure the diagnosis is (the levels are defined
    once, in the tool's field: agents/orchestra_tools.py, ConfidentFindingInput). A low-confidence finding that bears on a need is shown to the shift manager and never lowers the need.
    `backs`: the needs this finding is the evidence for — closed values, so code can check every need has one."""
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=LIMIT["claim"])
    quote: str = Field(min_length=1, max_length=LIMIT["quote"])
    confidence: Literal["high", "medium", "low"]
    backs: list[Need] = []


class AssessmentWithConfidence(PatientAssessment):
    """The Patient Agent's assessment in the multi-agent system: the same fields as the single agent's, no extra
    field, and every finding with its confidence. Code fills `grounded` in the handoff check, never the agent.
    Every need set to true is backed by at least one finding (Lea, 2026-11-08) — a need without evidence fails here,
    in code, and goes back to the agent. A severe patient is backed by a finding for severe, observation or
    intensive care: those needs are what makes a patient severe."""
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(None, max_length=LIMIT["note"])
    findings: list[FindingWithConfidence] = []

    @model_validator(mode="after")
    def every_need_has_a_finding(self) -> "AssessmentWithConfidence":
        wards = set(config.ladders()) | {"internal_medicine"}
        if self.target_ward not in wards:
            raise ValueError(f"{self.target_ward!r} is not a ward: one of {sorted(wards)}")
        backed = {need for finding in self.findings for need in finding.backs}
        if backed & {"observation", "intensive_care"}:
            backed.add("severe")
        unbacked = [need for need in NEEDS if getattr(self, need) and need not in backed]
        if unbacked:
            raise ValueError(f"no finding backs {', '.join(unbacked)}: add the finding and its quote, with the need "
                             "in `backs`")
        return self

    @property
    def low_confidence(self) -> list[FindingWithConfidence]:
        return [finding for finding in self.findings if finding.confidence == "low"]

    @property
    def stable(self) -> bool:
        """The answer to a ward's closed question "stable" (WardAnswer.asks), given by code, not by a model (Lea,
        2026-11-08): stable means not severe — no observation, no intensive care, not severe. The same reading as
        the expected answers (template T19)."""
        return not self.never_waits


# ---- the Department Agent

class WardTask(Strict):
    """What the graph gives one run of the Department Agent: one ward, the patient's needs only — no identifier, no
    record. `ward_id` is also what the tools allow this run to see: that ward's report and notes, no other."""
    ward_id: str
    request: WardRequest
    patient_answer: bool | None = None   # the answer to this ward's question (stable), on its second run — by code
    judge_feedback: str | None = Field(None, max_length=LIMIT["correction"])   # the one correction round


class WardSummary(WardAnswer):
    """What one ward answered, as it is kept in the case state — the ward's answer, never its private notes."""
    ward_id: str


# ---- the checks in code

class HandoffFailure(str, Enum):
    """Why a handoff check failed, from a closed list."""
    SCHEMA = "schema"                                # a missing or extra field, a wrong type
    VALUE_NOT_ALLOWED = "value_not_allowed"          # e.g. a ward that does not exist, a rubric item not the Judge's
    BED_NOT_IN_WARD = "bed_not_in_ward"              # the bed named is not a bed of that ward
    TOO_LONG = "too_long"                            # free text over its limit
    QUOTE_NOT_IN_RECORD = "quote_not_in_record"      # a finding's quote does not appear in the record
    BED_NOT_IN_REPORT = "bed_not_in_report"          # source "report", but the report does not show the bed coming free
    NO_WARD_NOTE = "no_ward_note"                    # source "note", but the ward has no note


class HandoffCheck(Strict):
    """The check in code after every agent, kept in the case state and the trace. Routing reads `passed` only."""
    stage: Literal["patient", "wards", "judge"]
    passed: bool
    failures: list[HandoffFailure] = []
    ungrounded_quotes: list[str] = []
    attempt: int = Field(1, ge=1, le=2)  # 2 is the one retry; failing again escalates

    @model_validator(mode="after")
    def passed_means_no_failure(self) -> "HandoffCheck":
        if self.passed == bool(self.failures):
            raise ValueError("passed with failures, or failed without naming one")
        return self


class RubricItem(Strict):
    """One item of the Judge's rubric (config/judge_rubric.yaml): passed or not, and why, in one line."""
    number: int = Field(ge=1)
    passed: bool
    reason: str = Field(min_length=1, max_length=LIMIT["reason"])

    @property
    def blocking(self) -> bool:
        return config.judge_rubric()[self.number]["blocking"]

    @property
    def checked_by(self) -> str:
        return config.judge_rubric()[self.number]["checked_by"]

    @model_validator(mode="after")
    def a_rubric_item(self) -> "RubricItem":
        if self.number not in config.judge_rubric():
            raise ValueError(f"no rubric item {self.number}")
        return self


def _only_items_checked_by(items: list[RubricItem], who: str) -> None:
    """Each layer of the Judge returns only its own items, each once."""
    numbers = [item.number for item in items]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"a rubric item twice: {numbers}")
    others = [item.number for item in items if item.checked_by != who]
    if others:
        raise ValueError(f"items {others} are not checked by {who}")


class CodeChecks(Strict):
    """The Judge's layer 1: the rubric items code checks (1, 3–7, 10, 11). They block: a proposal that fails goes
    back to the ranking, and the Judge Agent never sees it."""
    items: list[RubricItem]

    @model_validator(mode="after")
    def code_items_only(self) -> "CodeChecks":
        _only_items_checked_by(self.items, "code")
        return self

    @property
    def passed(self) -> bool:
        return not any(item.blocking and not item.passed for item in self.items)


# ---- the proposal and the Judge Agent

class PlacementProposal(Strict):
    """Written by code: the highest-ranked ward on the ladder that accepted, a wait, or none — with every ward asked,
    the code checks, and the low-confidence findings the shift manager must see."""
    kind: Literal["place", "wait", "none"]
    ward_id: str | None = None
    bed_number: int | None = None
    rung: int = Field(ge=1)              # the ladder level the ward is on
    reason: str = Field(min_length=1)    # written by code from the wards' answers
    wards: list[WardSummary] = []        # every ward asked, rung by rung
    code_checks: CodeChecks | None = None
    low_confidence: list[FindingWithConfidence] = []
    bed_from_note: bool = False          # the bed comes from a ward's note: the system has not caught up with it —
                                         # marked for the shift manager

    @model_validator(mode="after")
    def fields_match_the_kind(self) -> "PlacementProposal":
        if self.kind in ("place", "wait") and (self.ward_id is None or self.bed_number is None):
            raise ValueError(f"a {self.kind} proposal names the ward and the bed")
        if self.kind == "none" and (self.ward_id or self.bed_number):
            raise ValueError("a proposal of no bed names no ward and no bed")
        return self


class JudgeVerdict(Strict):
    """The Judge's layer 2, the Judge Agent: it reads the case's trace up to a proposal that passed layer 1 and
    returns only the items the model checks (2, 8, 9). Valid only if no blocking item failed. Not valid: what to fix
    in the one correction round. Routing reads `valid` only."""
    valid: bool
    items: list[RubricItem] = Field(min_length=1)
    correction: str | None = Field(None, max_length=LIMIT["correction"])

    @model_validator(mode="after")
    def valid_means_no_blocking_failure(self) -> "JudgeVerdict":
        _only_items_checked_by(self.items, "judge")
        theirs = {number for number, item in config.judge_rubric().items() if item["checked_by"] == "judge"}
        if {item.number for item in self.items} != theirs:          # no item skipped (Lea, 2026-11-08)
            raise ValueError(f"answer every one of the Judge's items: {sorted(theirs)}")
        failed = [item.number for item in self.items if not item.passed and item.blocking]
        if self.valid and failed:
            raise ValueError(f"valid, but blocking items failed: {failed}")
        if not self.valid and not failed:
            raise ValueError("not valid: name the blocking item that failed")
        if not self.valid and not self.correction:
            raise ValueError("not valid: say what to fix")
        return self

    @property
    def correction_goes_to(self) -> Literal["patient", "wards"] | None:
        """Who gets the one correction round — read from the failed item's number, a closed value, never from the
        correction's words (Lea, 2026-11-08): item 2 failed (a need rests on a quote read wrong) -> the Patient Agent,
        who reads the whole record, assesses again — and the ranking and the wards follow its new assessment; only
        item 8 failed (the reason does not match the wards' answers) -> the wards. None: valid, nothing to correct."""
        failed = {item.number for item in self.items if not item.passed and item.blocking}
        if not failed:
            return None
        return "patient" if 2 in failed else "wards"
