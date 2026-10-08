"""Baseline 3: "simulated coordinator" — today's procedure, the shift manager without the system. Every metric of the
system is compared with it: safe, suitable and fast.

Like a busy shift manager, it:
  1. reads the record's structured fields — not the free text (reading.py);
  2. sees every ward's report;
  3. phones the wards, the target ward first and then down the ladder (5 minutes a call); a ward answers from its
     true state — a bed that can take this patient, or none (environment/ward_truth.py). It learns that a bed is
     free though not updated, or that a free bed is not really available — but not a ward's conditions in words;
  4. decides by the same rules (rank_placements on what the wards answered), and the shift manager approves.
A non-severe patient may wait for a bed in cleaning or a discharge the ward confirms within the hour; after an
hour with no bed on the ladder, escalate. Waiting patients: severe first, then the earlier admission decision.

Owner: integrator
"""
from ed_admission import config
from ed_admission.baselines.reading import read_record
from ed_admission.baselines.rules_only import to_decision, waited_too_long
from ed_admission.environment.decision import Decision
from ed_admission.environment.events import Event
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.environment.shifts import discharge_in_report_is_waited_for
from ed_admission.environment.ward_report import ward_report
from ed_admission.environment.ward_truth import true_state
from ed_admission.tools.rank_placements import Option, rank_placements

CALL_MINUTES = config.operations()["phone_call_minutes"]


def _answers(engine, wards: list[str], target: str) -> dict[str, dict]:
    """What the called wards say: their true state, in the shape of a report. A freed bed not yet updated counts
    as free; a discharge counts as likely only if it will happen."""
    real = true_state(engine, patient_target=target)
    said = getattr(real, "note_discharges", set())
    answers = {}
    for ward_id in wards:
        ward = real.hospital.ward(ward_id)
        for bed in ward.beds:
            bed.in_report = True
        report = ward_report(ward, real.clock, real.today())
        weekday_morning = discharge_in_report_is_waited_for(real.today(), real.clock)
        for discharge in report["expected_discharges_today"]:
            discharge["likely_within_hour"] = weekday_morning or (ward_id, discharge["bed"]) in said
        answers[ward_id] = report
    return answers


def simulated_coordinator(engine, event: Event, waiting: list[WaitingPatient]) -> list[Decision]:
    reports = {ward: engine.department_status(ward) for ward in engine.hospital.wards}
    readings = {p.patient_id: read_record(p.patient_id, reports) for p in waiting}
    order = sorted(waiting, key=lambda p: (not readings[p.patient_id].needs.severe, p.decided_at))
    taken, decisions = set(), []
    for patient in order:
        reading = readings[patient.patient_id]
        needs = reading.needs
        ladder = [needs.target_ward] if needs.bed_type == "intensive_care" else \
            [w for level in config.ladders()[needs.target_ward] for w in level]
        calls, option = 0, None
        for reach in range(1, len(ladder) + 1):                     # call one more ward each time
            calls = reach
            known = dict(reports) | _answers(engine, ladder[:reach], needs.target_ward)
            best = rank_placements(known, needs, taken).best
            if best.action != "escalate" and (best.ward_id in ladder[:reach]):
                option = best
                break
        if option is None:
            option = rank_placements(dict(reports) | _answers(engine, ladder, needs.target_ward), needs, taken).best
        if option.action == "wait" and waited_too_long(engine, patient):
            option = Option("escalate", reason="waited an hour and no bed on the ladder")
        if option.bed_number is not None and option.action != "wait":
            taken.add((option.ward_id, option.bed_number))
        decisions.append(to_decision(patient.patient_id, option, needs.target_ward, reading,
                                     handling_minutes=calls * CALL_MINUTES))
    return decisions
