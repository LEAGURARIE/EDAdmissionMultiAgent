# Scenarios — scripted days

Each file is one simulated day: the starting state of the beds, then a timed list of events.

| Event | What it does |
|---|---|
| `admission_decision` | a patient joins the Emergency Department queue — patient id only |
| `discharge` | the bed goes to cleaning (45 minutes). `expected_time`: the ward announces it as a discharge today. `in_report: false`: the ward frees the bed without updating the system — the report keeps showing it occupied |
| `ward_note` / `ward_note_end` | a ward's private note starts / ends — only that ward's Department Agent reads it. `effects`: what the note means for the beds (`environment/ward_truth.py`) — never shown to a decider; a freed bed not yet updated needs none, it is really free |

| File | Day |
|---|---|
| `busy_day.yaml` | Monday 08:00-20:00 — 15 admissions, a shortage of observation beds, intensive care full by evening |
| `evening_night.yaml` | Thursday 18:00 to Friday 08:00 — 12 admissions, almost no discharges, staffing limits at night |
| `weekend.yaml` | Friday 14:00 to Saturday 22:00 — 12 admissions, few discharges, freed beds not updated until Sunday |

Times after midnight are written like `01:45 +1d`; the day of the week in the ward report moves on with them.
No needs, severity or expected answers here — patients' details come from `data/records/`. The days have no
expected answers: they measure hard-rule violations, time to a bed and escalations. Synthetic data only.
