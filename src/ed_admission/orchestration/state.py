"""The state of one placement case in the multi-agent system — the one state the LangGraph graph passes from node to
node, saved after every node (LangGraph's checkpointer: in memory in tests, outputs/checkpoints.sqlite in the
scripted days).

Each node reads only its own fields (DECISIONS.md): an agent's prompt is built from the fields it may see, never from
the whole state. Never in the state: the record itself (only the assessment and its quotes), a ward's private notes
(only its answer), anything of another patient.

Fields written by parallel runs — the Department Agent, once per ward on a rung — are lists (and the cost) that
LangGraph joins (`operator.add`) instead of letting one run overwrite another. A ward asked again with the patient's answer adds a
second answer; its latest answer is the one that counts (`latest_ward_answers`).

Owner: Lea Gur-Arie
"""
import operator
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ed_admission.contracts.agent_outputs import (AssessmentWithConfidence, HandoffCheck, JudgeVerdict, PatientTask,
                                                  PlacementProposal, WardSummary)
from ed_admission.environment.decision import Decision
from ed_admission.orchestration.proposal import wards_on_rung
from ed_admission.tools.rank_placements import Option


class Asked(BaseModel):
    """One ward asked for one bed type — what the code checks of an escalation look at (rubric item 11)."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    ward_id: str
    bed_type: str
    rung: int = Field(ge=1)


class CaseState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: PatientTask
    assessment: AssessmentWithConfidence | None = None          # the Patient Agent, after its handoff check
    options: list[Option] = []                                   # rank_placements, in ladder order
    target: str | None = None                                    # the target ward, internal medicine resolved to A/B
    rung: int = Field(1, ge=1)                                   # the ladder level being asked now
    bed_type: str | None = None                                  # the bed asked for now (the need, then the last resort)
    asked: Annotated[list[Asked], operator.add] = []             # every ward asked, for which bed
    ward_answers: Annotated[list[WardSummary], operator.add] = []
    patient_answers: dict[str, bool] = {}                        # ward id -> the answer to its one question (code)
    reasked: list[str] = []                                      # wards asked again with that answer, this assessment
    pending: list[str] = []                                      # wards to ask again now
    asked_from: int = 0                                          # where this assessment's requests start in `asked`
    handoff_checks: Annotated[list[HandoffCheck], operator.add] = []
    proposal: PlacementProposal | None = None                    # written by code; carries the code checks
    verdict: JudgeVerdict | None = None                          # the Judge Agent, on a proposal that passed code
    correction_round_used: bool = False                          # the Judge's one correction round
    rejected: list[str] = []                                     # wards whose bed the code checks blocked, this pass
    cost_usd: Annotated[float, operator.add] = 0.0               # every agent run adds its cost
    escalation: str | None = None                                # why the case goes to the shift manager, in words
    decision: Decision | None = None                             # the end: a placement or wait held, or an escalation

    @property
    def latest_ward_answers(self) -> dict[str, WardSummary]:
        """Each ward's latest answer: a ward asked again with the patient's answer replaces its first answer."""
        return {answer.ward_id: answer for answer in self.ward_answers}

    def wards_on_rung(self) -> list[str]:
        """The wards the graph asks now, in parallel: every ward on the current level of the target ward's ladder —
        not only the ones the report shows a bed in (a note can free a bed)."""
        return wards_on_rung(self.target, self.rung) if self.target else []
