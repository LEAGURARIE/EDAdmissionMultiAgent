"""What a ward knows and tells about itself — the only view of the beds a decider gets.

The report holds: bed counts by type and status · free beds by number · beds being cleaned and when each will be
ready (cleaning always takes the same time, so the ward knows) · beds expected to free today — without the hour,
which nobody knows in advance · the day of the week and the time · the shift (morning, evening, night) and
whether it is the weekend · for each discharge expected today, whether it is likely within the hour (only in
a weekday morning shift — the shift rule).
It never holds: patient identifiers · for whom or until when a bed is held · the actual discharge time · a bed the
ward freed but has not updated in the system yet (it is shown occupied — only the ward's private note tells).

Owner: Lea Gur-Arie
"""
from ed_admission.environment.events import MINUTES_PER_DAY, to_clock
from ed_admission.environment.hospital import BedStatus, BedType, Ward
from ed_admission.environment.shifts import discharge_in_report_is_waited_for, shift, weekend


def ward_report(ward: Ward, clock: int, day: str | None) -> dict:
    today = clock // MINUTES_PER_DAY
    report = {"ward_id": ward.ward_id, "name": ward.name, "level": ward.level.value,
              "day": day, "time": to_clock(clock), "shift": shift(clock), "weekend": weekend(day, clock)}

    shown = {bed.number: bed.status if bed.in_report else BedStatus.OCCUPIED for bed in ward.beds}
    for bed_type in BedType:
        if ward.total(bed_type):
            counts = {status.value: sum(1 for b in ward.beds if b.bed_type == bed_type and shown[b.number] == status)
                      for status in BedStatus}
            report[bed_type.value] = counts | {"total": ward.total(bed_type)}

    report["free_beds"] = [
        {"bed": bed.number, "bed_type": bed.bed_type.value}
        for bed in ward.beds if shown[bed.number] == BedStatus.FREE
    ]
    report["cleaning_beds"] = [
        {"bed": bed.number, "bed_type": bed.bed_type.value, "ready_about": to_clock(bed.cleaning_until)}
        for bed in ward.beds if shown[bed.number] == BedStatus.CLEANING
    ]
    likely = discharge_in_report_is_waited_for(day, clock)     # the shift rule, in placement_rules.yaml
    report["expected_discharges_today"] = [
        {"bed": bed.number, "bed_type": bed.bed_type.value, "likely_within_hour": likely}
        for bed in ward.beds
        if bed.status == BedStatus.OCCUPIED
        and bed.expected_discharge_at is not None
        and bed.expected_discharge_at // MINUTES_PER_DAY == today
    ]
    return report
