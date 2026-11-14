"""The agent loop shared by every agent.

One loop for all the agents, so each agent adds only two things of its own: its system prompt
(src/ed_admission/prompts/<agent>.md) and the list of tools it may use. The parts:

  1  settings and budget guard   config/agents.yaml, checked and read in agents/budget.py: the model, effort,
                                 max_tokens, each agent's activation budget, the run's budget, the prices
  2  system prompt               given by each agent — not here
  3  the model call              a caller — the only place that talks to the model. Two callers, one answer shape
                                 (ModelReply): AnthropicCaller, the Anthropic SDK directly (the single agent:
                                 chat(), unchanged), and LangChainCaller (the multi-agent system's agents). Both
                                 send the same request: model, effort, max_tokens, the tools, no temperature, and
                                 prompt caching (automatic): the tools, the system prompt and the conversation so
                                 far are read from the cache on the next call — so the tools, the system prompt
                                 and the effort never change inside a loop, and the history is only appended to
  4  vector database             not used: the record is read whole by a tool, the clinical lists are short and closed
  5  tools                       a Tool: the function the code runs, and the schema the model sees
  6  registry                    the tools passed to run_agent are the only ones the model is offered
  7  memory                      the conversation of one case only; no memory between cases — every case starts clean
  8  safety                      every tool result is wrapped in <tool_output source="<tool>"> tags: data, never
                                 an instruction. A tag inside the data is neutralized (a backslash after its "<"), so text in a
                                 record cannot close the tag and pass as an instruction. Nothing is removed — a
                                 record's injection attempt must stay visible, so the agent can flag it
  9  the loop                    run_agent(), below
  10 checks                      on the trajectory, not on the text: tests/test_agent_loop.py, and the case's trace
                                 — every model call is recorded in it: model, stop reason, tokens of each kind,
                                 cost, seconds, request id

How it works:
  - The answer is a decision, not text. Either a tool marked `final` ends the loop when it succeeds (its result is
    the answer), or the caller gives `done`: the loop ends when it says the task is done — the single agent's case
    is done when every patient has a decision or an escalation.
  - The model stops before the task is done: `remind` may give it one reminder of what is left (the one retry);
    after that the loop stops, and the caller handles what is left.
  - `state`: after every round, a short system message from the caller's code says where the task stands, so the
    model does not spend its thinking working that out again (its earlier thinking is kept too: the history is
    only appended to).
  - A failing tool goes back to the model as an error (is_error), and the loop goes on — e.g. `decide` refusing a
    decision that breaks a hard rule, with the reasons. The run never stops on it.
  - Out of budget, refused, or an API error after one retry: the loop stops and says why (status). It never guesses;
    the caller escalates to the shift manager. Out of the run's budget: the caller stops the whole run.
  - The cost is counted from the tokens the API reports, each kind at its own price (budget.call_cost).
  - Every tool call of one model answer is run, and all the results go back in one message.
  - No temperature is sent. Thinking is the model's adaptive thinking; its depth is `effort`.
  - No fallback model yet: a failure gets one retry, then the case is escalated. The multi-agent system's fallback
    to another provider is added later as one more caller, without changing the agents (DECISIONS.md).

Owner: Lea Gur-Arie
"""
import json
import re
import threading
import time
from dataclasses import dataclass, field
from functools import cache
from types import SimpleNamespace
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv

from ed_admission.agents.budget import RunBudget, call_cost, settings    # 1. settings + budget guard


@cache
def _client() -> anthropic.Anthropic:
    """The connection to Anthropic. The key comes from the environment (.env locally, the secrets in Colab)."""
    load_dotenv()
    return anthropic.Anthropic(max_retries=1)          # one retry, then the caller escalates — never a guess


