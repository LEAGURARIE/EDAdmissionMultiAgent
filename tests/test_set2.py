"""The second evaluation set — ward notes (templates T13-T16) and ping-pong patterns (T17-T19): the notes are private, and each note changes the
expected answer the way its note_effects (next to the note, in the template) say."""
from pathlib import Path

import yaml

from ed_admission.baselines import first_free_bed
from ed_admission.environment import load_template
from ed_admission.evaluation import load_pairs, run_cases

PROJECT = Path(__file__).resolve().parents[1]
ANSWER_KEY = PROJECT / "data" / "answer_key"
TEMPLATES = PROJECT / "data" / "templates"
PAIRS = load_pairs("second")
ANSWERS = {(p["case_id"], p["template"]): yaml.safe_load(
    (ANSWER_KEY / f"{p['case_id']}__{p['template']}.yaml").read_text(encoding="utf8")) for p in PAIRS}


def test_three_pairs_per_template():
    templates = [p["template"] for p in PAIRS if int(p["case_id"][2:]) <= 60]
    assert len(templates) == 30
    assert sorted(set(templates), key=lambda t: int(t[1:])) == [f"T{n}" for n in range(13, 23)]
    assert all(templates.count(t) == 3 for t in set(templates))


def test_every_note_is_private():
    """Every template has notes, and no ward's note appears in any ward's report."""
    for pair in PAIRS:
        engine = load_template(TEMPLATES / f"{pair['template']}.yaml", target_ward=pair["target_ward"])
        wards = list(engine.hospital.wards)
        notes = [note for ward in wards for note in engine.ward_notes(ward)]
        assert notes, pair
        reports = str([engine.department_status(ward) for ward in wards])
        assert not any(note in reports for note in notes)


def test_notes_change_the_answers():
    by_template = lambda t: [a for (_, tmpl), a in ANSWERS.items() if tmpl == t and a["challenge"] != 6]
    for a in by_template("T13"):                 # no nurse for observation here or on level 2: the isolation room,
        assert a["needs"]["observation"]         # as a last resort (back to the Supervision Agent)
        assert (a["expected"]["ward_id"], a["expected"]["bed_type"]) == (a["target_ward"], "isolation")
    for a in by_template("T14"):                 # the discharge will not happen today: nobody waits for it
        assert a["expected"]["action"] != "wait"
    for a in by_template("T15"):                 # a bed after the round: the non-severe patient waits for it
        assert not a["needs"]["severe"] and a["expected"]["action"] == "wait"
    for a in by_template("T16"):                 # the note that looks unimportant: the bed is taken, frees within the hour
        assert not a["needs"]["severe"] and a["expected"]["action"] == "wait"


def test_ping_pong_answers():
    by_template = lambda t: [a for (_, tmpl), a in ANSWERS.items() if tmpl == t]
    for a in by_template("T17"):                 # unsafe offer, and no observation bed on level 2: escalate
        assert a["needs"]["observation"] and a["expected"]["action"] == "escalate"
    for a in by_template("T18"):                 # only surgery's own note has the bed: level 4 of the ladder
        assert a["target_ward"] == "internal_medicine_a" and a["expected"]["ward_id"] == "general_surgery"
    for a in by_template("T19"):                 # a stable patient stays; an unstable one goes to level 2
        if a["needs"]["severe"]:
            assert a["expected"]["ward_id"] != a["target_ward"]
        else:
            assert a["expected"]["action"] == "place" and a["expected"]["ward_id"] == a["target_ward"]


def test_the_rumour_is_checked_with_the_ward_itself():
    """T18: Internal Medicine A heard surgery has room; surgery's report shows none — only its own note says so."""
    engine = load_template(TEMPLATES / "T18.yaml")
    assert "כירורגיה" in engine.ward_notes("internal_medicine_a")[0]
    assert not [b for b in engine.hospital.ward("general_surgery").beds if b.status.value == "free"]
    assert engine.ward_notes("general_surgery")


def test_the_shift_rule_answers():
    """T20: night — the reported discharge is not waited for · T21: weekend, but the ward said it frees soon — wait
    · T22: weekday morning, but the ward said it will not happen — no wait."""
    by_template = lambda t: [a for (_, tmpl), a in ANSWERS.items() if tmpl == t]
    for a in by_template("T20"):
        assert a["expected"]["action"] == "temporary_place" and a["expected"]["ladder_level"] == 3
    for a in by_template("T21"):
        assert a["expected"]["action"] == "wait" and a["expected"]["wait_for"]["because"] == "ward_note"
    for a in by_template("T22"):
        assert a["expected"]["action"] != "wait" and a["reports_alone_would_give"] == "wait"
