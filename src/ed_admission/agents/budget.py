"""The agents' settings and the budget guard.

config/agents.yaml is checked here once, when it is first read (Pydantic): an unknown effort, a negative limit, an
agent without a budget or a model without prices stops at load — never in the middle of a run.

The budget guard has three layers (DECISIONS.md):
  activation   one agent's loop: max_iterations and cost_cap_usd — checked by run_agent after every model call.
               For the single agent one activation is one case, so it is also the per-case limit.
  case         all the agents of one case together: case_cost_cap_usd, enforced when the multi-agent system is
               built. The single agent's case cap is its activation cap (one activation = one case).
  run          every case of one script run, a cap per system (single_agent, multi_agent): RunBudget below.
               start_run(system) makes one, after checking the prices.

The cost of a call is counted from the token counts the API returns, each kind at its own price (call_cost).

Owner: Lea Gur-Arie
"""
from datetime import date
from functools import cache
from typing import Literal

System = Literal["single_agent", "multi_agent"]

from pydantic import BaseModel, ConfigDict, PositiveFloat, PositiveInt, model_validator

from ed_admission import config

MILLION = 1_000_000


# ============ THE SETTINGS FILE, CHECKED ============
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelPrices(_Strict):
    """USD per million tokens, as on Anthropic's pricing page."""
    input: PositiveFloat
    cache_write: PositiveFloat               # the 5-minute cache
    cache_read: PositiveFloat
    output: PositiveFloat


class Prices(_Strict):
    checked: date                            # when the prices were last compared with the page
    source: str
    recheck_after_days: PositiveInt
    models: dict[str, ModelPrices]


class ActivationBudget(_Strict):
    max_iterations: PositiveInt              # model calls in one activation
    cost_cap_usd: PositiveFloat


class Budget(_Strict):
    activation: dict[str, ActivationBudget]
    case_cost_cap_usd: dict[Literal["multi_agent"], PositiveFloat]   # the single agent's is its activation cap
    run_cost_cap_usd: dict[System, PositiveFloat]       # per system: the single agent, the multi-agent system


class AgentsConfig(_Strict):
    models: dict[str, str]
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens: PositiveInt
    budget: Budget
    prices: Prices

    @model_validator(mode="after")
    def _complete(self):
        """Every agent has a budget, every budget belongs to an agent, every model has prices."""
        agents, budgets = set(self.models), set(self.budget.activation)
        if agents != budgets:
            raise ValueError(f"agents without a budget: {sorted(agents - budgets)} · "
                             f"budgets without an agent: {sorted(budgets - agents)}")
        if set(self.budget.run_cost_cap_usd) != {"single_agent", "multi_agent"}:
            raise ValueError("run_cost_cap_usd needs a cap for single_agent and for multi_agent")
        if "multi_agent" not in self.budget.case_cost_cap_usd:
            raise ValueError("case_cost_cap_usd needs a cap for multi_agent")
        unpriced = set(self.models.values()) - set(self.prices.models)
        if unpriced:
            raise ValueError(f"models without prices: {sorted(unpriced)}")
        return self


@cache
def agents_config() -> AgentsConfig:
    """config/agents.yaml, checked. Read once."""
    return AgentsConfig.model_validate(config.agents())


def reload() -> None:
    """Forget the settings read so far — after config/agents.yaml was changed (the price check updates its date)."""
    config.agents.cache_clear()
    agents_config.cache_clear()


def settings(agent: str) -> dict:
    """What one agent's loop needs: its model, effort, max_tokens, its activation budget and its model's prices."""
    values = agents_config()
    model = values.models[agent]
    budget = values.budget.activation[agent]
    return {"model": model, "effort": values.effort, "max_tokens": values.max_tokens,
            "max_iterations": budget.max_iterations, "cost_cap_usd": budget.cost_cap_usd,
            "prices": values.prices.models[model]}


# ============ THE COST OF ONE CALL ============
def call_cost(usage, prices: ModelPrices) -> float:
    """USD of one model call: each kind of token the API counted, at its own price.
    input_tokens are only the uncached ones; the cached ones come in the two cache fields."""
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (usage.input_tokens * prices.input + written * prices.cache_write
            + read * prices.cache_read + usage.output_tokens * prices.output) / MILLION


# ============ THE RUN LAYER ============
class RunStopped(Exception):
    """The run's budget is spent: the script stops, keeps what was paid for, and marks the run partial. The case
    runner lets it through (a failing decider is otherwise a result, not a crash)."""


class RunBudget:
    """The cost of one script run, every case together. run_agent adds each call's cost and stops the case when the
    cap is reached; the script then stops, keeps the cases already decided and marks the run partial."""

    def __init__(self, system: System, cap_usd: float | None = None):
        self.system = system
        self.cap_usd = cap_usd if cap_usd is not None else agents_config().budget.run_cost_cap_usd[system]
        self.spent_usd = 0.0

    def add(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd


def start_run(system: System) -> RunBudget:
    """The start of every run that pays for model calls: the prices are checked first (once a month, see
    agents/prices.py) — a changed price stops the run here, before anything is spent."""
    from ed_admission.agents import prices                 # here, so that reading the settings never needs the web
    prices.ensure_current()
    return RunBudget(system)