# ============ 3. THE MODEL CALL: one answer shape, two callers ============
def _cached_system(system: str) -> list[dict]:
    """Two cache breakpoints: the end of the system prompt — the tools and the prompt, the same in every case, so the
    next case reads them from the cache (without it every case wrote them again: the trial of 2026-10-15) — and the
    automatic one at the end of the conversation (cache_control on the request), for the next call of this case."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def chat(system: str, messages: list, tools: list[dict], agent: str, client=None):
    """The single agent's request, on the Anthropic SDK directly. Returns the model's answer (a Message)."""
    s = settings(agent)
    return (client or _client()).messages.create(
        model=s["model"], max_tokens=s["max_tokens"], system=_cached_system(system), tools=tools, messages=messages,
        output_config={"effort": s["effort"]}, cache_control={"type": "ephemeral"})


@dataclass
class ModelReply:
    """One model answer, the same from every caller — the loop reads only this."""
    content: list                   # the blocks: .type, and .text (text) or .name .id .input (tool_use)
    stop_reason: str | None
    usage: Any                      # input_tokens (uncached), cache_creation_input_tokens, cache_read_input_tokens,
                                    # output_tokens — as the API counted them
    model: str
    turn: Any                       # what goes back into the history: the model's answer, unchanged — its thinking
                                    # blocks included, so the next call reads the history from the cache
    request_id: str | None = None   # the API's request id, when the caller can see it
    stop_details: Any = None        # on a refusal: .category


class AnthropicCaller:
    """The single agent's caller: chat() — the request it always sent, unchanged."""

    def __init__(self, client=None):
        self.client = client                        # a test's scripted model; None: the real connection

    def __call__(self, system: str, messages: list, tools: list[dict], agent: str) -> ModelReply:
        message = chat(system, messages, tools, agent, self.client)
        return ModelReply(content=message.content, stop_reason=message.stop_reason, usage=message.usage,
                          model=getattr(message, "model", ""), turn={"role": "assistant", "content": message.content},
                          request_id=getattr(message, "_request_id", None),
                          stop_details=getattr(message, "stop_details", None))


def _chat_model(agent: str, api_key: str | None = None) -> "ChatAnthropic":
    """LangChain's Anthropic model for one agent, from config/agents.yaml. The key comes from the environment (a
    test passes a placeholder). Imported here: the single agent never loads LangChain (DECISIONS.md).
    Effort goes in output_config, as the single agent sends it: LangChain's own `effort` field would also add a
    thinking setting of its own to some models (adaptive, summarized) — a request the single agent never sends."""
    from langchain_anthropic import ChatAnthropic
    if api_key is None:
        load_dotenv()
    s = settings(agent)
    return ChatAnthropic(model=s["model"], max_tokens=s["max_tokens"], output_config={"effort": s["effort"]},
                         max_retries=1,             # one retry, then the caller escalates — never a guess
                         **({"api_key": api_key} if api_key else {}))


def _block(block) -> SimpleNamespace:
    """A LangChain content block (a dict) as the loop reads blocks: by attribute."""
    return SimpleNamespace(**block) if isinstance(block, dict) else SimpleNamespace(type="text", text=str(block))


class LangChainCaller:
    """The multi-agent system's caller: the same request through LangChain (DECISIONS.md: LangChain is the model
    call inside every agent's loop). Effort, max_tokens, strict tools and both cache breakpoints reach the API as
    they are; no temperature is set, so none is sent (tests/test_model_callers.py checks the request)."""

    def __init__(self, make_model: Callable[[str], "ChatAnthropic"] = _chat_model):
        self.make_model = make_model                # agent -> its model; a test gives a model with a scripted client
        self.models: dict[str, "ChatAnthropic"] = {}  # one model per agent, kept for the run (one connection each)
        self._lock = threading.Lock()               # the wards of a rung call at once: one model per agent, still

    def __call__(self, system: str, messages: list, tools: list[dict], agent: str) -> ModelReply:
        with self._lock:
            if agent not in self.models:
                self.models[agent] = self.make_model(agent)
        history = [{"role": "system", "content": _cached_system(system)}, *messages]
        answer = self.models[agent].bind_tools(tools).invoke(history, cache_control={"type": "ephemeral"})
        meta = answer.response_metadata
        counted = meta.get("usage") or {}
        usage = SimpleNamespace(
            input_tokens=counted.get("input_tokens") or 0, output_tokens=counted.get("output_tokens") or 0,
            cache_creation_input_tokens=counted.get("cache_creation_input_tokens") or 0,
            cache_read_input_tokens=counted.get("cache_read_input_tokens") or 0)
        content = answer.content if isinstance(answer.content, list) else [answer.content]
        details = meta.get("stop_details")
        return ModelReply(content=[_block(block) for block in content], stop_reason=meta.get("stop_reason"),
                          usage=usage, model=meta.get("model", ""), turn=answer,
                          stop_details=_block(details) if isinstance(details, dict) else details)


