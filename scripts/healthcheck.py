"""Check the development environment: Python version and the Anthropic key.

Run:  .venv\\Scripts\\python scripts\\healthcheck.py
Prints one line per check. Exit code 0 only if every check passes.
Owner: integrator
"""
import os
import sys

from dotenv import load_dotenv

MINIMUM_PYTHON = (3, 11)


def check_python():
    version = sys.version_info
    ok = version >= MINIMUM_PYTHON
    return ok, f"Python {version.major}.{version.minor}.{version.micro} (minimum {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]})"


def check_anthropic():
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY is not set (locally: in .env · in Colab: in the Colab secrets)"
    import anthropic

    try:
        # Reading model information verifies the key without generating any tokens.
        model = anthropic.Anthropic().models.retrieve("claude-opus-5-5")
        return True, f"Anthropic key valid — model available: {model.display_name}"
    except anthropic.AuthenticationError:
        return False, "Anthropic key rejected — check ANTHROPIC_API_KEY (.env, or the Colab secrets)"
    except anthropic.APIError as error:
        return False, f"Anthropic check failed — {error}"


def main():
    load_dotenv()
    checks = [("Python", check_python), ("Anthropic", check_anthropic)]
    all_ok = True
    for name, check in checks:
        ok, message = check()
        all_ok = all_ok and ok
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {message}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
