"""One trace per placement case: one step per agent call, tool call, negotiation exchange and approval —
input, output, model, prompt version, tokens, cost, time, errors — and the claims each agent makes, with the quote
from the record that backs each one.

Two charter metrics come from the traces (evaluation/metrics.py):
  complete trace   every required step is there (REQUIRED_STEPS) — target 100%
  groundedness     the share of claims whose quote really appears in the record — target ≥ 95%

Handing the traces to the measurement: a decider calls collect(trace) once a case is decided; scripts/run_metrics.py
clears the collection before a run and takes it after (take), so every decider hands its traces the same way.
A decider that collects none (the code baselines) has no trace metrics.

Traces are saved as JSON lines in outputs/traces/ (not uploaded to Git); scripts/run_metrics.py saves each run's
traces as traces/<run id>.jsonl, and the admissions log points to them.

Owner: integrator
"""
import json
import time
from dataclasses import asdict, dataclass, field

from ed_admission import config

# The steps every case must have, in the order of the placement case (orchestration/placement_case.py).
REQUIRED_STEPS = ("patient_assessed", "availability_collected", "ranked", "critic_reviewed", "decided")


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
    at: float = field(default_factory=time.time)


@dataclass
class Trace:
    case_id: str
    decider: str
    steps: list[Step] = field(default_factory=list)

    def agent_call(self, role: str, input, output, *, model: str, prompt_version: str, tokens_in: int = 0,
                   tokens_out: int = 0, cost_usd: float = 0.0, seconds: float = 0.0, claims: list[Claim] = (),
                   error: str = "") -> None:
        self.steps.append(Step("agent_call", role, role, input, output, model, prompt_version, tokens_in, tokens_out,
                               cost_usd, seconds, error, list(claims)))

    def tool_call(self, role: str, tool: str, input, output, seconds: float, error: str = "") -> None:
        self.steps.append(Step("tool_call", role, tool, input, output, seconds=seconds, error=error))

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

    def save(self, folder=None, name: str | None = None) -> None:
        """Append to <folder>/<name>.jsonl — by default outputs/traces/<decider>.jsonl."""
        folder = folder or config.outputs_dir() / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{name or self.decider}.jsonl").open("a", encoding="utf8") as file:
            file.write(json.dumps(asdict(self), ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------- handing traces to the measurement

_COLLECTED: list[Trace] = []


def collect(trace: Trace) -> None:
    """Called by a decider once a case is decided — the Coordinator Agent's `run`, or the example pipeline."""
    _COLLECTED.append(trace)


def take() -> list[Trace]:
    """The traces collected since the last take, and a clean collection for the next run."""
    taken = list(_COLLECTED)
    _COLLECTED.clear()
    return taken
