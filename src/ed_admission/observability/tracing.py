"""One trace per placement case: one step per agent call, tool call, negotiation exchange and approval —
input, output, model, prompt version, tokens, cost, time, errors — and the claims each agent makes, with the quote
from the record that backs each one.

Two charter metrics come from the traces (evaluation/metrics.py):
  complete trace   every required step is there (REQUIRED_STEPS) — target 100%
  groundedness     the share of claims whose quote really appears in the record — target ≥ 95%

Handing the traces to the measurement: a decider calls collect(trace) once a case is decided; scripts/run_metrics.py
clears the collection before a run and takes it after (take), so every decider hands its traces the same way.
A decider that collects none (the code baselines) has no trace metrics.

The cost of every case comes from its trace too: each agent call records its model calls, tokens and cost, and the
trace records how the case ended (`ended`: the agent loop's status). database/run_log.py writes one row per case to
costs.csv; `system` says which system decided — the single agent, the multi-agent system, or code.

Traces are saved as JSON lines in outputs/traces/ (not uploaded to Git); scripts/run_metrics.py saves each run's
traces as traces/<run id>.jsonl, and the admissions log points to them. A paid run also writes every trace the moment
it is collected (`listening`, used by evaluation/deciders.paid_run) to traces/journal-<decider>-<start>.jsonl, so a
run killed half way still has the record of every case it paid for.

Owner: Lea Gur-Arie
"""
import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from ed_admission import config

# The steps every case must have, in order — the milestones a decider writes (agents/multi_agent.py, single_agent.py).
REQUIRED_STEPS = ("patient_assessed", "availability_collected", "ranked", "rules_checked", "decided")
SINGLE_AGENT_ROLE = "single_agent"     # an agent call by this role means the single agent decided the case


@dataclass
class Claim:
    """One thing an agent states about the patient, and the exact words in the record that back it."""
    claim: str
    quote: str


@dataclass
class Step:
    kind: str                          # agent_call · tool_call · negotiation · approval · milestone
    actor: str                         # role or tool
    name: str = ""
    input: object = None
    output: object = None
    model: str = ""
    prompt_version: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    error: str = ""
    claims: list[Claim] = field(default_factory=list)
    model_calls: int = 0               # an agent call: how many times its loop called the model
    at: float = field(default_factory=time.time)


@dataclass
class Trace:
    case_id: str
    decider: str
    steps: list[Step] = field(default_factory=list)
    ended: str = ""                    # how the case ended: final · budget · run_budget · refused · failed · no_final
                                       # ("" for a decider without a model)

    def agent_call(self, role: str, input, output, *, model: str, prompt_version: str, tokens_in: int = 0,
                   tokens_out: int = 0, cost_usd: float = 0.0, seconds: float = 0.0, claims: list[Claim] = (),
                   error: str = "", model_calls: int = 0) -> None:
        self.steps.append(Step("agent_call", role, role, input, output, model, prompt_version, tokens_in, tokens_out,
                               cost_usd, seconds, error, list(claims), model_calls))

    def tool_call(self, role: str, tool: str, input, output, seconds: float, error: str = "",
                  claims: list[Claim] = ()) -> None:
        """`claims`: a tool that carries an agent's findings (the single agent's record_assessment)."""
        self.steps.append(Step("tool_call", role, tool, input, output, seconds=seconds, error=error,
                               claims=list(claims)))

    def milestone(self, name: str, output=None) -> None:
        """A step of the placement case — e.g. patient_assessed, ranked, decided."""
        self.steps.append(Step("milestone", "case", name, output=output))

    @property
    def complete(self) -> bool:
        reached = {s.name for s in self.steps if s.kind == "milestone"}
        return all(step in reached for step in REQUIRED_STEPS) and not any(s.error for s in self.steps)

    @property
    def claims(self) -> list[Claim]:
        return [c for s in self.steps for c in s.claims]

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def _agent_calls(self) -> list[Step]:
        return [s for s in self.steps if s.kind == "agent_call"]

    @property
    def model_calls(self) -> int:
        return sum(s.model_calls for s in self._agent_calls)

    @property
    def tokens_in(self) -> int:
        return sum(s.tokens_in for s in self._agent_calls)

    @property
    def tokens_out(self) -> int:
        return sum(s.tokens_out for s in self._agent_calls)

    @property
    def cost_by_role(self) -> dict[str, float]:
        """The case's cost per agent — in the multi-agent system, which agent cost what."""
        costs: dict[str, float] = {}
        for step in self._agent_calls:
            costs[step.actor] = costs.get(step.actor, 0.0) + step.cost_usd
        return costs

    @property
    def system(self) -> str:
        """single_agent · multi_agent · code (no agent call — a code decider)."""
        roles = {s.actor for s in self._agent_calls}
        if SINGLE_AGENT_ROLE in roles:
            return "single_agent"
        return "multi_agent" if roles else "code"

    def save(self, folder=None, name: str | None = None) -> None:
        """Append to <folder>/<name>.jsonl — by default outputs/traces/<decider>.jsonl."""
        folder = folder or config.outputs_dir() / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{name or self.decider}.jsonl").open("a", encoding="utf8") as file:
            file.write(json.dumps(asdict(self), ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------- handing traces to the measurement

_COLLECTED: list[Trace] = []
_LISTENERS: list = []           # called with every trace as it is collected — e.g. the paid-run journal


def collect(trace: Trace) -> None:
    """Called by a decider once a case is decided — the multi-agent system's graph, the single agent, or the example pipeline."""
    _COLLECTED.append(trace)
    for listener in list(_LISTENERS):
        listener(trace)


@contextmanager
def listening(listener):
    """While inside: `listener(trace)` runs on every trace as soon as it is collected."""
    _LISTENERS.append(listener)
    try:
        yield
    finally:
        _LISTENERS.remove(listener)


def take() -> list[Trace]:
    """The traces collected since the last take, and a clean collection for the next run."""
    taken = list(_COLLECTED)
    _COLLECTED.clear()
    return taken
