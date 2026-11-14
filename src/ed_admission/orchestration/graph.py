"""The Coordinator — code, not an agent: the LangGraph graph of one patient's placement (DECISIONS.md, 2026-11-08).

  patient_agent ─► rank ─► department_agent (every ward on the rung, in parallel) ─► wards_answered
        ▲                         ▲   ▲                                                    │
        │                         │   └─ a ward asked "stable": code answers, that ward again (once)
        │                         │                                                        ▼
        │                         └──────────── nothing on this rung: the next rung ◄── propose
        │                                                                                  │
        │                                                                          code_checks (layer 1)
        │                                                     blocked: the next option ◄───┤
        │                                                                                  ▼
        └── item 2 failed (one correction round) ◄──────────────────────────────── judge_agent (layer 2)
                                       item 8 failed: the wards again ◄────────────────────┤
                                                                                  valid ─► hold    · else ─► escalate

Every agent's answer passes validate_handoff (orchestration/handoff.py) inside its node; a failure gets one more try,
then the case is escalated. The graph routes on closed fields only — `passed`, `answer`, `asks`, `kind`, `valid` and
the number of a failed rubric item — never on free text. The limits are edges code allows or not:
  - one correction round · one question per ward · the request limits (placement_rules.yaml, negotiation), counted
    per assessment: a new assessment after the Judge's correction asks the wards afresh
  - the case's cost cap (config/agents.yaml, case_cost_cap_usd): no agent runs once the case has spent it
  - no decision without the Judge: a placement or a wait only after both layers passed; an escalation for lack of a
    bed only after the code checks found every ward asked (item 11)

The agents come in from outside (`Agents`): in step 3b scripted ones, for the tests; in step 4 the real ones — the
same agent loop and tools (agents/orchestra_tools.py). Each returns its answer and what it cost.

Owner: Lea Gur-Arie
"""
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict

from ed_admission import config
from ed_admission.agents.budget import agents_config
from ed_admission.contracts.agent_outputs import PatientTask, WardTask
from ed_admission.contracts.negotiation import Answer, WardRequest
from ed_admission.environment.decision import Decision
from ed_admission.orchestration.code_checks import code_checks
from ed_admission.orchestration.handoff import validate_handoff
from ed_admission.orchestration.proposal import bed_types, no_bed, rungs, to_decision, write_proposal
from ed_admission.orchestration.state import Asked, CaseState
from ed_admission.tools import department_status
from ed_admission.tools.decide import decide
from ed_admission.tools.rank_placements import PatientNeeds, rank_placements, resolve_target_ward


@dataclass
class Agents:
    """The three agents, as the graph calls them. Each returns (its answer, its cost in USD)."""
    patient: Callable[[PatientTask], tuple[Any, float]]
    department: Callable[[WardTask], tuple[Any, float]]
    judge: Callable[[CaseState], tuple[Any, float]]


def checkpoint_serde() -> JsonPlusSerializer:
    """How LangGraph saves the case state: our own types are allowed by name, nothing else is loaded back."""
    from ed_admission.contracts import agent_outputs as out
    from ed_admission.contracts import negotiation
    from ed_admission.environment import decision
    from ed_admission.orchestration import state
    from ed_admission.tools import rank_placements
    types = (out.PatientTask, out.HandoffCheck, out.HandoffFailure, out.AssessmentWithConfidence,
             out.FindingWithConfidence, out.WardSummary, out.PlacementProposal, out.CodeChecks, out.RubricItem,
             out.JudgeVerdict, out.WardTask, negotiation.Answer, negotiation.WardRequest, decision.Action,
             decision.Decision, decision.Flag, decision.WaitFor, state.Asked, rank_placements.Option)
    return JsonPlusSerializer(allowed_msgpack_modules=[(t.__module__, t.__name__) for t in types])


class WardRun(BaseModel):
    """What one parallel run of the Department Agent node gets from the graph (LangGraph Send)."""
    model_config = ConfigDict(extra="forbid")
    task: WardTask
    rung: int


