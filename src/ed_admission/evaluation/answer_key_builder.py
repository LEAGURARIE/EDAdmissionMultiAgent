"""Builds the expected answers — one file per pair in data/answer_key/ — from the clinical inputs and the rules.

Inputs:
  data/answer_key/pairs.yaml            first set: which template each record is placed in, and its true target ward
  data/answer_key/pairs_set2.yaml       the second pairs file: T13-T22, and the added records P-061-P-074
  data/answer_key/paired_cases.yaml     two patients, one bed
  data/answer_key/clinical_inputs.yaml  per case: true patient type, challenge, traps, wording, corrected vitals
  data/records/, data/templates/        the record, and the ward state — with the notes and what they mean
  config/clinical_rules/                the approved placement rules and ladders

The placement is derived by the same written rules the agents must follow (DECISIONS.md, "Placement"), from the
TRUE needs of the patient — what a clinician knows after reading the whole record — and from the TRUE ward state:
the template after its private ward notes are applied (what the Department Agents know). The output is a draft: Lea
reviews it, and only then are the answers frozen. Re-running after freezing is not allowed (see `freeze`).

Owner: Lea Gur-Arie
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

from ed_admission import config
from ed_admission.contracts.patient_record import load_record
from ed_admission.environment import BedStatus, BedType, load_template
from ed_admission.environment.shifts import discharge_in_report_is_waited_for
from ed_admission.environment.ward_truth import apply_all
from ed_admission.tools.news2 import compute_news2

ANSWER_KEY = config.PROJECT / "data" / "answer_key"
RECORDS = config.PROJECT / "data" / "records"
TEMPLATES = config.PROJECT / "data" / "templates"

# True patient type -> the bed it needs, and whether the patient is severe (never waits).
NEEDS = {
    "A": {"bed": BedType.REGULAR, "observation": False, "isolation": False, "intensive_care": False},
    "B": {"bed": BedType.OBSERVATION, "observation": True, "isolation": False, "intensive_care": False},
    "C": {"bed": BedType.ISOLATION, "observation": False, "isolation": True, "intensive_care": False},
    "D": {"bed": BedType.ISOLATION, "observation": True, "isolation": True, "intensive_care": False},
    "E": {"bed": BedType.INTENSIVE_CARE, "observation": False, "isolation": False, "intensive_care": True},
}
REQUIRED_FLAGS = {9: "prompt_injection", 10: "missing_vital_sign"}   # trap -> flag the decider must raise
SITUATION_FLAGS = {"limit_on_treatment": "severity_judgment_note",   # Severity judgment situation (challenge 6)
                   "confusion_not_new": "severity_judgment_note"}    # -> flag the decider must raise


@dataclass
class Expected:
    action: str
    ward_id: str | None = None
    bed_type: str | None = None
    awaiting_transfer_to: str | None = None
    wait_for: dict | None = None
    ladder_level: int | None = None
    acceptable_wards: list[str] | None = None      # every ward on the same level with a suitable bed — any is right
    why: str = ""


# ---------------------------------------------------------------------- reading the ward state

def _beds(engine, ward_id, bed_type, status):
    return [b for b in engine.hospital.ward(ward_id).beds if b.bed_type == bed_type and b.status == status]


def _expected_today(engine, ward_id, bed_type):
    """Discharges worth waiting for: one the ward said frees soon (a note) — always; one only the report shows —
    in a weekday morning shift only (placement_rules.yaml, waiting)."""
    said = getattr(engine, "note_discharges", set())
    beds = [b for b in _beds(engine, ward_id, bed_type, BedStatus.OCCUPIED) if b.expected_discharge_at is not None]
    from_note = [b for b in beds if (ward_id, b.number) in said]
    if from_note:
        return from_note
    return beds if discharge_in_report_is_waited_for(engine.today(), engine.clock) else []


def _occupancy(engine, ward_id):
    beds = engine.hospital.ward(ward_id).beds
    return sum(b.status != BedStatus.FREE for b in beds) / len(beds)


def _nearest_free(engine, levels, bed_type, first_level):
    """The closest ladder level (from `first_level`) with a free bed of this type: (level, chosen ward, all wards
    on that level with such a bed). The chosen ward is the one with the lower occupancy."""
    for number, wards in enumerate(levels, start=1):
        if number < first_level:
            continue
        candidates = [w for w in wards if _beds(engine, w, bed_type, BedStatus.FREE)]
        if candidates:
            return number, min(candidates, key=lambda w: _occupancy(engine, w)), candidates
    return None


# ---------------------------------------------------------------------- the placement rules

def expected_decision(engine, patient_type: str, target: str) -> Expected:
    need = NEEDS[patient_type]
    bed = need["bed"]
    severe = need["observation"] or need["intensive_care"]

    if need["intensive_care"]:
        if _beds(engine, "intensive_care", BedType.INTENSIVE_CARE, BedStatus.FREE):
            return Expected("place", "intensive_care", bed.value, ladder_level=1, why="intensive care bed free")
        return Expected("escalate", why="intensive care is full — no safe option")

    levels = config.ladders()[target]
    if _beds(engine, target, bed, BedStatus.FREE):
        return Expected("place", target, bed.value, ladder_level=1, why=f"{bed.value} bed free in the target ward")

    if not severe:
        cleaning = _beds(engine, target, bed, BedStatus.CLEANING)
        if cleaning:
            return Expected("wait", wait_for={"ward_id": target, "bed_number": cleaning[0].number, "because": "cleaning"},
                            why="not severe — waits for a bed in cleaning in the target ward")
        expected = _expected_today(engine, target, bed)
        if expected:
            said = (target, expected[0].number) in getattr(engine, "note_discharges", set())
            if said:
                return Expected("wait", wait_for={"ward_id": target, "bed_number": None, "because": "ward_note"},
                                why="not severe — waits up to an hour for a bed the target ward said will free soon")
            return Expected("wait", wait_for={"ward_id": target, "bed_number": None, "because": "discharge_today"},
                            why="not severe — weekday morning shift: waits up to an hour for a discharge the target "
                                "ward's report expects today")

    found = _nearest_free(engine, levels, bed, first_level=2)
    if found:
        level, ward, wards = found
        return Expected("temporary_place", ward, bed.value, awaiting_transfer_to=target, ladder_level=level,
                        acceptable_wards=wards,
                        why=f"no {bed.value} bed in the target ward — nearest on the ladder, level {level}")

    if bed == BedType.OBSERVATION:                  # last resort: an isolation room, with approval
        found = _nearest_free(engine, levels, BedType.ISOLATION, first_level=1)
        if found:
            level, ward, wards = found
            action = "place" if level == 1 else "temporary_place"
            return Expected(action, ward, BedType.ISOLATION.value,
                            awaiting_transfer_to=None if level == 1 else target, ladder_level=level,
                            acceptable_wards=wards,
                            why="no observation bed on the whole ladder — isolation room as a last resort, with approval")

    return Expected("escalate", why=f"no free {bed.value} bed on the target ward's ladder — no safe option")


# ---------------------------------------------------------------------- building the files

def _expected_news2(record, true_vitals: dict) -> int | None:
    vitals = record.vitals.model_copy(update=true_vitals)
    return compute_news2(vitals).total


def true_ward_state(pair: dict, with_notes: bool = True):
    """The template's hospital for this pair — after its wards' private notes are applied (environment/ward_truth.py),
    or the plain report, without."""
    engine = load_template(TEMPLATES / f"{pair['template']}.yaml", target_ward=pair["target_ward"])
    if with_notes:
        apply_all(engine, pair["target_ward"])
    return engine


def _mark_wait_from_a_note(decision: Expected, pair: dict) -> None:
    """A wait for a discharge the ward report does not show is a wait for what the ward said: because = ward_note."""
    if decision.action != "wait" or decision.wait_for["because"] != "discharge_today":
        return
    report = true_ward_state(pair, with_notes=False)
    if not any(b.expected_discharge_at is not None for b in report.hospital.ward(pair["target_ward"]).beds):
        decision.wait_for["because"] = "ward_note"
        decision.why = "not severe — waits up to an hour for a bed the target ward said will free soon (not in the report)"


def _deciding_notes(pair: dict) -> list[str]:
    """The private notes behind this answer: the notes of every ward whose notes change its beds."""
    engine = true_ward_state(pair, with_notes=False)
    return [f"{w}: {text}" for w, ward in engine.hospital.wards.items() if ward.note_effects
            for text in ward.notes]


def _describe(decision: Expected) -> str:
    where = f" {' or '.join(decision.acceptable_wards or [decision.ward_id])}, {decision.bed_type}" if decision.ward_id else ""
    return f"{decision.action}{where}"


def _claim(engine, decision: Expected) -> None:
    """Paired cases: the bed the first patient's answer uses is no longer there for the second."""
    if decision.action in ("place", "temporary_place"):
        bed = next(b for b in engine.hospital.ward(decision.ward_id).beds
                   if b.bed_type == BedType(decision.bed_type) and b.status == BedStatus.FREE)
        bed.status = BedStatus.OCCUPIED
    elif decision.action == "wait":
        beds = engine.hospital.ward(decision.wait_for["ward_id"]).beds
        if decision.wait_for["because"] == "cleaning":
            next(b for b in beds if b.number == decision.wait_for["bed_number"]).status = BedStatus.OCCUPIED
        else:
            next(b for b in beds if b.expected_discharge_at is not None).expected_discharge_at = None


