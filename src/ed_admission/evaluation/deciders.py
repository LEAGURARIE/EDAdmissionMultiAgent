"""The deciders the run scripts know by name — and any other one, given as an import path.

    --decider code_coordinator                             a name from DECIDERS (single_agent too)
    --decider ed_admission.agents.coordinator_agent:run    module:function — any other decider,
                                                           with no change to the scripts

A decider that calls a model is paid for (its module has start_run — the single agent). Every run script asks for
--paid before it runs one — only after its estimated cost was approved — and runs it inside paid_run(), which opens
the run's budget (the prices are checked first) and closes it.

Owner: Lea Gur-Arie
"""
import importlib
from contextlib import contextmanager

from ed_admission.agents import single_agent
from ed_admission.baselines import code_coordinator
from ed_admission.orchestration.example_pipeline import example_pipeline

# single_agent calls the model and pays for it: the run scripts open its run budget first (start_run).
DECIDERS = {"code_coordinator": code_coordinator, "example_pipeline": example_pipeline,
            "single_agent": single_agent.run}


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
    """Around a run: a paid decider's run budget is opened (the prices checked) and closed; any other, nothing."""
    module = importlib.import_module(decider.__module__) if decider is not None else None
    if not hasattr(module, "start_run"):
        yield
        return
    module.start_run()
    try:
        yield
    finally:
        module.end_run()


def label(name: str) -> str:
    """A short name for output files: the function's name."""
    return name.rsplit(":", 1)[-1]
