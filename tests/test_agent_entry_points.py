"""The single agent and the multi-agent system keep the entry point the system calls and the measurement uses: a
function `run` with the same signature as every decider. A changed signature fails here."""
import inspect

from ed_admission import config
from ed_admission.agents import single_agent


def test_the_single_agent_keeps_its_entry_point():
    signature = inspect.signature(single_agent.run)
    assert list(signature.parameters) == ["engine", "event", "waiting"]
    assert str(signature.return_annotation).replace("ed_admission.environment.decision.", "") == "list[Decision]"


def test_the_multi_agent_system_has_the_same_entry_point():
    from ed_admission.agents import multi_agent
    signature = inspect.signature(multi_agent.run)
    assert list(signature.parameters) == ["engine", "event", "waiting"]
    assert str(signature.return_annotation).replace("ed_admission.environment.decision.", "") == "list[Decision]"


def test_every_decider_file_and_every_prompt_is_known():
    """The single agent has one prompt; the multi-agent system's three agents have one each — the Patient Agent's, the
    Department Agent's and the Judge Agent's (written with Lea, 2026-11-09)."""
    files = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "agents").glob("*_agent.py")}
    prompts = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "prompts").glob("*.md")}
    assert files == {"single_agent", "multi_agent"} and prompts == {"single_agent", "patient", "department", "judge"}
