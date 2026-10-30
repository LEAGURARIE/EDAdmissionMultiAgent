"""The case desk of the scripted days (environment/simulation_engine.py): a patient case for every admission decision,
a bed case when a bed frees while patients wait, a queue when there is no room, cases that take time while the clock
goes on, and conflicts when two open cases choose the same bed. The decider never gets a hint about who needs what.

The deciders here take simulated time through `handling_minutes`, the way the Code Coordinator's phone calls do."""
from test_decision_loop import admit, discharge, make_engine

from ed_admission.environment import BedStatus, Decision, to_clock, to_minutes
from ed_admission.environment.hospital import WaitingPatient


def slow(minutes: int, ward: str = "cardiology", beds: tuple = (3, 4)):
    """A decider that takes `minutes` per case and places each patient in the first of `beds` free when the case
    opens (one bed per patient), or tells them to wait."""
    def decide(engine, event, waiting):
        free = [b for b in beds if engine.hospital.ward(ward).bed(b).status == BedStatus.FREE]
        decisions = []
        for patient in waiting:
            if free:
                decisions.append(Decision.place(patient.patient_id, ward, free.pop(0)))
            else:
                decisions.append(Decision.wait(patient.patient_id))
        return [d.model_copy(update={"handling_minutes": minutes}) for d in decisions]
    return decide


def opened(engine, patient_id):
    return [to_clock(c["opened_at"]) for c in engine.cases if patient_id in c["patients"]]


def case_of(engine, patient_id, nth=0):
    return [c for c in engine.cases if patient_id in c["patients"]][nth]


# ---------------------------------------------------------------- patient cases and the queue

def test_an_admission_opens_a_patient_case_for_that_patient_only():
    seen = []

    def records(engine, event, waiting):
        seen.append([p.patient_id for p in waiting])
        return slow(10)(engine, event, waiting)

    engine = make_engine(records, free={"cardiology": [3, 4]})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    engine.run_to_end()
    assert seen == [["P-001"], ["P-002"]]
    assert [c["kind"] for c in engine.cases] == ["patient", "patient"]


def test_one_case_at_a_time_the_next_patient_waits_in_the_queue():
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases = 1
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")                  # arrives while P-001's case is open
    engine.run_to_end()

    assert opened(engine, "P-001") == ["09:00"]
    assert opened(engine, "P-002") == ["09:10"]      # when P-001's case closed
    p2 = case_of(engine, "P-002")
    assert p2["opened_at"] - p2["queued_at"] == 8    # minutes in the queue
    times = {p.patient_id: p.wait_minutes for p in engine.placements}
    assert times == {"P-001": 25, "P-002": 33}       # 10 deciding + 15 approval · 8 queue + 10 + 15


def test_two_cases_at_a_time_both_open_at_once():
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases = 2
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    engine.run_to_end()

    assert opened(engine, "P-001")[0] == "09:00" and opened(engine, "P-002")[0] == "09:02"   # both open at once


def test_no_limit_every_case_opens_at_once():
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases = None
    for i, clock in enumerate(["09:00", "09:01", "09:02"], start=1):
        admit(engine, clock, f"P-00{i}")
    engine.run_to_end()

    assert [opened(engine, f"P-00{i}")[0] for i in (1, 2, 3)] == ["09:00", "09:01", "09:02"]


# ---------------------------------------------------------------- holding the bed, and conflicts

def test_held_when_the_case_closes_two_open_cases_choose_the_same_bed():
    """P-001 and P-002 both see bed 3 free; P-001 closes first and takes it; P-002 finds it taken — a conflict —
    and gets a new patient case, which gives it bed 4."""
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases, engine.hold_bed_when = 2, "close"
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    engine.run_to_end()

    assert case_of(engine, "P-002")["conflicts"] == ["P-002"] and case_of(engine, "P-002", 1)["conflicts"] == []
    assert opened(engine, "P-002") == ["09:02", "09:12"]
    assert {(p.patient_id, p.bed_number) for p in engine.placements} == {("P-001", 3), ("P-002", 4)}
    assert any("CONFLICT" in entry.message for entry in engine.log)


def test_held_when_the_decider_answers_no_conflict():
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases, engine.hold_bed_when = 2, "start"
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    engine.run_to_end()

    assert not any(c["conflicts"] for c in engine.cases)
    assert {(p.patient_id, p.bed_number) for p in engine.placements} == {("P-001", 3), ("P-002", 4)}


