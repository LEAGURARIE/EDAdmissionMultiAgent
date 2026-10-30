"""Every agent file keeps the entry point the system calls and the measurement uses: a function `run` with a fixed
signature. A changed signature fails here. The code examples keep the same signatures."""
import importlib
import inspect

import pytest

from ed_admission.orchestration import example_agents

# agent module -> (parameter names, return type name)
ENTRY_POINTS = {
    "patient_agent": (["toolbox", "patient_id"], "PatientAssessment"),
    "department_agent": (["view", "request"], "WardAnswer"),
    "coordinator_agent": (["engine", "event", "waiting"], "list[Decision]"),
    "single_agent": (["engine", "event", "waiting"], "list[Decision]"),       # the comparison: one agent, all of it
}
EXAMPLES = {"patient_agent": "patient", "department_agent": "department"}


def _return_name(function) -> str:
    annotation = inspect.signature(function).return_annotation
    if hasattr(annotation, "__args__"):                     # list[Decision]
        return str(annotation).replace("ed_admission.environment.decision.", "")
    return annotation.__name__


@pytest.mark.parametrize("module", list(ENTRY_POINTS))
def test_every_agent_keeps_its_entry_point(module):
    agent = importlib.import_module(f"ed_admission.agents.{module}")
    parameters, returns = ENTRY_POINTS[module]
    assert list(inspect.signature(agent.run).parameters) == parameters
    assert _return_name(agent.run) == returns


@pytest.mark.parametrize("module", list(EXAMPLES))
def test_the_code_examples_have_the_same_signatures(module):
    example = getattr(example_agents, EXAMPLES[module])
    parameters, returns = ENTRY_POINTS[module]
    assert list(inspect.signature(example).parameters) == parameters
    assert _return_name(example) == returns


def test_two_agents_a_coordinator_and_the_single_agent_and_nothing_else():
    from ed_admission import config
    files = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "agents").glob("*_agent.py")}
    prompts = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "prompts").glob("*_agent.md")}
    assert files == prompts == set(ENTRY_POINTS)
