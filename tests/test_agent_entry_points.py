"""Every agent file keeps the entry point the Coordinator Agent calls and the measurement uses: a function `run` with
a fixed signature (and, for the Supervision Agent, `run_back`). A changed signature fails here, in the owner's
tests, before the code reaches the integrator. The code examples keep the same signatures."""
import importlib
import inspect

import pytest

from ed_admission.orchestration import example_agents

# agent module -> function -> (parameter names, return type name)
ENTRY_POINTS = {
    "clinical_problem_agent": {"run": (["toolbox", "patient_id"], "ClinicalProblemOutput")},
    "severity_agent": {"run": (["toolbox", "patient_id"], "SeverityOutput")},
    "supervision_agent": {"run": (["toolbox", "patient_id"], "SupervisionOutput"),
                          "run_back": (["toolbox", "patient_id", "offers"], "BackToSupervisionAnswer")},
    "department_agent": {"run": (["view", "request"], "WardAnswer")},
    "safety_critic_agent": {"run": (["toolbox", "patient_id", "decision"], "SafetyVerdict")},
    "coordinator_agent": {"run": (["engine", "event", "waiting"], "list[Decision]")},
}
EXAMPLES = {("clinical_problem_agent", "run"): "clinical_problem", ("severity_agent", "run"): "severity",
            ("supervision_agent", "run"): "supervision", ("supervision_agent", "run_back"): "supervision_back",
            ("department_agent", "run"): "department", ("safety_critic_agent", "run"): "safety_critic"}


def _return_name(function) -> str:
    annotation = inspect.signature(function).return_annotation
    if hasattr(annotation, "__args__"):                     # list[Decision]
        return str(annotation).replace("ed_admission.environment.decision.", "")
    return annotation.__name__


@pytest.mark.parametrize("module, function", [(m, f) for m, fs in ENTRY_POINTS.items() for f in fs])
def test_every_agent_keeps_its_entry_point(module, function):
    agent = importlib.import_module(f"ed_admission.agents.{module}")
    assert hasattr(agent, function), f"{module} must define {function}"
    parameters, returns = ENTRY_POINTS[module][function]
    assert list(inspect.signature(getattr(agent, function)).parameters) == parameters
    assert _return_name(getattr(agent, function)) == returns


@pytest.mark.parametrize("module, function", list(EXAMPLES))
def test_the_code_examples_have_the_same_signatures(module, function):
    example = getattr(example_agents, EXAMPLES[(module, function)])
    parameters, returns = ENTRY_POINTS[module][function]
    assert list(inspect.signature(example).parameters) == parameters
    assert _return_name(example) == returns