def test_one_case_at_a_time_never_conflicts():
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases, engine.hold_bed_when = 1, "close"
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    engine.run_to_end()

    assert not any(c["conflicts"] for c in engine.cases)


# ---------------------------------------------------------------- bed cases: who gets a freed bed

def test_a_freed_bed_opens_a_bed_case_with_every_waiting_patient():
    seen = []

    def records(engine, event, waiting):
        seen.append((event.event_type.value, [p.patient_id for p in waiting]))
        return slow(0)(engine, event, waiting)

    engine = make_engine(records, free={})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:30", "P-002")
    discharge(engine, "10:00", "cardiology", 3)      # clean at 10:45
    engine.run_to_end()

    assert seen[-1] == ("cleaning_done", ["P-001", "P-002"])
    assert case_of(engine, "P-002", 1)["kind"] == "bed"


def test_the_bed_case_gives_no_hint_about_who_needs_what():
    """The decider gets the patients' ids and admission decision times — nothing else: who is severe it must find
    out from the records. The waiting list is in admission time order, and the decider may choose anyone."""
    def reads_the_records(engine, event, waiting):
        for patient in waiting:
            assert set(type(patient).model_fields) == {"patient_id", "decided_at", "awaiting_approval"}
            assert isinstance(patient, WaitingPatient) and not patient.awaiting_approval
        if event.event_type.value != "cleaning_done":
            return [Decision.wait(p.patient_id) for p in waiting]
        severe = "P-002"                                        # what the decider found in the records
        return [Decision.place(severe, "cardiology", 3)] + [Decision.wait(p.patient_id) for p in waiting
                                                              if p.patient_id != severe]

    engine = make_engine(reads_the_records, free={})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:30", "P-002")
    discharge(engine, "10:00", "cardiology", 3)
    engine.run_to_end()

    assert [p.patient_id for p in engine.placements] == ["P-002"]      # the later admission — the decider's choice
    assert [p.patient_id for p in engine.hospital.emergency_queue] == ["P-001"]


def test_a_patient_in_an_open_case_is_not_in_a_bed_case():
    seen = []

    def records(engine, event, waiting):
        seen.append([p.patient_id for p in waiting])
        minutes = 30 if [p.patient_id for p in waiting] == ["P-002"] else 0
        return [Decision.wait(p.patient_id).model_copy(update={"handling_minutes": minutes}) for p in waiting]

    engine = make_engine(records, free={})
    engine.concurrent_cases = None
    admit(engine, "09:00", "P-001")
    admit(engine, "10:30", "P-002")                  # its case is open 10:30-11:00
    discharge(engine, "10:00", "cardiology", 3)      # clean at 10:45 — while P-002's case is open
    engine.run_to_end()

    assert ["P-001"] in seen and ["P-001", "P-002"] not in seen


def test_a_patient_told_to_wait_is_in_the_bed_case_when_a_bed_frees():
    engine = make_engine(slow(0), free={})
    admit(engine, "09:00", "P-001")
    discharge(engine, "10:00", "cardiology", 3)      # clean at 10:45
    engine.run_to_end()

    assert opened(engine, "P-001") == ["09:00", "10:45"]
    assert engine.placements[0].bed_number == 3


def test_an_escalation_applies_when_the_case_closes():
    def escalates(engine, event, waiting):
        return [Decision.escalate(waiting[0].patient_id, "no safe bed").model_copy(update={"handling_minutes": 7})]

    engine = make_engine(escalates, free={})
    admit(engine, "09:00", "P-001")
    engine.run_to_end()

    [escalation] = engine.escalations
    assert escalation.escalated_at == to_minutes("09:07")
    assert engine.hospital.emergency_queue == []


def test_a_day_may_set_its_own_case_desk(tmp_path):
    from ed_admission.environment import load_scenario
    day = tmp_path / "day.yaml"
    day.write_text('name: test\nday: Monday\nstart_time: "08:00"\nconcurrent_cases: 3\nevents: []\n', encoding="utf8")
    assert load_scenario(day).concurrent_cases == 3


# ---------------------------------------------------------------- the case desk in the metrics