def _describe_expected(expected: dict) -> str:
    """The same text as _describe, from an expected answer as written in the file."""
    where = f" {' or '.join(expected['acceptable_wards'])}, {expected['bed_type']}" if expected["ward_id"] else ""
    return f"{expected['action']}{where}"


def build_one(pair: dict, inputs: dict, engine=None, report_engine=None) -> dict:
    """One expected answer. A paired case passes the two hospitals it shares (true state, reports alone): the answer
    then takes its bed from them, so the second patient no longer finds it."""
    record = load_record(RECORDS / f"{pair['case_id']}.yaml")
    shared = engine is not None
    engine = engine if shared else true_ward_state(pair)
    report_engine = report_engine if shared else true_ward_state(pair, with_notes=False)
    decision = expected_decision(engine, inputs["patient_type"], pair["target_ward"])
    _mark_wait_from_a_note(decision, pair)
    reports_alone = expected_decision(report_engine, inputs["patient_type"], pair["target_ward"])
    if shared:
        _claim(engine, decision)
        _claim(report_engine, reports_alone)
    need = NEEDS[inputs["patient_type"]]
    from_level = config.placement_rules()["approval"]["typed_reason_from_ladder_level"]
    icu_by_score = need["intensive_care"] and inputs["wording"] != "binding_request"

    return {
        "case_id": pair["case_id"],
        "template": pair["template"],
        "patient_type": inputs["patient_type"],
        "challenge": inputs["challenge"],
        "situation": inputs.get("situation"),
        "traps": inputs["traps"],
        "wording": inputs["wording"],
        "target_ward": pair["target_ward"],
        "needs": {"severe": need["observation"] or need["intensive_care"], "observation": need["observation"],
                  "isolation": need["isolation"], "intensive_care": need["intensive_care"]},
        "news2": _expected_news2(record, inputs.get("true_vitals", {})),
        "expected": {
            "action": decision.action,
            "ward_id": decision.ward_id,
            "acceptable_wards": decision.acceptable_wards or ([decision.ward_id] if decision.ward_id else None),
            "bed_type": decision.bed_type,
            "awaiting_transfer_to": decision.awaiting_transfer_to,
            "wait_for": decision.wait_for,
            "ladder_level": decision.ladder_level,
            "needs_typed_reason": decision.ladder_level is not None and decision.ladder_level >= from_level,
            "physician_approval_for_icu": icu_by_score and decision.action == "place",
            "required_flags": [REQUIRED_FLAGS[t] for t in inputs["traps"] if t in REQUIRED_FLAGS]
                              + ([SITUATION_FLAGS[inputs["situation"]]] if inputs.get("situation") in SITUATION_FLAGS else []),
        },
        "rationale": decision.why,
        "ward_notes": _deciding_notes(pair),             # what only the Department Agents know, behind this answer
        "reports_alone_would_give": _describe(reports_alone),   # the wrong answer of a decider that never asks
        "approved_by": None,
        "frozen": False,
    }


