"""compute_news2 — the NEWS2 score (National Early Warning Score 2) from the vital signs of a record. Pure code.

Scoring: Royal College of Physicians 2017, Chart 1 "The NEWS scoring system"; risk bands: Chart 2 "NEWS
thresholds and triggers" — both checked against the original charts on 2026-10-05 (docs/design/sources.md, S4).
Mapping the score to a level of care is our own clinical decision (config/clinical_rules/news2_levels.yaml, decision A59).

A missing vital sign is never assumed normal (trap 10): the result is marked incomplete and lists what is missing.
The points that are known are still a lower bound, so the level of care can sometimes be decided anyway —
e.g. 7 points from five vital signs already means intensive care, whatever the sixth one is.

Owner: integrator
"""
from dataclasses import dataclass, field

from ed_admission import config
from ed_admission.contracts.patient_record import Consciousness, Vitals

MAX_POINTS_PER_PARAMETER = 3        # Chart 1: no single parameter scores more than 3


def thresholds() -> dict:
    """The level-of-care thresholds — read from config, never written here a second time."""
    return config.news2_levels()


@dataclass
class News2Result:
    points: dict[str, int]                  # points per parameter that could be scored
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def total(self) -> int | None:
        """The NEWS2 score — only when every parameter is known."""
        return sum(self.points.values()) if self.complete else None

    @property
    def known_total(self) -> int:
        """The points from the parameters that are known — a lower bound on the score."""
        return sum(self.points.values())

    @property
    def red_score(self) -> bool:
        """The single-parameter threshold (3 points) reached in any one parameter."""
        return thresholds()["single_parameter_points"] in self.points.values()


def compute_news2(vitals: Vitals) -> News2Result:
    points, missing = {}, []

    def score(name, value, scorer):
        if value is None:
            missing.append(name)
        else:
            points[name] = scorer(value)

    score("respiratory_rate", vitals.respiratory_rate, _respiratory_rate)
    score("systolic_bp", vitals.systolic_bp, _systolic_bp)
    score("heart_rate", vitals.heart_rate, _heart_rate)
    score("consciousness", vitals.consciousness, lambda c: 0 if c == Consciousness.ALERT else 3)
    score("temperature", vitals.temperature, _temperature)
    score("supplemental_oxygen", vitals.supplemental_oxygen, lambda on_oxygen: 2 if on_oxygen else 0)

    if vitals.oxygen_saturation is None:
        missing.append("oxygen_saturation")
    elif not vitals.spo2_scale_2:
        points["oxygen_saturation"] = _spo2_scale_1(vitals.oxygen_saturation)
    elif vitals.oxygen_saturation <= 92:
        points["oxygen_saturation"] = _spo2_scale_2_low(vitals.oxygen_saturation)
    elif vitals.supplemental_oxygen is None:
        missing.append("oxygen_saturation")     # scale 2 at 93% or more depends on air or oxygen
    else:
        points["oxygen_saturation"] = _spo2_scale_2_high(vitals.oxygen_saturation, vitals.supplemental_oxygen)

    return News2Result(points=points, missing=missing)


def level_of_care(result: News2Result) -> str | None:
    """The level of care for a NEWS2 result, by the thresholds in config/clinical_rules/news2_levels.yaml:
    intensive care (a recommendation, for a physician's approval) · observation · regular.
    With missing parameters, decide only what the known points already prove; otherwise None — flag it."""
    intensive_care_from = thresholds()["intensive_care_from"]
    observation_from = thresholds()["observation_from"]

    if result.known_total >= intensive_care_from:
        return "intensive_care"
    if result.complete:
        return "observation" if result.total >= observation_from or result.red_score else "regular"
    if result.red_score or result.known_total >= observation_from:
        highest_possible = result.known_total + MAX_POINTS_PER_PARAMETER * len(result.missing)
        return None if highest_possible >= intensive_care_from else "observation"
    return None


# ---------------------------------------------------------------------- Chart 1, row by row

def _respiratory_rate(rate: int) -> int:          # ≤8: 3 · 9–11: 1 · 12–20: 0 · 21–24: 2 · ≥25: 3
    if rate <= 8:
        return 3
    if rate <= 11:
        return 1
    if rate <= 20:
        return 0
    if rate <= 24:
        return 2
    return 3


def _spo2_scale_1(spo2: int) -> int:               # ≤91: 3 · 92–93: 2 · 94–95: 1 · ≥96: 0
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _spo2_scale_2_low(spo2: int) -> int:           # ≤83: 3 · 84–85: 2 · 86–87: 1 · 88–92: 0
    if spo2 <= 83:
        return 3
    if spo2 <= 85:
        return 2
    if spo2 <= 87:
        return 1
    return 0


def _spo2_scale_2_high(spo2: int, on_oxygen: bool) -> int:   # ≥93 on air: 0 · on oxygen 93–94: 1 · 95–96: 2 · ≥97: 3
    if not on_oxygen:
        return 0
    if spo2 <= 94:
        return 1
    if spo2 <= 96:
        return 2
    return 3


def _systolic_bp(bp: int) -> int:                  # ≤90: 3 · 91–100: 2 · 101–110: 1 · 111–219: 0 · ≥220: 3
    if bp <= 90:
        return 3
    if bp <= 100:
        return 2
    if bp <= 110:
        return 1
    if bp <= 219:
        return 0
    return 3


def _heart_rate(pulse: int) -> int:                # ≤40: 3 · 41–50: 1 · 51–90: 0 · 91–110: 1 · 111–130: 2 · ≥131: 3
    if pulse <= 40:
        return 3
    if pulse <= 50:
        return 1
    if pulse <= 90:
        return 0
    if pulse <= 110:
        return 1
    if pulse <= 130:
        return 2
    return 3


def _temperature(celsius: float) -> int:           # ≤35.0: 3 · 35.1–36.0: 1 · 36.1–38.0: 0 · 38.1–39.0: 1 · ≥39.1: 2
    if celsius <= 35.0:
        return 3
    if celsius <= 36.0:
        return 1
    if celsius <= 38.0:
        return 0
    if celsius <= 39.0:
        return 1
    return 2
