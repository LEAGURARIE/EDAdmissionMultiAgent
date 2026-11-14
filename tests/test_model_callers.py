"""The shared model call (agents/agent_loop.py): two callers, one answer shape — and the price of a long prompt.
Every model here is scripted: no call leaves the computer, nothing is paid. The LangChain caller runs LangChain's real
code end to end; only the connection under it is scripted, so the request it would send is checked as sent."""
import subprocess
import sys

import anthropic
import pytest

from ed_admission.agents.agent_loop import LangChainCaller, Tool, _chat_model, run_agent, settings
from ed_admission.agents.budget import ModelPrices, agents_config, call_cost
from ed_admission.agents import prices
from types import SimpleNamespace as N

SCHEMA = {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"],
          "additionalProperties": False}
MULTI_AGENT = ("patient", "department", "judge")
SINGLE_AGENT_REQUEST = {"model", "max_tokens", "system", "tools", "messages", "output_config", "cache_control"}


def tools():
    return [Tool("read_patient_record", "Read the record.", SCHEMA, lambda patient_id: {"case_id": patient_id},
                 strict=True),
            Tool("decide", "Hand in the decision.", SCHEMA, lambda patient_id: f"decided {patient_id}", final=True,
                 strict=True)]


def message(*blocks, stop="tool_use", model="claude-haiku-5-5", tokens_in=100, written=0, read=0, details=None):
    """A real Anthropic answer, as the API returns it."""
    return anthropic.types.Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": list(blocks),
        "stop_reason": stop, "stop_sequence": None, "stop_details": details,
        "usage": {"input_tokens": tokens_in, "output_tokens": 50, "cache_creation_input_tokens": written,
                  "cache_read_input_tokens": read}})


def use(name, tool_id="t1", **args):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}


class ScriptedConnection:
    """Stands in for the Anthropic connection under LangChain: keeps every request, returns the answers in order."""
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []
        self.messages = self.with_raw_response = self

    def create(self, **request):
        self.requests.append(request)
        reply = self.answers.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return N(parse=lambda: reply)


def langchain_caller(connection):
    """The real LangChainCaller and the real ChatAnthropic, from config/agents.yaml — on a scripted connection."""
    def make_model(agent):
        model = _chat_model(agent, api_key="test-only")
        model.__dict__["_client"] = connection
        return model
    return LangChainCaller(make_model)


# ============ the single agent: its request, unchanged ============
class ScriptedClient:
    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []
        self.messages = self

    def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})   # as sent, before the loop adds to it
        return self.answers.pop(0)


def test_the_single_agents_request_is_the_one_it_always_sent():
    client = ScriptedClient(message(use("decide", patient_id="P-001"), model="claude-opus-5-5"))
    run_agent("single_agent", "the prompt", "Place P-001", tools(), client=client)
    s = settings("single_agent")
    request = client.requests[0]
    assert set(request) == SINGLE_AGENT_REQUEST
    assert request["model"] == s["model"] and request["max_tokens"] == s["max_tokens"]
    assert request["output_config"] == {"effort": s["effort"]} and request["cache_control"] == {"type": "ephemeral"}
    assert request["system"] == [{"type": "text", "text": "the prompt", "cache_control": {"type": "ephemeral"}}]
    assert request["messages"] == [{"role": "user", "content": "Place P-001"}]