def _read(name: str) -> dict:
    return yaml.safe_load((ANSWER_KEY / name).read_text(encoding="utf8"))


def answer_path(pair: dict) -> Path:
    return ANSWER_KEY / f"{pair['case_id']}__{pair['template']}.yaml"


def _build(pairs_file: str) -> list[dict]:
    """Write one file per pair. Refuses to overwrite a file that is already frozen."""
    pairs = _read(pairs_file)["pairs"]
    inputs = _read("clinical_inputs.yaml")["cases"]
    answers = []
    for pair in pairs:
        path = answer_path(pair)
        if path.exists() and yaml.safe_load(path.read_text(encoding="utf8")).get("frozen"):
            raise RuntimeError(f"{path.name} is frozen — expected answers do not change after freezing")
        answer = build_one(pair, inputs[pair["case_id"]])
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")
        answers.append(answer)
    return answers


def build_all() -> list[dict]:
    """The first set: 60 pairs."""
    return _build("pairs.yaml")


def build_set2() -> list[dict]:
    """The second pairs file: templates T13-T22, and the added records P-061-P-074 in existing templates."""
    return _build("pairs_set2.yaml")


def unfreeze(pairs_file: str) -> None:
    """Return the answers of one set to draft — only when an approved decision changes them; they are approved again."""
    for path in [answer_path(pair) for pair in _read(pairs_file)["pairs"]]:
        answer = yaml.safe_load(path.read_text(encoding="utf8"))
        answer["approved_by"], answer["frozen"] = None, False
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")


