"""The monthly price check: are the prices in config/agents.yaml still the prices on Anthropic's page?

The API returns token counts, never prices, so the prices are written once in config/agents.yaml. Every run that
pays for model calls starts with ensure_current() (budget.start_run):
  - checked less than recheck_after_days ago  -> nothing to do
  - due, and every price matches the page     -> the `checked` date is set to today (the only line code writes there)
  - due, and a price changed                  -> PricesChanged: the run stops; the new price is written only after
                                                 Lea approves it
  - due, and the page cannot be read          -> a warning; the run goes on with the prices it has, and the check
                                                 is tried again next run

The page is downloaded as plain text (its markdown version) — no model is called, no key is needed, nothing is paid.
Run now, whatever the date:  .venv\\Scripts\\python scripts\\check_prices.py

Owner: Lea Gur-Arie
"""
import re
import urllib.request
import warnings
from dataclasses import dataclass, field
from datetime import date

from ed_admission import config
from ed_admission.agents.budget import ModelPrices, agents_config, reload

AGENTS_FILE = config.PROJECT / "config" / "agents.yaml"
FIELDS = ("input", "cache_write", "cache_read", "output")


class PricesChanged(Exception):
    """A price on Anthropic's page differs from config/agents.yaml — the run stops before anything is spent."""


@dataclass
class PriceCheck:
    """What one check found. status: not_due · match · changed · unreadable."""
    status: str
    changes: list[tuple[str, str, float, float]] = field(default_factory=list)   # model, field, ours, the page's
    problem: str = ""

    def table(self) -> str:
        lines = [f"{'model':<18} {'price':<12} {'ours':>8} {'page':>8}"]
        lines += [f"{model:<18} {name:<12} {ours:>8.2f} {page:>8.2f}" for model, name, ours, page in self.changes]
        return "\n".join(lines)


# ============ READING THE PAGE ============
def fetch_page(url: str) -> str:
    """The pricing page as text. The site refuses Python's default name (403), so the check names itself."""
    request = urllib.request.Request(url, headers={"User-Agent": "ed-admission-price-check/1.0"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return answer.read().decode("utf8")


def display_name(model_id: str) -> str:
    """claude-opus-5-5 -> Claude Opus 5.5: the name the pricing table uses."""
    _, family, *version = model_id.split("-")
    return f"Claude {family.capitalize()} {'.'.join(version)}"


def page_prices(page: str, model_id: str) -> ModelPrices | None:
    """The model's row in the pricing table: base input · 5m cache write · 1h cache write · cache read · output.
    None when the row is not there or cannot be read."""
    name = display_name(model_id)
    for line in page.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and re.sub(r"\s*\(.*$", "", cells[0]) == name:   # "Claude Mythos 5.1 ([limited ...])"
            found = [re.search(r"\$([\d.]+)", cell) for cell in cells[1:]]
            if not all(found):                                    # the table changed shape: not readable
                return None
            numbers = [float(match.group(1)) for match in found]
            return ModelPrices(input=numbers[0], cache_write=numbers[1], cache_read=numbers[3], output=numbers[4])
    return None


# ============ THE CHECK ============
def compare(page: str) -> PriceCheck:
    """Every model in config/agents.yaml against its row on the page."""
    changes, missing = [], []
    for model_id, ours in agents_config().prices.models.items():
        theirs = page_prices(page, model_id)
        if theirs is None:
            missing.append(model_id)
            continue
        changes += [(model_id, name, getattr(ours, name), getattr(theirs, name))
                    for name in FIELDS if getattr(ours, name) != getattr(theirs, name)]
    if missing:
        return PriceCheck("unreadable", problem=f"no row on the page for {', '.join(missing)}")
    return PriceCheck("changed", changes) if changes else PriceCheck("match")


def is_due(today: date | None = None) -> bool:
    prices = agents_config().prices
    return ((today or date.today()) - prices.checked).days >= prices.recheck_after_days


def mark_checked(today: date) -> None:
    """Set `checked:` in config/agents.yaml to today — that one line only, every comment kept."""
    text = AGENTS_FILE.read_text(encoding="utf8")
    text, count = re.subn(r"(?m)^(\s*checked:\s*)\S+", rf"\g<1>{today.isoformat()}", text)
    if count != 1:
        raise ValueError("config/agents.yaml must have exactly one 'checked:' line")
    AGENTS_FILE.write_text(text, encoding="utf8")
    reload()


def check(today: date | None = None, fetch=fetch_page) -> PriceCheck:
    """Compare now, whatever the date. On a match, `checked` becomes today."""
    today = today or date.today()
    try:
        page = fetch(agents_config().prices.source)
    except OSError as error:                                  # no network, timeout, the page moved
        return PriceCheck("unreadable", problem=f"{type(error).__name__}: {error}")
    result = compare(page)
    if result.status == "match":
        mark_checked(today)
    return result


def ensure_current(today: date | None = None, fetch=fetch_page) -> PriceCheck:
    """The start of every paid run: check only when due; a changed price stops the run."""
    if not is_due(today):
        return PriceCheck("not_due")
    result = check(today, fetch)
    if result.status == "changed":
        raise PricesChanged("Anthropic's prices changed — approve the new prices in config/agents.yaml first:\n"
                            + result.table())
    if result.status == "unreadable":
        warnings.warn(f"The price check could not read the pricing page ({result.problem}); "
                      "the run goes on with the prices in config/agents.yaml.")
    return result