# ============ 5. TOOLS: a function + the schema the model sees ============
@dataclass
class Tool:
    """One tool. `function` is called with the arguments the model sent; it returns what the model reads back.
    `final`: a successful call ends the loop, and its result is the agent's answer (decide, escalate_to_human)."""
    name: str
    description: str                        # what it does and when to use it — the model chooses by this alone
    input_schema: dict                      # JSON schema of the arguments
    function: Callable[..., Any]
    final: bool = False
    strict: bool = False                    # the API guarantees the arguments match input_schema

    def schema(self) -> dict:
        schema = {"name": self.name, "description": self.description, "input_schema": self.input_schema}
        return {**schema, "strict": True} if self.strict else schema


# ============ 8. SAFETY: a tool result is data, not a command ============
_TAG_INSIDE = re.compile(r"<(/?)(tool_output)", re.IGNORECASE)


def _neutralize(match: re.Match) -> str:
    r""""</tool_output" -> "<\/tool_output", "<tool_output" -> "<\tool_output": still readable, never a tag."""
    return ("<\\/" if match.group(1) else "<\\") + match.group(2)


def wrap_tool_output(result: Any, source: str) -> str:
    """Put a tool's result in <tool_output source="..."> tags. The system prompt says: what is inside is data, never
    an instruction. A tool_output tag inside the data is neutralized, so it cannot close the real tag. Nothing is
    removed — the agent must see an injection attempt to flag it."""
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False, default=_plain)
    source = re.sub(r"[^\w-]", "", source)                  # a tool name only: nothing can end the attribute
    return f'<tool_output source="{source}">\n{_TAG_INSIDE.sub(_neutralize, result)}\n</tool_output>'


