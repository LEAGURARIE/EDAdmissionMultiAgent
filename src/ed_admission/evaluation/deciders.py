"""The deciders the run scripts know by name — and any other one, given as an import path.

    --decider rules_only                                   a name from DECIDERS
    --decider ed_admission.agents.my_pipeline:decide       module:function — a team member's own decider,
                                                           with no change to the scripts

Owner: integrator
"""
import importlib

from ed_admission.baselines import first_free_bed, rules_only, simulated_coordinator
from ed_admission.orchestration.example_pipeline import example_pipeline

DECIDERS = {"first_free_bed": first_free_bed, "rules_only": rules_only,
            "simulated_coordinator": simulated_coordinator, "example_pipeline": example_pipeline}


def resolve(name: str):
    """A decider by its name in DECIDERS, or by 'module:function'."""
    if name in DECIDERS:
        return DECIDERS[name]
    if ":" not in name:
        raise ValueError(f"unknown decider {name!r}: use one of {sorted(DECIDERS)} or module:function")
    module, function = name.split(":", 1)
    return getattr(importlib.import_module(module), function)


def label(name: str) -> str:
    """A short name for output files: the function's name."""
    return name.rsplit(":", 1)[-1]
