"""Two simple deciders, for the tests only — they exercise the engine, the runner and the metrics with behaviour that
is easy to predict. They are not baselines and are not reported; the one baseline is the Code Coordinator
(src/ed_admission/baselines/code_coordinator.py).

  first_free_bed   takes the first free regular or observation bed in any regular ward, without looking at the
                   patient; serves the waiting patients in the order the admissions were decided
  never_asks       reads the structured fields and takes the first ranked option — it never asks a ward, so it does
                   not know what the wards' private notes say
"""
from ed_admission.baselines.code_coordinator import to_decision, waited_too_long
from ed_admission.baselines.reading import read_record
from ed_admission.environment.decision import Decision
from ed_admission.environment.hospital import BedType, WardLevel
from ed_admission.tools.rank_placements import Option, rank_placements

ALLOWED_BED_TYPES = (BedType.REGULAR, BedType.OBSERVATION)


def first_free_bed(engine, event, waiting) -> list[Decision]:
    free_beds = [(ward.ward_id, bed["bed"])
                 for ward in engine.hospital.wards.values() if ward.level == WardLevel.REGULAR
                 for bed in engine.department_status(ward.ward_id)["free_beds"]
                 if BedType(bed["bed_type"]) in ALLOWED_BED_TYPES]
    decisions = []
    for patient in sorted(waiting, key=lambda p: p.decided_at):
        if free_beds:
            ward_id, bed_number = free_beds.pop(0)
            decisions.append(Decision.place(patient.patient_id, ward_id, bed_number, reason="first free bed"))
        else:
            decisions.append(Decision.wait(patient.patient_id, "no free regular or observation bed in a regular ward"))
    return decisions


def never_asks(engine, event, waiting) -> list[Decision]:
    reports = {ward: engine.department_status(ward) for ward in engine.hospital.wards}
    readings = {p.patient_id: read_record(p.patient_id, reports) for p in waiting}
    order = sorted(waiting, key=lambda p: (not readings[p.patient_id].needs.severe, p.decided_at))
    taken, decisions = set(), []
    for patient in order:
        reading = readings[patient.patient_id]
        option = rank_placements(reports, reading.needs, taken).best
        if option.action == "wait" and waited_too_long(engine, patient):
            option = Option("escalate", reason="waited an hour and no bed on the ladder")
        if option.bed_number is not None and option.action != "wait":
            taken.add((option.ward_id, option.bed_number))
        decisions.append(to_decision(patient.patient_id, option, reading.needs.target_ward, reading))
    return decisions


def knows_the_answers(engine, event, waiting, order: str = "rule") -> list[Decision]:
    """For the tests only: knows every patient's TRUE needs and target ward (it reads the answer key — no decider may)
    and follows the approved rules: the rule for two patients and one bed decides who comes first, rank_placements
    decides where. It shows a day's designed moments can be reached and scored right. order="arrival":
    the same, but first come first served — what a decider that ignores the rule would do."""
    from types import SimpleNamespace

    from ed_admission import config
    from ed_admission.evaluation import answer_key_builder as builder
    from ed_admission.evaluation.metrics import RULE_KEYS
    from ed_admission.tools.rank_placements import PatientNeeds

    inputs = builder._read("clinical_inputs.yaml")["cases"]
    pairs = builder._read("pairs.yaml")["pairs"] + builder._read("pairs_set2.yaml")["pairs"]
    targets = {p["case_id"]: p["target_ward"] for p in pairs}
    rule = config.placement_rules()["competing_for_one_bed"]
    needs = {p.patient_id: builder.NEEDS[inputs[p.patient_id]["patient_type"]] for p in waiting}

    def first(patient):
        """The approved order. On a day where beds are short, an observation-only patient can only get the isolation
        room as a last resort — so, for the first key, the contested bed type is the isolation room."""
        n = needs[patient.patient_id]
        contested = "isolation" if n["bed"].value in ("isolation", "observation") else n["bed"].value
        return tuple(RULE_KEYS[name](n, contested, patient.decided_at) for name in rule) + (patient.patient_id,)

    reports = {ward: engine.department_status(ward) for ward in engine.hospital.wards}
    taken, decisions = set(), []
    arrival = lambda p: (p.decided_at, p.patient_id)
    for patient in sorted(waiting, key=first if order == "rule" else arrival):
        n, target = needs[patient.patient_id], targets[patient.patient_id]
        severe = n["observation"] or n["intensive_care"]
        ranking = rank_placements(reports, PatientNeeds(target, n["bed"].value, severe), taken)
        option = ranking.options[0]
        reading = SimpleNamespace(flags=[], physician_approval_for_icu=False)
        decisions.append(to_decision(patient.patient_id, option, target, reading))
        if option.bed_number is not None and option.action in ("place", "temporary_place"):
            taken.add((option.ward_id, option.bed_number))
    return decisions
