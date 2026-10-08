"""The expected answers must never reach the decision maker.

Two guards: no code outside the metrics package refers to data/answer_key, and no medical record
or scenario carries answer-like fields.
"""
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
CODE_FOLDERS = [PROJECT / "src", PROJECT / "scripts"]
METRICS_PACKAGE = PROJECT / "src" / "ed_admission" / "evaluation"     # the only code allowed to read answers
ANSWER_LIKE_KEYS = {"expected", "severe", "needs_observation", "needs_isolation", "target_ward",
                    "patient_type", "challenge", "traps", "template", "approved_by", "coverage", "note",
                    "news2", "level_of_care"}


def all_keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for value in node.values() for k in all_keys(value)}
    if isinstance(node, list):
        return {k for item in node for k in all_keys(item)}
    return set()


def test_only_metrics_code_refers_to_the_answer_key():
    offenders = [
        str(path.relative_to(PROJECT))
        for folder in CODE_FOLDERS for path in folder.rglob("*.py")
        if METRICS_PACKAGE not in path.parents and "answer_key" in path.read_text(encoding="utf8")
    ]
    assert offenders == []


def test_records_carry_no_answers():
    for path in (PROJECT / "data" / "records").glob("*.yaml"):
        leaked = all_keys(yaml.safe_load(path.read_text(encoding="utf8"))) & ANSWER_LIKE_KEYS
        assert not leaked, f"{path.name}: {sorted(leaked)}"


def test_scenarios_and_templates_carry_no_answers():
    paths = [*(PROJECT / "data" / "scenarios").glob("*.yaml"), *(PROJECT / "data" / "templates").glob("*.yaml")]
    for path in paths:
        leaked = all_keys(yaml.safe_load(path.read_text(encoding="utf8"))) & ANSWER_LIKE_KEYS
        assert not leaked, f"{path.name}: {sorted(leaked)}"


def test_records_have_no_comments():
    """A comment in a record could hint at the answer (e.g. '# NEWS2 = 6')."""
    for path in (PROJECT / "data" / "records").glob("*.yaml"):
        comments = [line for line in path.read_text(encoding="utf8").splitlines() if line.lstrip().startswith("#")]
        assert not comments, f"{path.name}: {comments}"


def test_the_case_file_example_mirrors_the_real_files():
    """docs/design/case_file_example.md copies three real files unchanged; if a file changes, the page is rebuilt."""
    page = (PROJECT / "docs" / "design" / "case_file_example.md").read_text(encoding="utf8")
    for path in ("data/records/P-043.yaml", "data/templates/T9.yaml", "data/answer_key/P-043__T9.yaml"):
        assert (PROJECT / path).read_text(encoding="utf8").rstrip("\n") in page, path