def test_the_day_metrics_count_the_queue_the_open_cases_and_the_conflicts():
    from ed_admission.evaluation.metrics import case_desk
    engine = make_engine(slow(10), free={"cardiology": [3, 4]})
    engine.concurrent_cases, engine.hold_bed_when = 2, "close"
    admit(engine, "09:00", "P-001")
    admit(engine, "09:02", "P-002")
    admit(engine, "09:03", "P-003")                  # no room: waits for a case
    engine.run_to_end()

    desk = case_desk(engine)
    assert desk["concurrent_cases"] == 2 and desk["most_open_cases"] == 2
    assert desk["conflicts"] >= 1 and desk["max_minutes_in_queue"] >= 7      # P-003: 09:03 -> 09:10
    assert desk["mean_minutes_per_case"] == 10


def test_cases_that_take_no_time_are_never_open_at_once():
    from ed_admission.evaluation.metrics import case_desk
    engine = make_engine(slow(0), free={"cardiology": [3, 4]})
    admit(engine, "09:00", "P-001")
    admit(engine, "09:00", "P-002")
    engine.run_to_end()
    assert case_desk(engine)["most_open_cases"] == 1


# ---------------------------------------------------------------- a bed that was not free: the decider's error

def stubborn(engine, event, waiting):
    """Always chooses cardiology bed 5 — occupied all day (an inpatient)."""
    return [Decision.place(p.patient_id, "cardiology", 5) for p in waiting]


def test_a_bed_that_was_not_free_is_an_error_one_more_case_then_escalated():
    engine = make_engine(stubborn, free={})
    admit(engine, "09:00", "P-001")
    engine.run_to_end()

    assert [c["bed_errors"] for c in engine.cases] == [["P-001"], ["P-001"]]
    assert not any(c["conflicts"] for c in engine.cases)              # an error, not a conflict
    [escalation] = engine.escalations
    assert "not free, twice" in escalation.reason
    assert engine.hospital.ward("cardiology").bed(5).status == BedStatus.OCCUPIED   # the inpatient stays


def test_held_when_the_decider_answers_a_bed_that_was_not_free_does_not_break_the_engine():
    engine = make_engine(stubborn, free={})
    engine.hold_bed_when = "start"
    admit(engine, "09:00", "P-001")
    engine.run_to_end()
    assert len(engine.escalations) == 1


def test_the_day_metrics_count_bed_errors():
    from ed_admission.evaluation.metrics import case_desk
    engine = make_engine(stubborn, free={})
    admit(engine, "09:00", "P-001")
    engine.run_to_end()
    assert case_desk(engine)["bed_errors"] == 2


def test_a_day_that_contradicts_itself_never_runs(tmp_path):
    import pytest
    from ed_admission.environment import load_scenario
    day = tmp_path / "day.yaml"
    day.write_text('name: t\nday: Monday\nstart_time: "08:00"\nfree_at_start:\n  cardiology: [4]\nevents:\n'
                   '  - {time: "09:00", type: discharge, ward: cardiology, bed: 4}\n', encoding="utf8")
    with pytest.raises(ValueError, match="contradicts itself"):
        load_scenario(day)


# ---------------------------------------------------------------- who waits together is handed together

def test_patients_admitted_in_the_same_minute_come_in_one_case():
    seen = []

    def records(engine, event, waiting):
        seen.append([p.patient_id for p in waiting])
        return [Decision.wait(p.patient_id) for p in waiting]

    engine = make_engine(records, free={})
    admit(engine, "10:00", "P-057")
    admit(engine, "10:00", "P-050")
    engine.run_to_end()
    assert seen == [["P-050", "P-057"]] and engine.cases[0]["kind"] == "patients"


def test_patients_who_pile_up_while_the_desk_is_busy_come_in_one_case():
    seen = []

    def records(engine, event, waiting):
        seen.append([p.patient_id for p in waiting])
        return [Decision.wait(p.patient_id).model_copy(update={"handling_minutes": 10}) for p in waiting]

    engine = make_engine(records, free={})
    engine.concurrent_cases = 1
    admit(engine, "09:00", "P-001")
    admit(engine, "09:03", "P-002")                  # the desk is busy until 09:10
    admit(engine, "09:05", "P-003")
    engine.run_to_end()
    assert seen == [["P-001"], ["P-002", "P-003"]]
