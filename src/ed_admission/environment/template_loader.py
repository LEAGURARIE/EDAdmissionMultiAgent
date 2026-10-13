"""Builds the hospital state of a ward-state template (data/templates/*.yaml) for one case.

A template describes one moment in the hospital: day, time, and for each ward how many beds of each type
are free, which are being cleaned (and when they will be ready), which are held, and which are expected to
be discharged today. Every other bed is occupied.

A relative template (relative_to: target_ward) is written by ladder level — level_1 is the patient's target
ward — so the same dilemma appears whatever ward the patient is meant for. A fixed template names the wards.

Templates are environment data: the decision maker never reads them, only the ward reports.
Design: docs/design/ward_state_templates.md.

Owner: Lea Gur-Arie
"""
from pathlib import Path

import yaml

from ed_admission import config
from ed_admission.environment.events import Event, EventType, to_minutes
from ed_admission.environment.hospital import BedStatus, BedType, Ward, WardLevel
from ed_admission.environment.scenario_loader import load_hospital
from ed_admission.environment.simulation_engine import SimulationEngine

# Field names in a ward spec -> bed type
FREE_FIELDS = {"regular_free": BedType.REGULAR, "observation_free": BedType.OBSERVATION,
               "intensive_care_free": BedType.INTENSIVE_CARE}
HELD_FOR_OTHER = "not-from-the-emergency-department"   # internal marker; ward reports never show who holds a bed


class TemplateError(Exception):
    """Raised when a template asks for more beds than a ward has, or names an unknown ward or level."""


def load_template(template_path: Path, hospital_path: Path | None = None,
                  target_ward: str | None = None) -> SimulationEngine:
    """One moment in the hospital, for one case. A relative template needs the patient's target ward."""
    template = yaml.safe_load(Path(template_path).read_text(encoding="utf8"))
    hospital = load_hospital(hospital_path)
    engine = SimulationEngine(hospital=hospital, clock=to_minutes(template["time"]), day=template["day"])

    for ward_id, spec in _ward_specs(template, hospital.wards, config.ladders(), target_ward).items():
        _apply(engine, hospital.ward(ward_id), spec)
    return engine


def _ward_specs(template: dict, wards: dict[str, Ward], ladders: dict, target_ward: str | None) -> dict[str, dict]:
    """The spec of every ward: by ladder level (relative) or by name (fixed); `default` for the rest."""
    default = template.get("default", {})
    specs = {ward_id: default for ward_id, ward in wards.items() if ward.level == WardLevel.REGULAR}

    if template["relative_to"] == "target_ward":
        if target_ward not in ladders:
            raise TemplateError(f"{template['id']}: a relative template needs a target ward with a ladder, got {target_ward!r}")
        levels = ladders[target_ward]
        for level_name, spec in template.get("ladder", {}).items():
            number = int(level_name.removeprefix("level_"))
            if number > len(levels):
                continue                           # this target ward's ladder is shorter — nothing to apply
            for ward_id in levels[number - 1]:
                specs[ward_id] = spec
    else:
        for ward_id, spec in template.get("wards", {}).items():
            if ward_id not in wards:
                raise TemplateError(f"{template['id']}: unknown ward {ward_id!r}")
            specs[ward_id] = spec

    specs["intensive_care"] = template.get("intensive_care", {}) | specs.get("intensive_care", {})
    return specs


def _apply(engine: SimulationEngine, ward: Ward, spec: dict) -> None:
    """Every bed starts occupied; then free, cleaning, held and discharges-today are set in bed-number order.
    The ward's private notes, if any, are kept on the ward, with what they mean for its beds (note_effects)."""
    ward.notes = list(spec.get("notes", []))
    ward.note_effects = list(spec.get("note_effects", []))
    for bed in ward.beds:
        bed.status, bed.patient_id = BedStatus.OCCUPIED, f"inpatient-{ward.ward_id}-{bed.number}"
    available = {bed_type: [b for b in ward.beds if b.bed_type == bed_type] for bed_type in BedType}

    def take(bed_type: BedType, what: str):
        if not available[bed_type]:
            raise TemplateError(f"{ward.ward_id}: not enough {bed_type.value} beds for {what}")
        return available[bed_type].pop(0)

    for field, bed_type in FREE_FIELDS.items():
        for _ in range(spec.get(field, 0)):
            bed = take(bed_type, field)
            bed.status, bed.patient_id = BedStatus.FREE, None
    if spec.get("isolation_free", False):
        bed = take(BedType.ISOLATION, "isolation_free")
        bed.status, bed.patient_id = BedStatus.FREE, None

    for item in spec.get("cleaning", []):
        bed = take(BedType(item["bed_type"]), "cleaning")
        bed.status, bed.patient_id = BedStatus.CLEANING, None
        bed.cleaning_until = engine.clock + item["ready_in"]
        engine.schedule(Event(time=bed.cleaning_until, event_type=EventType.CLEANING_DONE,
                              ward_id=ward.ward_id, bed_number=bed.number))

    for item in spec.get("held", []):
        bed = take(BedType(item["bed_type"]), "held")
        bed.status, bed.patient_id = BedStatus.FREE, None
        engine.hold_bed(ward.ward_id, bed.number, HELD_FOR_OTHER)

    for item in spec.get("discharges_today", []):
        bed = take(BedType(item["bed_type"]), "discharges_today")   # stays occupied; the ward expects it to free today
        bed.expected_discharge_at = engine.clock