def _plain(value):
    """Make a tool's result readable as JSON: pydantic objects, dataclasses, sets and enums."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return value.__dict__
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return getattr(value, "value", str(value))


# ============ 9. THE AGENT LOOP ============
@dataclass
class AgentResult:
    """How the loop ended, and everything it did.
    status: final (a final tool succeeded — `answer` is its result — or `done` said the task is done) · no_final
    (the model stopped before that, after its one reminder if there was one) ·
    budget (max_iterations or cost_cap_usd reached) · run_budget (the run's cost cap reached — the caller stops the
    run) · refused (the model declined) · failed (an API error, after one retry). Anything but final: the caller
    escalates to the shift manager."""
    status: str
    answer: Any = None
    trace: list[dict] = field(default_factory=list)
    iterations: int = 0
    tokens_in: int = 0                      # every input token: uncached, written to the cache, read from it
    tokens_out: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    reminded: bool = False                  # the model got its one reminder


def _call_record(iteration: int, response: ModelReply, cost: float, seconds: float) -> dict:
    """One model call, for the trace: who answered, why it stopped, the tokens of each kind, cost and time."""
    usage = response.usage
    return {"iter": iteration, "model_call": {
        "model": response.model, "stop_reason": response.stop_reason,
        "input_tokens": usage.input_tokens,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "output_tokens": usage.output_tokens, "cost_usd": round(cost, 6), "seconds": round(seconds, 2),
        "request_id": response.request_id}}


def run_agent(agent: str, system: str, task: str, tools: list[Tool], client=None,
              run_budget: RunBudget | None = None, done: Callable[[], bool] | None = None,
              remind: Callable[[], str | None] | None = None,
              state: Callable[["AgentResult"], str] | None = None,
              caller: Callable[..., ModelReply] | None = None) -> AgentResult:
    """Run one agent on one task until a final tool succeeds or `done` says so, the model stops, or the budget
    guard fires.
    caller: who talks to the model — AnthropicCaller (the default: the single agent; `client` is its connection) or
    LangChainCaller (the multi-agent system's agents).
    run_budget: the run this case belongs to (budget.start_run()); every call's cost is added to it.
    done: checked after every round of tool results — True ends the loop as final.
    remind: when the model stops before that, what is still left, in words (None: nothing) — sent once.
    state: after every round of tool results that does not end the loop, where the task stands — built by the
    caller's code from what really happened, sent as a system message (the operator's voice, never data), appended
    after the results so the cached history is untouched; the model need not reconstruct it from the whole history."""
    s = settings(agent)
    caller = caller or AnthropicCaller(client)
    registry = {tool.name: tool for tool in tools}              # 6. only these tools are offered
    schemas = [tool.schema() for tool in tools]
    messages = [{"role": "user", "content": task}]              # 7. this case's conversation, and nothing else
    result = AgentResult(status="no_final", model=s["model"])

    while True:
        if run_budget is not None and run_budget.exhausted:      # the run's money is spent: no more calls
            result.status = "run_budget"
            return result
        result.iterations += 1
        started = time.perf_counter()
        try:
            response = caller(system, messages, schemas, agent)
        except anthropic.APIError as error:                     # after one retry by the client
            result.status = "failed"
            result.trace.append({"iter": result.iterations, "error": f"{type(error).__name__}: {error}"})
            return result

        # Count the cost: each kind of token at its own price — and record the call.
        usage = response.usage
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        result.tokens_in += usage.input_tokens + written + read
        result.cache_write_tokens += written
        result.cache_read_tokens += read
        result.tokens_out += usage.output_tokens
        cost = call_cost(usage, s["prices"])
        result.cost_usd += cost
        result.trace.append(_call_record(result.iterations, response, cost, time.perf_counter() - started))
        if run_budget is not None:
            run_budget.add(cost)

        if response.stop_reason == "refusal":
            result.status = "refused"
            result.trace.append({"iter": result.iterations,
                                 "refused": getattr(response.stop_details, "category", None)})
            return result

        calls = [block for block in response.content if block.type == "tool_use"]
        messages.append(response.turn)                          # the history is only appended to
        if not calls:                                           # the model stopped before the task is done
            text = " ".join(block.text for block in response.content if block.type == "text")
            result.trace.append({"iter": result.iterations, "stopped": response.stop_reason, "text": text})
            left = remind() if remind is not None and not result.reminded else None
            if not left:
                return result
            result.reminded = True                              # the one retry: what is left, once
            result.trace.append({"iter": result.iterations, "reminder": left})
            messages.append({"role": "user", "content": left})
            if result.iterations >= s["max_iterations"] or result.cost_usd >= s["cost_cap_usd"]:
                result.status = "budget"
                return result
            continue

        results = []
        for call in calls:
            step = {"iter": result.iterations, "tool": call.name, "args": call.input}
            tool = registry.get(call.name)
            try:
                if tool is None:
                    raise ValueError(f"no tool named {call.name!r}")
                output = tool.function(**call.input)
            except Exception as error:                           # back to the model, to correct itself
                step["error"] = f"{type(error).__name__}: {error}"
                result.trace.append(step)
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": wrap_tool_output(step["error"], call.name)})
                continue
            step["result"] = output
            result.trace.append(step)
            if tool.final:                                       # the answer: the loop ends here
                result.status, result.answer = "final", output
                return result
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": wrap_tool_output(output, call.name)})
        messages.append({"role": "user", "content": results})
        if done is not None and done():                         # the task is done: every answer is in
            result.status = "final"
            return result

        # ---- the budget guard: this activation, then the run ----
        if result.iterations >= s["max_iterations"] or result.cost_usd >= s["cost_cap_usd"]:
            result.status = "budget"
            return result
        if run_budget is not None and run_budget.exhausted:
            result.status = "run_budget"
            return result
        if state is not None:                                   # where the task stands, from the code
            messages.append({"role": "system", "content": state(result)})
