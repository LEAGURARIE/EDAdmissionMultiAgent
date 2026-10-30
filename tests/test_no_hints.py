"""No hints reach a decider: what the environment hands it and what its tools return hold no expected answer, no
patient type, no needs that were not read from the record, and no ward's true state. Checked on every pair, through
every tool the single agent may use, and on the scripted days' cases."""
import json
from pathlib import Path

import pytest
import yaml

from ed_admission.contracts.negotiation import WardRequest
from ed_admission.environment import load_scenario, load_template
from ed_admission.environment.hospital import WaitingPatient
from ed_admission.tools.negotiation import Negotiation
from ed_admission.tools.rank_placements import PatientNeeds
from ed_admission.tools.toolbox import Toolbox

PROJECT = Path(__file__).resolve().parents[1]
# Words that belong to the answers or the ward's true state — never in what a decider sees.
FORBIDDEN = ['"patient_type"', '"challenge"', '"traps"', '"expected"', '"rationale"', '"acceptable_wards"',
             '"needs"', "note_effects", "hidden_free", "discharge_not_today", "discharge_within_hour",
             "only_own_patients", '"unavailable"', "held_for", "inpatient-", "reports_alone_would_give", "answer_key"]
PAIRS = [(p["case_id"], p["template"], p["target_ward"]) for name in ("pairs.yaml", "pairs_set2.yaml")
         for p in yaml.safe_load((PROJECT / "data" / "answer_key" / name).read_text(encoding="utf8"))["pairs"]]


def leaks(value) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, default=lambda v: getattr(v, "__dict__", str(v)))
    return [word for word in FORBIDDEN if word in text]


@pytest.mark.parametrize("case_id, template, target", PAIRS[::7])       # a sample of pairs from every template
def test_the_single_agents_tools_return_no_hint(case_id, template, target):
    engine = load_template(PROJECT / "data" / "templates" / f"{template}.yaml", target_ward=target)
    tools = Toolbox("single_agent", engine, negotiation=Negotiation(engine, {}))
    seen = {
        "record": tools.read_patient_record(case_id),
        "news2": tools.compute_news2(case_id),
        "lists": tools.clinical_lists(),
        "reports": tools.all_department_status(),
        "ranking": tools.rank_placements(PatientNeeds(target, "regular", severe=False)).options,
        "wards": [tools.department_view(ward) for ward in engine.hospital.wards],   # notes as text, no effects
    }
    assert {name: leaks(value) for name, value in seen.items() if leaks(value)} == {}


def test_a_ward_request_carries_needs_only():
    assert set(WardRequest.model_fields) == {"bed_type", "severe", "observation", "isolation", "target_ward",
                                             "proposed_bed"}


def test_a_waiting_patient_is_an_id_and_a_time():
    assert set(WaitingPatient.model_fields) == {"patient_id", "decided_at", "awaiting_approval"}


@pytest.mark.parametrize("day", ["busy_day", "evening_night", "weekend", "competing_for_a_bed"])
def test_on_a_scripted_day_the_decider_gets_ids_and_times_only(day):
    """Every case of the day — patient cases and bed cases — hands the decider the event and the waiting patients;
    none carries a ward note's text or effects, or anything about the patient beyond its id and time."""
    handed = []

    def records(engine, event, waiting):
        handed.append((event, waiting))
        return []

    engine = load_scenario(PROJECT / "data" / "scenarios" / f"{day}.yaml")
    engine.policy = records
    engine.run_to_end()
    assert handed
    for event, waiting in handed:
        assert event.text is None and event.effects == []
        assert all(set(p.model_dump()) == {"patient_id", "decided_at", "awaiting_approval"} for p in waiting)
