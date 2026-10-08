"""The Colab notebook (notebooks/ED_Admission_Colab.ipynb) and requirements.txt: the notebook only runs what exists,
holds no secret, and installs exactly the project's dependencies."""
import json
import re
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
NOTEBOOK = PROJECT / "notebooks" / "ED_Admission_Colab.ipynb"


def _code() -> str:
    cells = json.loads(NOTEBOOK.read_text(encoding="utf8"))["cells"]
    return "\n".join("".join(c["source"]) for c in cells if c["cell_type"] == "code")


def test_the_notebook_runs_only_scripts_and_files_that_exist():
    code = _code()
    for script in re.findall(r"scripts/\w+\.py", code):
        assert (PROJECT / script).exists(), script
    for path in re.findall(r"data/scenarios/\w+\.yaml", code):
        assert (PROJECT / path).exists(), path
    for agent in ("clinical_problem", "severity", "supervision", "department", "safety_critic", "coordinator"):
        assert (PROJECT / "src" / "ed_admission" / "agents" / f"{agent}_agent.py").exists()
        assert (PROJECT / "src" / "ed_admission" / "prompts" / f"{agent}_agent.md").exists()


def test_the_notebook_holds_no_secret_and_reads_keys_from_colab_secrets():
    code = _code()
    assert 'userdata.get(name)' in code
    assert not re.search(r"(sk-ant-|ghp_|github_pat_)\w+", code)
    assert "git add ." not in code and "add -A" not in code


def test_requirements_match_the_projects_dependencies():
    def name(requirement: str) -> str:
        return re.split(r"[<>=!~\[ ]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf8"))["project"]
    declared = {name(r) for r in project["dependencies"] + project["optional-dependencies"]["dev"]}
    lines = [l for l in (PROJECT / "requirements.txt").read_text(encoding="utf8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert all("==" in l for l in lines), "every requirement is pinned"
    assert {name(l) for l in lines} == declared
