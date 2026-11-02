"""The single agent keeps the entry point the system calls and the measurement uses: a function `run` with the same
signature as every decider. A changed signature fails here. Every agent file has its prompt file."""
import inspect

from ed_admission import config
from ed_admission.agents import single_agent


def test_the_single_agent_keeps_its_entry_point():
    signature = inspect.signature(single_agent.run)
    assert list(signature.parameters) == ["engine", "event", "waiting"]
    assert str(signature.return_annotation).replace("ed_admission.environment.decision.", "") == "list[Decision]"


def test_every_agent_file_has_its_prompt_and_nothing_else():
    files = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "agents").glob("*_agent.py")}
    prompts = {p.stem for p in (config.PROJECT / "src" / "ed_admission" / "prompts").glob("*.md")}
    assert files == prompts == {"single_agent"}
