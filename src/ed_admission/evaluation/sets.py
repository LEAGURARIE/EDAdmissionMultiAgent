"""The development and test sets of the comparison (docs/design/comparison_protocol.md, section 3).

  development   22 pairs, one from each template — improving each system (2 rounds)
  test          22 other pairs, one from each template, and the 7 paired cases — the comparison; the four
                scripted days are measured once per system, apart

How the pairs are chosen — in code, the same every time, no randomness:
  - one pair from each of the 22 templates for each set
  - no patient in both sets, and no patient twice in one set: a round of improvement never learns a record the test
    pairs will show. The patients of the paired cases are test patients, so they are never in the development set
  - the scripted days reuse 44 of the 74 patients, so some development patients appear on a day too (14 in the
    frozen split). Keeping them out would leave kinds uncovered (checked: three kinds missing), so coverage comes
    first and a day patient is taken only on a tie. The days put the patient in another hospital state; this is
    stated in the protocol
  - inside a template, the pair that adds the most kinds not yet in its set — patient type, challenge, trap — so
    every kind is in both sets where the data allows; then the lower patient id
  - the test set is chosen first, the template with the fewest choices first

The split was frozen on 2026-10-15 in data/answer_key/dev_test_sets.yaml, before any paid run; a test guards that
the file and this code agree. Only the evaluation code reads it — like every file in data/answer_key/.

Owner: Lea Gur-Arie
"""
import yaml

from ed_admission import config

ANSWER_KEY = config.PROJECT / "data" / "answer_key"
SETS_FILE = ANSWER_KEY / "dev_test_sets.yaml"
PAIR_FILES = ("pairs.yaml", "pairs_set2.yaml")


def all_pairs() -> list[dict]:
    """Every pair, with the kinds it covers: case_id, template, target_ward, patient_type, challenge, traps."""
    pairs = []
    for name in PAIR_FILES:
        for pair in yaml.safe_load((ANSWER_KEY / name).read_text(encoding="utf8"))["pairs"]:
            answer = yaml.safe_load((ANSWER_KEY / f"{pair['case_id']}__{pair['template']}.yaml").read_text(
                encoding="utf8"))
            pairs.append({**pair, "patient_type": str(answer["patient_type"]), "challenge": str(answer["challenge"]),
                          "traps": [str(t) for t in answer.get("traps") or []]})
    return pairs


def kinds(pair: dict) -> set[str]:
    return {f"type {pair['patient_type']}", f"challenge {pair['challenge']}"} | {f"trap {t}" for t in pair["traps"]}


def paired_patients() -> set[str]:
    cases = yaml.safe_load((ANSWER_KEY / "paired_cases.yaml").read_text(encoding="utf8"))["cases"]
    return {patient["case_id"] for case in cases for patient in case["patients"]}


def day_patients() -> set[str]:
    patients = set()
    for path in (config.PROJECT / "data" / "scenarios").glob("*.yaml"):
        for event in yaml.safe_load(path.read_text(encoding="utf8")).get("events", []):
            if event.get("patient"):
                patients.add(event["patient"])
    return patients


def _template_order(template: str) -> int:
    return int(template.lstrip("T"))


def build() -> dict[str, list[dict]]:
    """The split, from the rules above: {"dev": [...], "test": [...]} — each a list of {case_id, template}."""
    pairs = all_pairs()
    templates = sorted({p["template"] for p in pairs}, key=_template_order)
    in_template = {t: sorted((p for p in pairs if p["template"] == t), key=lambda p: p["case_id"]) for t in templates}
    test_only, on_days = paired_patients(), day_patients()

    def choose(candidates: list[dict], covered: set[str], prefer_not: set[str]) -> dict:
        return min(candidates, key=lambda p: (-len(kinds(p) - covered), p["case_id"] in prefer_not, p["case_id"]))

    chosen: dict[str, dict[str, dict]] = {"test": {}, "dev": {}}
    for name in ("test", "dev"):
        covered: set[str] = set()
        for template in sorted(templates, key=lambda t: (len(in_template[t]), _template_order(t))):
            taken = {p["case_id"] for p in chosen[name].values()}                    # never twice in one set
            if name == "dev":                                                        # never a test patient
                taken |= {p["case_id"] for p in chosen["test"].values()} | test_only
            candidates = [p for p in in_template[template] if p["case_id"] not in taken]
            if not candidates:
                raise ValueError(f"{template}: no pair left for the {name} set")
            pair = choose(candidates, covered, on_days if name == "dev" else set())
            chosen[name][template] = pair
            covered |= kinds(pair)
    return {name: [{"case_id": chosen[name][t]["case_id"], "template": t} for t in templates]
            for name in ("dev", "test")}


def load() -> dict[str, list[dict]]:
    """The frozen split."""
    frozen = yaml.safe_load(SETS_FILE.read_text(encoding="utf8"))
    return {"dev": frozen["dev"], "test": frozen["test"]}


def pairs_of(set_name: str) -> set[tuple[str, str]] | None:
    """The (case_id, template) pairs of a set; None for "all"."""
    if set_name == "all":
        return None
    return {(p["case_id"], p["template"]) for p in load()[set_name]}
