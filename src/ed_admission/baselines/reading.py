"""How the code baselines read a record — the structured fields only, never the free text.

  target ward     the ward the physician requested (physician_request.ward); otherwise by the chief complaint
                  (config/clinical_rules/chief_complaints.yaml) — of two wards, the one with the lower occupancy
  intensive care  a physician's request for it (binding), or NEWS2 7 or more (a recommendation, for approval)
  observation     the NEWS2 level, or a structured sign (config/clinical_rules/structured_signs.yaml)
  isolation       never — no structured field says it; reading it needs the text
A missing vital sign is flagged, never assumed normal.

Owner: integrator
"""
from dataclasses import dataclass, field

from ed_admission import config
from ed_admission.contracts.patient_record import Consciousness, PatientRecord, load_record
from ed_admission.environment.decision import Flag
from ed_admission.tools.news2 import compute_news2, level_of_care
from ed_admission.tools.rank_placements import PatientNeeds, occupancy

RECORDS = config.PROJECT / "data" / "records"


@dataclass
class Reading:
    needs: PatientNeeds
    flags: list[Flag] = field(default_factory=list)
    physician_approval_for_icu: bool = False


def _sign_holds(sign: dict, record: PatientRecord) -> bool:
    v, labs = record.vitals, record.labs
    checks = {
        "chief_complaint": lambda x: record.chief_complaint == x,
        "troponin_above": lambda x: labs.troponin is not None and labs.troponin > x,
        "supplemental_oxygen": lambda x: v.supplemental_oxygen is x,
        "glucose_at_least": lambda x: labs.glucose is not None and labs.glucose >= x,
        "glucose_below": lambda x: labs.glucose is not None and labs.glucose < x,
        "ph_below": lambda x: labs.ph is not None and labs.ph < x,
        "potassium_at_least": lambda x: labs.potassium is not None and labs.potassium >= x,
        "lactate_above": lambda x: labs.lactate is not None and labs.lactate > x,
        "temperature_at_least": lambda x: v.temperature is not None and v.temperature >= x,
        "consciousness_not_alert": lambda x: v.consciousness is not None and v.consciousness != Consciousness.ALERT,
    }
    return all(checks[key](value) for key, value in sign.items() if key != "condition")


def read_record(patient_id: str, reports: dict[str, dict]) -> Reading:
    record = load_record(RECORDS / f"{patient_id}.yaml")
    news2 = compute_news2(record.vitals)
    flags = [] if news2.complete else [Flag.MISSING_VITAL_SIGN]
    requested = record.physician_request.ward
    icu_from = config.news2_levels()["intensive_care_from"]

    if requested == "intensive_care" or news2.known_total >= icu_from:
        return Reading(PatientNeeds("intensive_care", "intensive_care", severe=True), flags,
                       physician_approval_for_icu=requested != "intensive_care")

    if requested:
        target = requested
    else:
        wards = config.chief_complaints()[record.chief_complaint]
        target = min(wards, key=lambda w: occupancy(reports[w]))
    observation = level_of_care(news2) == "observation" or any(
        _sign_holds(sign, record) for sign in config.structured_signs()["observation_signs"])
    bed_type = "observation" if observation else "regular"
    return Reading(PatientNeeds(target, bed_type, severe=observation), flags)