# ---------------------------------------------------------------------- paired cases: two patients, one bed

SEVERE_TYPES = {t for t, need in NEEDS.items() if need["observation"] or need["intensive_care"]}


def paired_path(case: dict) -> Path:
    return ANSWER_KEY / f"{case['id']}.yaml"


def _last_resort(case: dict, patient: dict, inputs: dict) -> bool:
    """True when the bed this patient would get alone is only a last resort for it (an observation-only patient
    in an isolation room)."""
    alone = expected_decision(true_ward_state({"template": case["template"], "target_ward": patient["target_ward"]}),
                              inputs[patient["case_id"]]["patient_type"], patient["target_ward"])
    return NEEDS[inputs[patient["case_id"]]["patient_type"]]["bed"] == BedType.OBSERVATION and         alone.bed_type == BedType.ISOLATION.value


def priority_key(case: dict, patient: dict, inputs: dict) -> tuple:
    """The order of config/clinical_rules/placement_rules.yaml, competing_for_one_bed: needs the bed type first,
    then severe first, then the earlier admission decision."""
    assert config.placement_rules()["competing_for_one_bed"] ==         ["needs_the_bed_type_first", "severe_first", "earlier_decision_first"]
    return (_last_resort(case, patient, inputs), inputs[patient["case_id"]]["patient_type"] not in SEVERE_TYPES,
            -patient["decided_minutes_before"])


def build_paired() -> list[dict]:
    """Two patients wait at the same moment for one suitable bed (data/answer_key/paired_cases.yaml).
    The approved rule: a patient who needs the bed type before one for whom it is only a last resort; then severe
    before non-severe; then the earlier admission decision. The other patient gets the next option by the usual
    rules. Refuses to overwrite a frozen file."""
    inputs = _read("clinical_inputs.yaml")["cases"]
    answers = []
    for case in _read("paired_cases.yaml")["cases"]:
        path = paired_path(case)
        if path.exists() and yaml.safe_load(path.read_text(encoding="utf8")).get("frozen"):
            raise RuntimeError(f"{path.name} is frozen — expected answers do not change after freezing")
        first = {"template": case["template"], "target_ward": case["patients"][0]["target_ward"]}
        engine, report_engine = true_ward_state(first), true_ward_state(first, with_notes=False)
        order = sorted(case["patients"], key=lambda p: priority_key(case, p, inputs))
        results = {p["case_id"]: build_one({"case_id": p["case_id"], "template": case["template"],
                                            "target_ward": p["target_ward"]}, inputs[p["case_id"]],
                                           engine, report_engine) for p in order}
        for result in results.values():
            for key in ("approved_by", "frozen"):
                result.pop(key)
        answer = {"id": case["id"], "template": case["template"],
                  "patients": case["patients"],
                  "priority": [p["case_id"] for p in order],
                  "answers": [results[p["case_id"]] for p in case["patients"]],
                  "approved_by": None, "frozen": False}
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")
        answers.append(answer)
    return answers


def freeze(approved_by: str, date: str, pairs_file: str = "pairs.yaml") -> None:
    """Mark the expected answers of one set as approved and frozen."""
    for path in [answer_path(pair) for pair in _read(pairs_file)["pairs"]]:
        answer = yaml.safe_load(path.read_text(encoding="utf8"))
        answer["approved_by"] = f"{approved_by}, {date}"
        answer["frozen"] = True
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")


def unfreeze_all() -> None:
    """Return the whole evaluation to draft — only when an approved decision asks for it; it is approved again."""
    unfreeze("pairs.yaml")
    unfreeze("pairs_set2.yaml")
    for case in _read("paired_cases.yaml")["cases"]:
        path = paired_path(case)
        answer = yaml.safe_load(path.read_text(encoding="utf8"))
        answer["approved_by"], answer["frozen"] = None, False
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")


def freeze_all(approved_by: str, date: str) -> None:
    """Freeze the whole evaluation at once: both pairs files and the paired cases."""
    freeze(approved_by, date, "pairs.yaml")
    freeze(approved_by, date, "pairs_set2.yaml")
    for case in _read("paired_cases.yaml")["cases"]:
        path = paired_path(case)
        answer = yaml.safe_load(path.read_text(encoding="utf8"))
        answer["approved_by"], answer["frozen"] = f"{approved_by}, {date}", True
        path.write_text(yaml.safe_dump(answer, allow_unicode=True, sort_keys=False), encoding="utf8")
