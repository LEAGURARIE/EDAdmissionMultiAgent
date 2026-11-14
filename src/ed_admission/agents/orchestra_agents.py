"""The multi-agent system's three real agents — each one the shared agent loop with its own prompt and tools.

    build(engine, trace, run_budget, caller) -> Agents

The graph (orchestration/graph.py) calls each agent with its task and gets back (its answer, its cost). One run of an
agent here:
  1  a toolbox with the agent's role — patient · department, bound to the one ward of the run · judge — so the
     permissions stay in code (tools/toolbox.py)
  2  its tools (agents/orchestra_tools.py) and its prompt (prompts/<agent>.md), with the prompt's version — the first
     10 characters of its fingerprint, so every answer says which prompt made it
  3  the task, in words: only what that agent may know (task_text_*, below). No hint, no expected answer; the
     Department Agent gets no patient identifier, the Judge Agent no record. The Judge's correction comes inside
     <judge_correction> tags, a tag inside it neutralized — data, never an order
  4  the loop (agent_loop.run_agent) through LangChain (LangChainCaller), the model of that agent from
     config/agents.yaml; every model call's cost is added to the run's budget by the loop itself
  5  the trace: one entry per agent run — its model, prompt version, model calls, tokens, cost, how it ended, and the
     Patient Agent's claims with their quotes

How an agent run ends:
  final       its closing tool succeeded: the answer goes to the handoff check in code
  run_budget  the run's money is spent: RunStopped — the run stops, the cases already decided are kept
  otherwise   (no_final · refused · failed · budget) an empty answer: the handoff check fails, the graph tries
              once more and then escalates — one retry, then the shift manager; never a guess

Owner: Lea Gur-Arie
"""
import hashlib
import re
import time
from functools import cache
from pathlib import Path

from ed_admission.agents.agent_loop import AgentResult, LangChainCaller, run_agent
from ed_admission.agents.budget import RunBudget, RunStopped
from ed_admission.agents.orchestra_tools import department_tools, judge_tools, patient_tools
from ed_admission.contracts.agent_outputs import PatientTask, WardTask
from ed_admission.environment.events import to_clock
from ed_admission.observability.tracing import Claim, Trace
from ed_admission.orchestration.graph import Agents
from ed_admission.tools.toolbox import Toolbox

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_OF = {"patient": "patient.md", "department": "department.md", "judge": "judge.md"}
CLOSING_TOOL = {"patient": "record_assessment", "department": "answer_ward", "judge": "give_verdict"}
_CORRECTION_TAG = re.compile(r"<(/?)(judge_correction)", re.IGNORECASE)


@cache
def system_prompt(agent: str) -> tuple[str, str]:
    """An agent's system prompt and its version: the first 10 characters of its fingerprint."""
    text = (PROMPTS / PROMPT_OF[agent]).read_text(encoding="utf8")
    return text, hashlib.sha256(text.encode("utf8")).hexdigest()[:10]


# ============ THE TASKS, IN WORDS ============
def _correction(feedback: str | None) -> list[str]:
    """The Judge's correction, as data in its own tags; a tag inside it can never close them."""
    if not feedback:
        return []
    safe = _CORRECTION_TAG.sub(lambda m: ("<\\/" if m.group(1) else "<\\") + m.group(2), feedback)
    return [f"<judge_correction>\n{safe}\n</judge_correction>"]


def task_text_patient(task: PatientTask) -> str:
    return "\n".join([f"The patient: {task.patient_id}. The admission was decided at {to_clock(task.decided_at)}.",
                      *_correction(task.judge_feedback)])


def task_text_department(task: WardTask) -> str:
    """The needs only: no patient identifier, no record."""
    request, yes = task.request, {True: "yes", False: "no"}
    whose = ("your ward" if request.target_ward == task.ward_id
             else f"{request.target_ward} — a temporary bed is asked of you")
    lines = [f"Your ward: {task.ward_id}.",
             f"The request: a {request.bed_type} bed. Severe: {yes[request.severe]}. "
             f"Observation: {yes[request.observation]}. Isolation: {yes[request.isolation]}.",
             f"The patient's target ward: {whose}."]
    if request.proposed_bed is not None:
        lines.append(f"A bed the Coordinator saw free in your report: {request.proposed_bed}.")
    if task.patient_answer is not None:
        lines.append(f"The answer to your question — stable: {yes[task.patient_answer]}.")
    return "\n".join([*lines, *_correction(task.judge_feedback)])


def task_text_judge() -> str:
    return "A placement is proposed. Read the case file, then give your verdict on items 2, 8 and 9."


# ============ ONE AGENT RUN ============
def _claims(answer) -> list[Claim]:
    findings = getattr(answer, "findings", None) or []
    return [Claim(f.claim, f.quote) for f in findings]


def _run(agent: str, task_text: str, tools, trace: Trace, run_budget: RunBudget | None, caller,
         task_for_trace) -> tuple[object, float]:
    prompt, version = system_prompt(agent)
    started = time.perf_counter()
    result: AgentResult = run_agent(agent, prompt, task_text, tools, run_budget=run_budget, caller=caller,
                                    remind=lambda: f"Hand in your answer with {CLOSING_TOOL[agent]}.")
    answer = result.answer if result.status == "final" else {}       # an empty answer fails the handoff check
    trace.agent_call(agent, task_for_trace, {"status": result.status, "loop": result.trace},
                     model=result.model, prompt_version=version, tokens_in=result.tokens_in,
                     tokens_out=result.tokens_out, cost_usd=result.cost_usd,
                     seconds=time.perf_counter() - started, model_calls=result.iterations,
                     claims=_claims(answer) if agent == "patient" else [],
                     error=f"the model could not be reached: {result.trace[-1].get('error', '')}"
                     if result.status == "failed" else "")
    if result.status == "run_budget":
        raise RunStopped("the run's budget was reached during this case")
    return answer, result.cost_usd


def build(engine, trace: Trace, run_budget: RunBudget | None = None, caller=None) -> Agents:
    """The three real agents for one case. `caller`: one LangChainCaller for the whole run (one model per agent)."""
    caller = caller or LangChainCaller()

    def patient(task: PatientTask):
        toolbox = Toolbox("patient", engine, trace=trace)
        return _run("patient", task_text_patient(task), patient_tools(toolbox, task.patient_id), trace,
                    run_budget, caller, task)

    def department(task: WardTask):
        toolbox = Toolbox("department", engine, ward_id=task.ward_id, trace=trace)
        return _run("department", task_text_department(task), department_tools(toolbox), trace, run_budget,
                    caller, task)

    def judge(state):
        toolbox = Toolbox("judge", engine, trace=trace)
        return _run("judge", task_text_judge(), judge_tools(toolbox, state), trace, run_budget, caller,
                    "the case file")

    return Agents(patient=patient, department=department, judge=judge)