def test_the_single_agent_never_loads_langchain():
    code = ("import sys; from ed_admission.agents import single_agent; "
            "print('langchain_anthropic' in sys.modules, 'langchain_core' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["False", "False"]


# ============ the multi-agent system: the same request through LangChain ============
@pytest.mark.parametrize("agent", MULTI_AGENT)
def test_effort_and_the_model_reach_the_api_and_no_temperature_is_sent(agent):
    connection = ScriptedConnection(message(use("decide", patient_id="P-001")))
    run_agent(agent, "the prompt", "Place P-001", tools(), caller=langchain_caller(connection))
    s = settings(agent)
    request = connection.requests[0]
    assert request["model"] == s["model"] and request["max_tokens"] == s["max_tokens"]
    assert request["output_config"] == {"effort": s["effort"]}       # Haiku's own default is medium: sent explicitly
    assert set(request) == SINGLE_AGENT_REQUEST                       # no temperature, no thinking setting of its own
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["system"] == [{"type": "text", "text": "the prompt", "cache_control": {"type": "ephemeral"}}]
    assert [tool["name"] for tool in request["tools"]] == ["read_patient_record", "decide"]
    assert all(tool["strict"] is True and tool["input_schema"] == SCHEMA for tool in request["tools"])


def test_both_callers_give_the_loop_the_same_trajectory_and_the_same_record():
    answers = [message(THINKING, use("read_patient_record", patient_id="P-001"), written=4000),
               message(THINKING, use("decide", tool_id="t2", patient_id="P-001"), read=4000)]
    by_anthropic = run_agent("department", "p", "Place P-001", tools(), client=ScriptedClient(*answers))
    by_langchain = run_agent("department", "p", "Place P-001", tools(),
                             caller=langchain_caller(ScriptedConnection(*answers)))
    for result in (by_anthropic, by_langchain):
        assert result.status == "final" and result.answer == "decided P-001" and result.iterations == 2
    assert by_langchain.cost_usd == pytest.approx(by_anthropic.cost_usd)
    assert (by_langchain.cache_write_tokens, by_langchain.cache_read_tokens) == (4000, 4000)
    calls = [[{k: v for k, v in step["model_call"].items() if k not in ("seconds", "request_id")}
              for step in result.trace if "model_call" in step] for result in (by_anthropic, by_langchain)]
    assert calls[0] == calls[1]


def test_the_history_goes_back_whole_thinking_and_tagged_results_and_the_state_message():
    connection = ScriptedConnection(message(THINKING, use("read_patient_record", patient_id="P-001")),
                                    message(use("decide", tool_id="t2", patient_id="P-001")))
    run_agent("patient", "p", "Assess P-001", tools(), caller=langchain_caller(connection),
              state=lambda result: "one patient left")
    sent = connection.requests[1]["messages"]
    assert [turn["role"] for turn in sent] == ["user", "assistant", "user", "system"]
    assert sent[1]["content"][0] == THINKING                         # its signature kept: the cache still reads it
    assert sent[2]["content"][0]["content"].startswith('<tool_output source="read_patient_record">')
    assert sent[3]["content"] == "one patient left"


def test_a_refusal_through_langchain_stops_the_loop_with_its_category():
    refused = message({"type": "text", "text": ""}, stop="refusal",
                      details={"type": "refusal", "category": "cyber", "explanation": None})
    result = run_agent("judge", "p", "Check", tools(), caller=langchain_caller(ScriptedConnection(refused)))
    assert result.status == "refused" and result.trace[-1]["refused"] == "cyber"


def test_an_api_error_through_langchain_fails_the_loop_never_a_guess():
    request = anthropic.DefaultHttpxClient().build_request("POST", "https://api.anthropic.com/v1/messages")
    down = anthropic.APIConnectionError(message="down", request=request)
    result = run_agent("department", "p", "Place", tools(), caller=langchain_caller(ScriptedConnection(down)))
    assert result.status == "failed" and "error" in result.trace[-1]


def test_one_model_per_agent_is_kept_for_the_run():
    made = []
    caller = langchain_caller(ScriptedConnection(*[message(use("decide", patient_id="P-001"))] * 3))
    make = caller.make_model
    caller.make_model = lambda agent: made.append(agent) or make(agent)
    for agent in ("department", "department", "judge"):
        run_agent(agent, "p", "Place", tools(), caller=caller)
    assert made == ["department", "judge"]


# ============ the price of a long prompt ============
def usage(uncached, written=0, read=0, out=1000):
    return N(input_tokens=uncached, cache_creation_input_tokens=written, cache_read_input_tokens=read,
             output_tokens=out)


def test_haiku_over_100000_prompt_tokens_pays_the_long_prompt_prices_for_the_whole_call():
    haiku = agents_config().prices.models["claude-haiku-5-5"]
    long = haiku.long_prompt
    at_the_line = usage(60_000, written=20_000, read=20_000)          # 100,000 exactly: the standard prices
    over = usage(60_000, written=20_000, read=20_001)                 # one cached token more: all at the long prices
    assert call_cost(at_the_line, haiku) == pytest.approx(
        (60_000 * haiku.input + 20_000 * haiku.cache_write + 20_000 * haiku.cache_read + 1000 * haiku.output) / 1e6)
    assert call_cost(over, haiku) == pytest.approx(
        (60_000 * long.input + 20_000 * long.cache_write + 20_001 * long.cache_read + 1000 * long.output) / 1e6)
    assert long.above_tokens == 100_000 and long.input == 5 * haiku.input and long.output == 5 * haiku.output


def test_opus_and_sonnet_have_one_price_at_any_prompt_length():
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        price = agents_config().prices.models[model]
        assert price.long_prompt is None
        assert call_cost(usage(900_000), price) == pytest.approx((900_000 * price.input + 1000 * price.output) / 1e6)


PAGE = """
| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |
| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok | $20 / MTok |
"""


def test_the_price_check_reads_both_rows_of_a_model_priced_by_prompt_length():
    haiku = prices.page_prices(PAGE, "claude-haiku-5-5")
    assert (haiku.input, haiku.output) == (0.10, 0.50)
    assert haiku.long_prompt.above_tokens == 100_000 and (haiku.long_prompt.input, haiku.long_prompt.output) == (0.5, 2.5)
    assert prices.page_prices(PAGE, "claude-opus-5-5").long_prompt is None


def test_the_price_check_sees_a_change_in_the_long_prompt_prices():
    ours = agents_config().prices.models["claude-haiku-5-5"]
    page = PAGE.replace("$2.50 / MTok", "$3 / MTok")
    changes = prices._differences("claude-haiku-5-5", ours, prices.page_prices(page, "claude-haiku-5-5"))
    assert changes == [("claude-haiku-5-5", "long.output", 2.5, 3.0)]
    assert prices._differences("claude-haiku-5-5", ours, prices.page_prices(PAGE, "claude-haiku-5-5")) == []
    assert isinstance(ours, ModelPrices)
