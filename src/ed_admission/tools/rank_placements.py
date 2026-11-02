"""rank_placements — code, not a language model. The rules below are written here (DECISIONS.md, Placement).

Works on the ward reports only — what every decider may see. Given the patient's needs and target ward:
1. Hard rules only: level of care; observation need → observation bed; isolation need → isolation room
   (isolation + observation → isolation room); a non-severe patient never in an observation bed; intensive care
   only for a patient who needs it.
2. Rank by the target ward's proximity ladder (config/clinical_rules/proximity_ladder.yaml); two wards on the same
   level → lower occupancy first.
3. Time to bed: a non-severe patient waits up to one hour for a bed in the target ward — a bed in cleaning; a
   discharge the report marks likely_within_hour (a weekday morning shift). A severe patient never waits.
4. An observation-only patient gets an isolation room only as a last resort (no observation bed on the whole ladder).
5. Ladder level 3 or farther needs a typed reason with the shift manager's approval (Decision.needs_typed_reason).
6. No safe option → escalate.
What a ward says in the negotiation (a bed that frees soon, a bed not really available) is not in the report: the
Coordinator Agent weighs it over this ranking, and the Code Coordinator baseline learns it by phone.

Returns every option in order — the first is the recommendation — and a reason for every ward left out.

Owner: Lea Gur-Arie
"""
from dataclasses import dataclass, field

from ed_admission import config

BED_FOR_NEED = {"regular": "regular", "observation": "observation", "isolation": "isolation",
                "intensive_care": "intensive_care"}


@dataclass
class PatientNeeds:
    target_ward: str
    bed_type: str                    # regular · observation · isolation · intensive_care
    severe: bool                     # observation or intensive care: never waits


@dataclass
class Option:
    action: str                      # place · temporary_place · wait · escalate
    ward_id: str | None = None
    bed_number: int | None = None
    bed_type: str | None = None
    ladder_level: int | None = None
    wait_because: str | None = None  # cleaning · discharge_today
    reason: str = ""


@dataclass
class Ranking:
    options: list[Option]
    excluded: dict[str, str] = field(default_factory=dict)     # ward -> why it is not an option

    @property
    def best(self) -> Option:
        return self.options[0]


def occupancy(report: dict) -> float:
    """(occupied + held + cleaning) / all beds, from the report's counts."""
    counts = [v for k, v in report.items() if isinstance(v, dict) and "total" in v]
    busy = sum(c["occupied"] + c["held"] + c["cleaning"] for c in counts)
    return busy / sum(c["total"] for c in counts)


INTERNAL_MEDICINE = ("internal_medicine_a", "internal_medicine_b")


def resolve_target_ward(target_ward: str, reports: dict[str, dict]) -> str:
    """"internal_medicine" (the record names neither A nor B) -> the one with the lower occupancy, A on a tie.
    Any other ward is returned as it is."""
    if target_ward != "internal_medicine":
        return target_ward
    return min(INTERNAL_MEDICINE, key=lambda ward: occupancy(reports[ward]))


def _free(report: dict, bed_type: str, taken: set) -> list[int]:
    return [b["bed"] for b in report["free_beds"]
            if b["bed_type"] == bed_type and (report["ward_id"], b["bed"]) not in taken]


def rank_placements(reports: dict[str, dict], needs: PatientNeeds, taken: set = frozenset()) -> Ranking:
    """`reports`: every ward's report, by ward id. `taken`: (ward, bed) already given to another patient now."""
    ranking = Ranking(options=[])
    bed_type = needs.bed_type

    if bed_type == "intensive_care":
        free = _free(reports["intensive_care"], "intensive_care", taken)
        if free:
            ranking.options.append(Option("place", "intensive_care", free[0], bed_type, 1, reason="intensive care bed free"))
        else:
            ranking.excluded["intensive_care"] = "no free intensive care bed"
        ranking.options.append(Option("escalate", reason="no safe option: intensive care is full"))
        return ranking

    target, levels = needs.target_ward, config.ladders()[needs.target_ward]
    free = _free(reports[target], bed_type, taken)
    if free:
        ranking.options.append(Option("place", target, free[0], bed_type, 1, reason=f"{bed_type} bed free in the target ward"))
    else:
        ranking.excluded[target] = f"no free {bed_type} bed in the target ward"
        if not needs.severe:
            cleaning = [b for b in reports[target]["cleaning_beds"] if b["bed_type"] == bed_type]
            likely = [d for d in reports[target]["expected_discharges_today"]
                      if d["bed_type"] == bed_type and d["likely_within_hour"]]
            if cleaning:
                ranking.options.append(Option("wait", target, cleaning[0]["bed"], wait_because="cleaning",
                                              reason="not severe — waits for a bed in cleaning in the target ward"))
            elif likely:
                ranking.options.append(Option("wait", target, wait_because="discharge_today",
                                              reason="not severe — a discharge is likely within the hour"))

    for number, wards in enumerate(levels[1:], start=2):
        with_bed = [w for w in wards if _free(reports[w], bed_type, taken)]
        for ward in set(wards) - set(with_bed):
            ranking.excluded.setdefault(ward, f"no free {bed_type} bed")
        for ward in sorted(with_bed, key=lambda w: occupancy(reports[w])):
            ranking.options.append(Option("temporary_place", ward, _free(reports[ward], bed_type, taken)[0], bed_type,
                                          number, reason=f"no {bed_type} bed in the target ward — ladder level {number}"))

    if bed_type == "observation" and not any(o.action in ("place", "temporary_place") for o in ranking.options):
        for number, wards in enumerate(levels, start=1):              # last resort: an isolation room
            for ward in sorted([w for w in wards if _free(reports[w], "isolation", taken)],
                               key=lambda w: occupancy(reports[w])):
                ranking.options.append(Option("place" if number == 1 else "temporary_place", ward,
                                              _free(reports[ward], "isolation", taken)[0], "isolation", number,
                                              reason="no observation bed on the ladder — isolation room as a last resort"))

    ranking.options.append(Option("escalate", reason=f"no safe option: no {bed_type} bed on the ladder"))
    return ranking
