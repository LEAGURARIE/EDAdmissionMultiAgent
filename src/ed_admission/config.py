"""One place that reads the configuration files.

config/hospital.yaml          what exists: wards and beds, and how long cleaning, holds and approvals take
config/agents.yaml            the agents' models, effort, the free-text limits, the budget guard and the prices
config/judge_rubric.yaml      the Judge's rubric: what a proposed placement is checked on
config/clinical_rules/*.yaml  what is right to do: ladders, complaint -> ward, NEWS2 levels, placement rules,
                              severity judgments

Where results go: outputs_dir() — outputs/ in the project, or the folder in the ED_OUTPUTS environment variable
(in Colab, a Google Drive folder, so the history outlives the session).

Every value lives in exactly one file; code reads it from here and never writes it a second time.
Each file is read once and kept (cache).

Owner: Lea Gur-Arie
"""
import os
from functools import cache
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[2]
HOSPITAL_FILE = PROJECT / "config" / "hospital.yaml"
RULES = PROJECT / "config" / "clinical_rules"


def outputs_dir() -> Path:
    """Where every run writes its results: ED_OUTPUTS if set, else outputs/ in the project (not uploaded to Git)."""
    folder = Path(os.environ.get("ED_OUTPUTS") or PROJECT / "outputs")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf8"))


@cache
def agents() -> dict:
    """The agents' settings: the model of each agent, effort, max_tokens, the budget guard and the prices."""
    return _read(PROJECT / "config" / "agents.yaml")


def text_limits() -> dict[str, int]:
    """The longest free text of each field that passes between parts of the multi-agent system, in characters
    (config/agents.yaml; checked when loaded, agents/budget.py)."""
    return agents()["text_limits"]


@cache
def judge_rubric() -> dict[int, dict]:
    """The Judge's rubric: item number -> name, text, blocking, checked_by (code or judge)."""
    return {int(number): item for number, item in _read(PROJECT / "config" / "judge_rubric.yaml")["items"].items()}


@cache
def hospital() -> dict:
    """The whole hospital file: operations and wards."""
    return _read(HOSPITAL_FILE)


@cache
def case_desk() -> dict:
    """How the scripted days hand cases to the decider: concurrent_cases (1, a number, or None = no limit) and
    hold_bed_when (close or start)."""
    values = hospital()
    return {"concurrent_cases": values["concurrent_cases"], "hold_bed_when": values["hold_bed_when"]}


@cache
def operations() -> dict:
    """Cleaning, hold and approval times, in simulated minutes."""
    return hospital()["operations"]


@cache
def ladders() -> dict[str, list[list[str]]]:
    """For each target ward, the ladder levels from closest (level 1 = the ward itself) to farthest."""
    return _read(RULES / "proximity_ladder.yaml")["ladders"]


@cache
def observation_conditions() -> list[dict]:
    """The conditions that require an observation bed (approved list)."""
    return _read(RULES / "observation_conditions.yaml")["conditions"]


@cache
def isolation_conditions() -> dict:
    """The conditions that require the isolation room, and those that do not."""
    return _read(RULES / "isolation_conditions.yaml")


@cache
def chief_complaints() -> dict[str, list[str]]:
    """The closed list of chief complaints, each with the ward or wards it points to."""
    return _read(RULES / "chief_complaints.yaml")["complaints"]


@cache
def news2_levels() -> dict:
    """NEWS2 thresholds for the level of care."""
    return _read(RULES / "news2_levels.yaml")


@cache
def severity_judgment() -> dict[str, list[dict]]:
    """When the record raises the level NEWS2 points to, keeps it with a note, or which data counts — the approved
    situations only (a situation still to be checked is never handed to an agent)."""
    groups = _read(RULES / "severity_judgment.yaml")
    return {group: [item for item in items if item["status"] == "approved"] for group, items in groups.items()}


@cache
def structured_signs() -> dict:
    """What a code baseline can read from structured fields to conclude observation (no free text)."""
    return _read(RULES / "structured_signs.yaml")


@cache
def evaluation() -> dict:
    """The evaluation's fixed values: the charter's targets, the comparison's repetitions and its side checks."""
    return _read(PROJECT / "config" / "evaluation.yaml")


@cache
def placement_rules() -> dict:
    """Waiting, approval and bed rules for placement."""
    return _read(RULES / "placement_rules.yaml")


def ladder_level(target_ward: str, ward_id: str) -> int | None:
    """The level of `ward_id` on the target ward's ladder (1 = the target ward itself); None if not on it."""
    for number, level in enumerate(ladders()[target_ward], start=1):
        if ward_id in level:
            return number
    return None
