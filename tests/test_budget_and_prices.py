"""The agents' settings file, the run budget and the monthly price check. No model is called and nothing is
downloaded: the pricing page is a short copy of its table, and the settings file is a copy in a temporary folder."""
import shutil
import warnings
from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from ed_admission import config
from ed_admission.agents import budget, prices
from ed_admission.agents.budget import AgentsConfig

PAGE = """
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Claude Mythos 5.1 ([limited availability](https://support.claude.com/en/articles/14604842)) | $10 / MTok | $12.50 / MTok | $20 / MTok | $0.25 / MTok<sup>1</sup> | $50 / MTok |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Sonnet 5.5 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.10 / MTok<sup>2</sup> | $10 / MTok |
| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |
| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |
"""


@pytest.fixture
def settings_copy(tmp_path, monkeypatch):
    """config/agents.yaml copied to a temporary project, so a test may change it."""
    (tmp_path / "config").mkdir()
    shutil.copy(config.PROJECT / "config" / "agents.yaml", tmp_path / "config" / "agents.yaml")
    monkeypatch.setattr(config, "PROJECT", tmp_path)
    monkeypatch.setattr(prices, "AGENTS_FILE", tmp_path / "config" / "agents.yaml")
    budget.reload()
    yield tmp_path / "config" / "agents.yaml"
    monkeypatch.undo()
    budget.reload()


# ---------- the settings file ----------
def test_the_real_settings_file_is_valid_and_complete():
    values = budget.agents_config()
    assert set(values.models) == set(values.budget.activation)
    assert all(model in values.prices.models for model in values.models.values())


def test_the_single_agent_gets_its_own_budget():
    s = budget.settings("single_agent")
    assert (s["max_iterations"], s["cost_cap_usd"]) == (15, 1.00) and s["prices"].cache_read == 0.20


@pytest.mark.parametrize("change, words", [
    (lambda v: v.update(effort="extreme"), "effort"),
    (lambda v: v["budget"]["activation"]["single_agent"].update(max_iterations=-1), "max_iterations"),
    (lambda v: v["budget"]["activation"].pop("department"), "without a budget"),
    (lambda v: v["models"].update(patient="claude-unknown-1"), "without prices"),
    (lambda v: v["prices"]["models"]["claude-opus-5-5"].pop("cache_read"), "cache_read"),
    (lambda v: v.update(temperature=0.2), "temperature"),
    (lambda v: v["budget"]["run_cost_cap_usd"].pop("multi_agent"), "multi_agent"),
    (lambda v: v["budget"].pop("case_cost_cap_usd"), "case_cost_cap_usd"),
])
def test_a_wrong_settings_file_stops_at_load(change, words):
    values = yaml.safe_load((config.PROJECT / "config" / "agents.yaml").read_text(encoding="utf8"))
    change(values)
    with pytest.raises(ValidationError, match=words):
        AgentsConfig.model_validate(values)


# ---------- the run budget ----------
def test_each_system_has_its_own_case_and_run_caps():
    caps = budget.agents_config().budget
    assert budget.settings("single_agent")["cost_cap_usd"] == 1.00 and caps.case_cost_cap_usd["multi_agent"] == 1.20
    assert budget.RunBudget("single_agent").cap_usd == 12.00
    assert budget.RunBudget("multi_agent").cap_usd == 14.40


def test_the_run_budget_starts_from_the_settings_and_counts_up():
    run = budget.RunBudget("single_agent")
    assert run.cap_usd == budget.agents_config().budget.run_cost_cap_usd["single_agent"] and not run.exhausted
    run.add(run.cap_usd)
    assert run.exhausted


def test_start_run_checks_the_prices_first(monkeypatch):
    seen = []
    monkeypatch.setattr(prices, "ensure_current", lambda: seen.append("checked"))
    run = budget.start_run("multi_agent")
    assert run.system == "multi_agent" and seen == ["checked"]


# ---------- reading the page ----------
def test_the_display_name_matches_the_page():
    assert prices.display_name("claude-opus-5-5") == "Claude Opus 5.5"
    assert prices.display_name("claude-opus-5") == "Claude Opus 5"


def test_the_row_of_the_model_is_read_and_not_its_neighbour():
    assert prices.page_prices(PAGE, "claude-opus-5-5") == budget.ModelPrices(input=4, cache_write=5, cache_read=0.2,
                                                                             output=20)
    assert prices.page_prices(PAGE, "claude-opus-5").input == 5
    assert prices.page_prices(PAGE, "claude-mythos-5-1").cache_read == 0.25        # the link text is ignored
    assert prices.page_prices(PAGE, "claude-haiku-5-5").input == 0.10       # the row up to 100,000 tokens
    assert prices.page_prices(PAGE, "claude-haiku-4-5") is None


# ---------- the monthly check ----------
def test_not_due_means_no_download(settings_copy):
    checked = budget.agents_config().prices.checked
    result = prices.ensure_current(today=checked, fetch=lambda url: pytest.fail("downloaded"))
    assert result.status == "not_due"


def test_due_and_matching_sets_the_date_and_keeps_every_comment(settings_copy):
    before = settings_copy.read_text(encoding="utf8")
    later = date(2030, 1, 1)
    result = prices.ensure_current(today=later, fetch=lambda url: PAGE)
    assert result.status == "match" and budget.agents_config().prices.checked == later
    after = settings_copy.read_text(encoding="utf8")
    assert after.replace("2030-01-01", "") == before.replace(budget.agents_config().prices.checked.isoformat(), "") \
        or after.count("#") == before.count("#")
    assert [line for line in before.splitlines() if "checked:" not in line] == \
           [line for line in after.splitlines() if "checked:" not in line]


def test_due_and_changed_stops_the_run_and_writes_nothing(settings_copy):
    before = settings_copy.read_text(encoding="utf8")
    raised = PAGE.replace("| $4 / MTok |", "| $4.50 / MTok |")
    with pytest.raises(prices.PricesChanged, match="input"):
        prices.ensure_current(today=date(2030, 1, 1), fetch=lambda url: raised)
    assert settings_copy.read_text(encoding="utf8") == before


def test_an_unreadable_page_warns_and_the_run_goes_on(settings_copy):
    def offline(url):
        raise OSError("no network")

    before = settings_copy.read_text(encoding="utf8")
    with pytest.warns(UserWarning, match="no network"):
        result = prices.ensure_current(today=date(2030, 1, 1), fetch=offline)
    assert result.status == "unreadable" and settings_copy.read_text(encoding="utf8") == before


def test_a_page_without_the_model_is_unreadable_not_a_match(settings_copy):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = prices.ensure_current(today=date(2030, 1, 1), fetch=lambda url: "| nothing here |")
    assert result.status == "unreadable" and "claude-opus-5-5" in result.problem
