"""The deciders the run scripts know by name — and any other one, given as an import path.

    --decider code_coordinator                             a name from DECIDERS (single_agent too)
    --decider <module>:<function>                          any other decider,
                                                           with no change to the scripts

A decider that calls a model is paid for (its module has start_run — the single agent, the multi-agent system). Every
run script asks for --paid before it runs one — only after its estimated cost was approved — and runs it inside
paid_run(), which opens the run's budget (the prices are checked first) and closes it.

Owner: Lea Gur-Arie
"""
import importlib
from contextlib import contextmanager
from datetime import datetime

from ed_admission import config
from ed_admission.observability import tracing

from ed_admission.agents import multi_agent, single_agent
from ed_admission.baselines import code_coordinator

# single_agent and multi_agent call models and pay for them: the run scripts open their run budget first (start_run),
# and multi_agent's start_run is also where its real agents are set.
DECIDERS = {"code_coordinator": code_coordinator, "single_agent": single_agent.run, "multi_agent": multi_agent.run}


class PaidWithoutApproval(Exception):
    """A paid decider was asked for without --paid."""


def resolve(name: str, paid: bool = False):
    """A decider by its name in DECIDERS, or by 'module:function'. A decider that calls a model only with paid=True."""
    if name in DECIDERS:
        decider = DECIDERS[name]
    elif ":" not in name:
        raise ValueError(f"unknown decider {name!r}: use one of {sorted(DECIDERS)} or module:function")
    else:
        module, function = name.split(":", 1)
        decider = getattr(importlib.import_module(module), function)
    if is_paid(decider) and not paid:
        raise PaidWithoutApproval(f"{name} calls a model and every case is paid for. Run it only after its "
                                  "estimated cost was approved, with --paid.")
    return decider


def is_paid(decider) -> bool:
    return hasattr(importlib.import_module(decider.__module__), "start_run")


@contextmanager
def paid_run(decider):
    """Around a run: a paid decider's run budget is opened (the prices checked) and closed, and every case it pays for
    is written to the journal the moment it ends (journal); any other decider, nothing."""
    module = importlib.import_module(decider.__module__) if decider is not None else None
    if not hasattr(module, "start_run"):
        yield
        return
    module.start_run()
    try:
        with tracing.listening(journal(decider.__module__.rsplit(".", 1)[-1])):
            yield
    finally:
        module.end_run()


def journal(name: str):
    """A listener that appends each paid case's trace — its cost, model calls and how it ended — to
    outputs/traces/journal-<name>-<start>.jsonl as soon as the case ends. The run's own files (runs.csv, costs.csv)
    are written at its end; the journal is what is left when a run is killed before that."""
    started = datetime.now()

    def write(trace) -> None:
        trace.save(config.outputs_dir() / "traces", name=f"journal-{name}-{started:%Y%m%d-%H%M%S}")
    return write


def label(name: str) -> str:
    """A short name for output files: the function's name."""
    return name.rsplit(":", 1)[-1]
