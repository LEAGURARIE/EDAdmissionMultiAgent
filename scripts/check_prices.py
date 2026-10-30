r"""Compare the prices in config/agents.yaml with Anthropic's pricing page — now, whatever the date.

Run (from the project folder):
    .venv\Scripts\python scripts\check_prices.py

Every paid run does the same check by itself once a month (src/ed_admission/agents/prices.py). No model is called
and nothing is paid: the page is downloaded as text. A changed price is printed, never written — it is updated in
config/agents.yaml only after it is approved.
Exit code: 0 the prices match · 1 a price changed · 2 the page could not be read.
Owner: Lea Gur-Arie
"""
import sys

from ed_admission.agents import prices


def main() -> int:
    result = prices.check()
    if result.status == "match":
        print("The prices match Anthropic's page. 'checked' in config/agents.yaml is set to today.")
        return 0
    if result.status == "changed":
        print("A price changed — approve it before updating config/agents.yaml:")
        print(result.table())
        return 1
    print(f"The pricing page could not be read: {result.problem}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