def _limits() -> dict:
    return config.placement_rules()["negotiation"]


def _case_cap() -> float:
    return agents_config().budget.case_cost_cap_usd["multi_agent"]


def build_graph(agents: Agents, engine, checkpointer=None, taken: set | None = None, interrupt_after=None):
    """The compiled graph for one patient. `engine`: the hospital the case runs in (its reports and notes).
    `taken`: (ward, bed) already given to another patient of the same case — never proposed again, never offered
    in a request. `interrupt_after`: nodes to pause after (the decider pauses every patient after the assessment,
    to order the patients of a case before any bed is searched)."""
    taken = set() if taken is None else taken

    def reports() -> dict[str, dict]:
        return department_status.all_department_status(engine)

    # ---------------------------------------------------------------- the Patient Agent
    def patient_agent(state: CaseState) -> dict:
        checks, cost = [], 0.0
        for attempt in (1, 2):                                   # one more try after a failed handoff
            answer, spent = agents.patient(state.task)
            cost += spent
            check, assessment = validate_handoff("patient", answer, attempt=attempt,
                                                 patient_id=state.task.patient_id)
            checks.append(check)
            if check.passed:
                return {"assessment": assessment, "handoff_checks": checks, "cost_usd": cost}
        return {"handoff_checks": checks, "cost_usd": cost,
                "escalation": "the Patient Agent's answer failed the handoff check twice"}

    def after_patient(state: CaseState) -> str:
        return "rank" if state.escalation is None else "escalate"

    # ---------------------------------------------------------------- the ranking (code)
    def rank(state: CaseState) -> dict:
        everything = reports()
        target = resolve_target_ward(state.assessment.target, everything)
        needs = PatientNeeds(target, state.assessment.bed_type, severe=state.assessment.never_waits)
        return {"target": target, "rung": 1, "bed_type": bed_types(state.assessment)[0], "rejected": [],
                "proposal": None, "patient_answers": {}, "reasked": [], "pending": [],      # a new assessment
                "asked_from": len(state.asked),
                "options": rank_placements(everything, needs).options}

    def request(state: CaseState, ward_id: str) -> WardRequest:
        free = [b["bed"] for b in engine.department_status(ward_id)["free_beds"]
                if b["bed_type"] == state.bed_type and (ward_id, b["bed"]) not in taken]
        return WardRequest(bed_type=state.bed_type, severe=state.assessment.never_waits,
                           observation=state.assessment.observation, isolation=state.assessment.isolation,
                           target_ward=state.target, proposed_bed=free[0] if free else None)

    def ask(state: CaseState, wards: list[str], **more) -> list[Send] | str:
        """Send the request to these wards, in parallel — or escalate when a limit would be passed."""
        if state.cost_usd >= _case_cap():
            return "escalate_budget"
        asked = [a.ward_id for a in state.asked[state.asked_from:]] + wards    # this assessment's requests
        if len(asked) > _limits()["max_requests_per_case"] or any(
                asked.count(w) > _limits()["max_requests_per_ward"] for w in wards):
            return "escalate_limit"
        return [Send("department_agent", WardRun(task=WardTask(ward_id=w, request=request(state, w), **more.get(w, {})),
                                                 rung=state.rung))
                for w in wards]

    def ask_the_rung(state: CaseState):
        return ask(state, state.wards_on_rung())

    # ---------------------------------------------------------------- the Department Agent, one run per ward
    def department_agent(run: WardRun) -> dict:
        ward_id, checks, cost = run.task.ward_id, [], 0.0
        view = department_status.department_view(engine, ward_id)
        beds = {bed.number for bed in engine.hospital.ward(ward_id).beds}
        asked = [Asked(ward_id=ward_id, bed_type=run.task.request.bed_type, rung=run.rung)]
        for attempt in (1, 2):
            answer, spent = agents.department(run.task)
            cost += spent
            if isinstance(answer, BaseModel) and not hasattr(answer, "ward_id"):
                answer = {**answer.model_dump(mode="json"), "ward_id": ward_id}
            elif isinstance(answer, dict):
                answer = {**answer, "ward_id": ward_id}          # the ward is the run's, whatever the answer says
            check, summary = validate_handoff("wards", answer, attempt=attempt, view=view, ward_beds=beds)
            checks.append(check)
            if check.passed:
                return {"ward_answers": [summary], "handoff_checks": checks, "asked": asked, "cost_usd": cost}
        return {"handoff_checks": checks, "asked": asked, "cost_usd": cost}

    def wards_answered(state: CaseState) -> dict:
        """After a rung (or a ward asked again) answered. A ward that asked the closed question "stable" gets code's
        answer from the assessment — stable means not severe — and is asked again, once: `pending`."""
        pending = [ward for ward, answer in state.latest_ward_answers.items()
                   if answer.asks == "stable" and answer.answer == Answer.CONDITION and ward not in state.reasked]
        update = {"patient_answers": {**state.patient_answers, **{w: state.assessment.stable for w in pending}},
                  "reasked": state.reasked + pending, "pending": pending}
        if any(c.stage == "wards" and not c.passed and c.attempt == 2 for c in state.handoff_checks):
            update["escalation"] = "a Department Agent's answer failed the handoff check twice"
        return update

    def after_wards(state: CaseState):
        if state.escalation:
            return "escalate"
        if state.pending:
            return ask(state, state.pending, **{w: {"patient_answer": state.patient_answers[w]} for w in state.pending})
        return "propose"

    # ---------------------------------------------------------------- the proposal (code)
    def propose(state: CaseState) -> dict:
        on_rung = [state.latest_ward_answers[w] for w in state.wards_on_rung() if w in state.latest_ward_answers
                   and (w, state.latest_ward_answers[w].bed_number) not in taken]       # given to another patient
        every = list(state.latest_ward_answers.values())
        proposal = write_proposal(state.assessment, state.target, state.rung, on_rung, reports(), every,
                                  frozenset(state.rejected))
        if proposal is not None:
            return {"proposal": proposal}
        if state.rung < rungs(state.target):
            return {"rung": state.rung + 1, "proposal": None}
        kinds = bed_types(state.assessment)
        if kinds.index(state.bed_type) + 1 < len(kinds):            # the last resort: the ladder again
            return {"bed_type": kinds[kinds.index(state.bed_type) + 1], "rung": 1, "proposal": None,
                    "rejected": []}
        return {"proposal": no_bed(state.assessment, state.target, every)}

    def after_propose(state: CaseState):
        return "code_checks" if state.proposal is not None else ask_the_rung(state)

    # ---------------------------------------------------------------- the Judge, layer 1 (code)
    def check_proposal(state: CaseState) -> dict:
        checks = code_checks(state.proposal, state.assessment, state.target, state.task.patient_id,
                             state.latest_ward_answers, state.asked, reports(), frozenset(state.rejected),
                             frozenset(taken))
        proposal = state.proposal.model_copy(update={"code_checks": checks})
        if checks.passed:
            update = {"proposal": proposal}
            if proposal.kind == "none":
                update["escalation"] = proposal.reason
            return update
        failed = "; ".join(f"item {i.number}: {i.reason}" for i in checks.items if not i.passed and i.blocking)
        if proposal.kind == "none":
            return {"proposal": proposal, "escalation": f"no bed, and the code checks failed — {failed}"}
        return {"proposal": None, "rejected": state.rejected + [proposal.ward_id]}

    def after_code_checks(state: CaseState) -> str:
        if state.escalation:
            return "escalate"
        if state.proposal is None:
            return "propose"                                        # the next option
        return "judge_agent" if state.cost_usd < _case_cap() else "escalate_budget"

    # ---------------------------------------------------------------- the Judge, layer 2 (the Judge Agent)
    def judge_agent(state: CaseState) -> dict:
        checks, cost = [], 0.0
        for attempt in (1, 2):
            answer, spent = agents.judge(state)
            cost += spent
            check, verdict = validate_handoff("judge", answer, attempt=attempt)
            checks.append(check)
            if check.passed:
                return {"verdict": verdict, "handoff_checks": checks, "cost_usd": cost}
        return {"handoff_checks": checks, "cost_usd": cost,
                "escalation": "the Judge Agent's answer failed the handoff check twice"}

    def after_judge(state: CaseState):
        if state.escalation:
            return "escalate"
        if state.verdict.valid:
            return "hold"
        if state.correction_round_used:
            return "escalate_judge"
        return "correct"

    def correct(state: CaseState) -> dict:
        """The one correction round: to whoever made the mistake — read from the failed item's number."""
        update = {"correction_round_used": True}
        if state.verdict.correction_goes_to == "patient":
            update["task"] = state.task.model_copy(update={"judge_feedback": state.verdict.correction})
        else:
            update["rejected"] = []
        return update

    def after_correct(state: CaseState):
        if state.cost_usd >= _case_cap():
            return "escalate_budget"
        if state.verdict.correction_goes_to == "patient":
            return "patient_agent"
        wards = [w for w in state.wards_on_rung()]
        return ask(state, wards, **{w: {"judge_feedback": state.verdict.correction,
                                        "patient_answer": state.patient_answers.get(w)} for w in wards})

    # ---------------------------------------------------------------- the end
    def hold(state: CaseState) -> dict:
        """Both layers passed: the decision goes to the shift manager — the hard rules checked one last time."""
        return {"decision": decide(to_decision(state.proposal, state.task.patient_id, state.assessment, state.target),
                                   state.assessment)}

    def escalate(state: CaseState) -> dict:
        flags = list(state.assessment.flags) if state.assessment else []
        reason = state.escalation or "escalated"
        decision = Decision.escalate(state.task.patient_id, reason, flags)
        if state.target:
            decision = decision.model_copy(update={"target_ward": state.target})
        return {"decision": decision, "escalation": reason}

    def escalate_because(reason: str):
        def node(state: CaseState) -> dict:
            return {"escalation": reason}
        return node

    graph = StateGraph(CaseState)
    graph.add_node("patient_agent", patient_agent)
    graph.add_node("rank", rank)
    graph.add_node("department_agent", department_agent, input_schema=WardRun)
    graph.add_node("wards_answered", wards_answered)
    graph.add_node("propose", propose)
    graph.add_node("code_checks", check_proposal)
    graph.add_node("judge_agent", judge_agent)
    graph.add_node("correct", correct)
    graph.add_node("hold", hold)
    graph.add_node("escalate", escalate)
    graph.add_node("escalate_budget", escalate_because("the case reached its cost cap"))
    graph.add_node("escalate_limit", escalate_because("the request limits were reached"))
    graph.add_node("escalate_judge", escalate_because("the Judge Agent rejected the proposal after its correction "
                                                      "round"))

    graph.add_edge(START, "patient_agent")
    graph.add_conditional_edges("patient_agent", after_patient, ["rank", "escalate"])
    graph.add_conditional_edges("rank", ask_the_rung, ["department_agent", "escalate_budget", "escalate_limit"])
    graph.add_edge("department_agent", "wards_answered")
    graph.add_conditional_edges("wards_answered", after_wards,
                                ["department_agent", "propose", "escalate", "escalate_budget", "escalate_limit"])
    graph.add_conditional_edges("propose", after_propose,
                                ["code_checks", "department_agent", "escalate_budget", "escalate_limit"])
    graph.add_conditional_edges("code_checks", after_code_checks,
                                ["propose", "judge_agent", "escalate", "escalate_budget"])
    graph.add_conditional_edges("judge_agent", after_judge, ["hold", "correct", "escalate", "escalate_judge"])
    graph.add_conditional_edges("correct", after_correct,
                                ["patient_agent", "department_agent", "escalate_budget", "escalate_limit"])
    for reason in ("escalate_budget", "escalate_limit", "escalate_judge"):
        graph.add_edge(reason, "escalate")
    graph.add_edge("hold", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
