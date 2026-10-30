# Data dictionary

Every data file of the project, and every field in it: name, meaning, type, unit and allowed values.

**All the data is synthetic.** It was written for this project: no real patient, no real hospital, no external
source. The free text is in Hebrew with English medical terms, like real notes in an Israeli emergency department.
Nothing was cleaned or transformed: every file is read as it is, and checked against a contract in code when it is
loaded.

| Data | Folder | Files | Who reads it |
|---|---|---|---|
| Patient records | `data/records/` | 74 | The system — only through the `read_patient_record` tool |
| Ward-state templates | `data/templates/` | 22 | The environment only. The system sees the ward reports built from them |
| Scripted days | `data/scenarios/` | 4 | The environment only |
| Expected answers | `data/answer_key/` | 117 | **The evaluation code only** — a test fails if any other code refers to it |
| The hospital and the clinical rules | `config/` | 8 | The environment, the tools and the baseline |
| Run history (results, never in Git) | `outputs/` | 2 | The person who runs the measurement |

Identifiers: a record is `P-001` … `P-074` · a template is `T1` … `T22` · a paired case is `PC-1` … `PC-7` · a ward is
one of `internal_medicine_a`, `internal_medicine_b`, `cardiology`, `general_surgery`, `intensive_care`.

---

## 1. Patient records — `data/records/P-<nnn>.yaml`

One file per patient: the emergency department record, frozen at the moment the physician decides to admit. It holds
facts only — no expected answer, no severity, no needs, and no clock times. Contract:
`src/ed_admission/contracts/patient_record.py`; an unknown field is rejected.

| Field | Meaning | Type | Unit / allowed values |
|---|---|---|---|
| `case_id` | The record's identifier | text | `P-` and three digits |
| `patient.age` | Age — adults only | integer | years, 18–110 |
| `patient.sex` | Sex | text | `male` · `female` |
| `triage_level` | The triage nurse's urgency level | integer | 1–5, 1 is the most urgent |
| `chief_complaint` | The main complaint, chosen by the triage nurse from a closed list | text | one of the 11 complaints in `config/clinical_rules/chief_complaints.yaml` |
| `vitals.respiratory_rate` | Respiratory rate | integer or empty | breaths per minute, 4–60 |
| `vitals.oxygen_saturation` | Oxygen saturation | integer or empty | percent, 50–100 |
| `vitals.supplemental_oxygen` | Whether the patient is on supplemental oxygen | true / false or empty | |
| `vitals.spo2_scale_2` | Whether NEWS2 saturation scale 2 applies (target 88–92%, e.g. hypercapnic COPD) | true / false | default false |
| `vitals.systolic_bp` | Systolic blood pressure | integer or empty | mmHg, 40–260 |
| `vitals.heart_rate` | Heart rate | integer or empty | beats per minute, 20–250 |
| `vitals.consciousness` | Level of consciousness, on the NEWS2 scale | text or empty | `alert` · `confusion` (new confusion) · `voice` · `pain` · `unresponsive` |
| `vitals.temperature` | Body temperature | number or empty | °C, 32–43 |
| `labs.wbc` | White blood cells | number or missing | 10³ per microliter, 0–200 |
| `labs.hemoglobin` | Hemoglobin | number or missing | g/dL, 2–25 |
| `labs.platelets` | Platelets | number or missing | 10³ per microliter, 0–2000 |
| `labs.sodium` | Sodium | number or missing | mmol/L, 100–180 |
| `labs.potassium` | Potassium | number or missing | mmol/L, 1–10 |
| `labs.creatinine` | Creatinine | number or missing | mg/dL, 0.1–20 |
| `labs.glucose` | Glucose | number or missing | mg/dL, 10–2000 |
| `labs.crp` | C-reactive protein | number or missing | mg/L, 0–600 |
| `labs.troponin` | High-sensitivity troponin | number or missing | ng/L, 0–100000 |
| `labs.lactate` | Lactate | number or missing | mmol/L, 0–30 |
| `labs.ph` | Blood pH | number or missing | 6.5–7.8 |
| `labs.pco2` | Partial pressure of carbon dioxide | number or missing | mmHg, 10–150 |
| `labs.inr` | International normalized ratio | number or missing | 0.5–15 |
| `background_conditions` | Background diseases, as written — abbreviations included | free text | Hebrew and English |
| `medications` | Regular medications | list of text | |
| `allergies` | Known allergies | list of text | |
| `functional_status` | Functional status | text or empty | `independent` · `partly_dependent` · `dependent` |
| `physician_request.ward` | The ward the physician requested, in the structured field | text or empty | a ward identifier |
| `triage_note` | The triage nurse's short note | free text or empty | Hebrew |
| `physician_note` | The emergency department physician's summary | free text, required | Hebrew with English medical terms |

Notes:

- **A vital sign may be empty on purpose.** Some records leave a field empty and give the value only in the text, and
  some leave it missing altogether. A value that is present must be inside the range above.
- **A field and the text may disagree on purpose** (for example a saturation of 95 in the field and "dropped to 88%"
  in the text). The text is part of the data, not a mistake to clean.
- The record never says what the patient needs. That is in the expected answers only.

---

## 2. Ward-state templates — `data/templates/T<n>.yaml`

One file per template: the hospital at one moment — the day, the time, and what every ward has. Every bed that is not
listed is occupied. Loader: `src/ed_admission/environment/template_loader.py`. Design:
`docs/design/ward_state_templates.md`.

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `id` | The template's identifier | text | `T1` … `T22` |
| `name` | A short name of the dilemma | text | Hebrew |
| `day` | Day of the week | text | `Sunday` … `Saturday` |
| `time` | Time of day | text | `HH:MM` |
| `relative_to` | How the wards are written | text | `target_ward` — by ladder level, so the same dilemma fits any target ward · `none` — the wards are named |
| `default` | The state of every regular ward that is not written otherwise | ward state | see below |
| `ladder.level_<n>` | The state of the wards on ladder level n of the patient's target ward; level 1 is the target ward (relative templates) | ward state | |
| `wards.<ward>` | The state of a named ward (fixed templates) | ward state | |
| `intensive_care` | The state of the intensive care unit | ward state | |

A **ward state** holds:

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `regular_free` | Free regular beds | integer | 0–10 |
| `observation_free` | Free observation beds | integer | 0–2 |
| `intensive_care_free` | Free intensive care beds (intensive care only) | integer | 0–6 |
| `isolation_free` | Whether the ward's isolation room is free | true / false | |
| `cleaning` | Beds being cleaned: `bed_type`, and `ready_in` — minutes until ready | list | cleaning always takes 45 minutes |
| `held` | Beds held for a patient who does not come from the emergency department: `bed_type` | list | shown in the report as unavailable |
| `discharges_today` | Occupied beds the ward expects to free today, without the hour: `bed_type` | list | |
| `notes` | The ward's private notes — what only that ward knows | list of free text | Hebrew |
| `note_effects` | What each note means for the beds — the ward's true state | list | see below |

`note_effects` — each item has `effect` and, where it applies, `bed_type`
(`regular` · `observation` · `isolation` · `intensive_care`):

| `effect` | Meaning |
|---|---|
| `unavailable` | The free beds of this type cannot take a patient now — staffing, broken, promised |
| `hidden_free` | The bed the note names (`bed: <number>`, the same number as in the note's text) is free, but the report still shows it occupied |
| `discharge_not_today` | The discharges the report expects today will not happen |
| `discharge_within_hour` | One bed of this type frees within about an hour, and is not in the report |
| `cleaning_long` | The beds in cleaning will not be ready within the hour |
| `only_own_patients` | The ward takes only patients whose target ward it is |

**Who sees what:** the system sees only the ward report built from the counts. A Department Agent also sees the text
of its own ward's `notes`. Nobody but the environment and the evaluation code reads `note_effects`.

---

## 3. Scripted days — `data/scenarios/<day>.yaml`

One file per simulated day: the starting state of the beds, then a timed list of events. Loader:
`src/ed_admission/environment/scenario_loader.py`. The days have no expected answer for each decision; they measure
hard-rule violations, time to a bed and escalations.

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `name` | The day's identifier | text | `busy_day` · `evening_night` · `weekend` · `competing_for_a_bed` |
| `description` | What the day is like | text | |
| `day` | The day of the week the scenario starts on | text | `Sunday` … `Saturday` |
| `start_time` | The time the scenario starts | text | `HH:MM` |
| `concurrent_cases` | Cases the desk may hold open at once, for this day (optional; default in `config/hospital.yaml`) | integer or empty | 1 or more; empty = no limit |
| `free_at_start.<ward>` | The beds that are free at the start, by number; every other bed is occupied | list of integers | regular wards: 1–10 regular, 11–12 observation, 13 the isolation room · intensive care: 1–6, 7 the isolation room |
| `events` | The events of the day, in time order | list | see below |

Each event:

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `time` | When it happens | text | `HH:MM`, and `HH:MM +1d` after midnight |
| `type` | The kind of event | text | `admission_decision` · `discharge` · `ward_note` · `ward_note_end` |
| `patient` | The admitted patient (`admission_decision`) — the identifier only | text | a record identifier |
| `ward` | The ward (`discharge`, `ward_note`, `ward_note_end`) | text | a ward identifier |
| `bed` | The bed that is freed (`discharge`); it goes to cleaning for 45 minutes | integer | a bed number |
| `expected_time` | When the ward announced the discharge (`discharge`) — the report shows it as a discharge expected today | text | `HH:MM` |
| `in_report` | `false`: the ward frees the bed without updating the system, so the report keeps showing it occupied (`discharge`) | true / false | default true |
| `text` | The private note that starts or ends (`ward_note`, `ward_note_end`) | free text | Hebrew |
| `effects` | What the note means for the beds (`ward_note`) — as `note_effects` in a template | list | |

---

## 4. Expected answers — `data/answer_key/`

The ruler the system is measured with. Clinically approved and frozen. Built by
`src/ed_admission/evaluation/answer_key_builder.py`; a frozen answer is never overwritten.

### `pairs.yaml`, `pairs_set2.yaml` — which record is placed in which template

A case is a pair: one record in one template. 60 pairs in the first file, 44 in the second.

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `pairs[].case_id` | The record | text | a record identifier |
| `pairs[].template` | The template it is placed in | text | a template identifier |
| `pairs[].target_ward` | The record's true target ward — needed to build a relative template | text | a ward identifier |

### `dev_test_sets.yaml` — the development and test sets of the comparison

Frozen on 2026-10-15, before any paid run; built by `src/ed_admission/evaluation/sets.py`.

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `frozen` | When the sets were frozen | date | 2026-10-15 |
| `dev[]`, `test[]` | The pairs of each set: `case_id` and `template` | list | 22 pairs each, one per template |

### `paired_cases.yaml` — two patients, one suitable bed

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `cases[].id` | The paired case | text | `PC-1` … `PC-7` |
| `cases[].template` | The template both patients are placed in | text | a template identifier |
| `cases[].patients[].case_id` | The record | text | a record identifier |
| `cases[].patients[].target_ward` | Its true target ward | text | a ward identifier |
| `cases[].patients[].decided_minutes_before` | How long before the decision moment the admission was decided | integer | minutes |

### `clinical_inputs.yaml` — what each record really is

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `cases.<record>.patient_type` | The true patient type | text | `A` not severe · `B` severe, observation · `C` isolation · `D` isolation and observation · `E` intensive care |
| `cases.<record>.challenge` | The patient-side challenge planted in the record | integer | 1 clear · 2 hidden severity · 3 misleading complaint · 4 looks severe, is not · 5 a physician's request, or an injection · 6 a judgment situation · 7 looks like isolation, is not |
| `cases.<record>.situation` | The judgment situation (challenge 6) | text or missing | `treatment_needs_icu` · `deteriorating_trend` · `limit_on_treatment` · `confusion_not_new` · `faulty_measurement` · `physicians_disagree` |
| `cases.<record>.traps` | The text traps planted in the record, at most two | list of integers | 1 plain negation · 2 uncertainty · 3 past vs present · 4 another person's finding · 5 medical abbreviations · 6 field vs text contradiction · 7 noise · 8 a request that is not an order · 9 an injection attempt · 10 a value missing altogether · 11 complex negation · 12 an empty field, the value in the text |
| `cases.<record>.wording` | How the physician wrote the admitting ward | text | `admit_only` · `specialty` · `binding_request` |
| `cases.<record>.true_vitals` | Vital signs the text corrects or completes (traps 6 and 12) | fields as in `vitals` | |

### `<record>__<template>.yaml` — the expected answer of one pair (104 files)

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `case_id`, `template` | The pair | text | |
| `patient_type`, `challenge`, `situation`, `traps`, `wording` | Copied from `clinical_inputs.yaml` | | as above |
| `target_ward` | The true target ward | text | a ward identifier |
| `needs.severe` | Whether the patient is severe — a severe patient never waits | true / false | |
| `needs.observation` | Whether an observation bed is needed | true / false | |
| `needs.isolation` | Whether the isolation room is needed | true / false | |
| `needs.intensive_care` | Whether intensive care is needed | true / false | |
| `news2` | The NEWS2 score, computed from the true vital signs | integer | 0–20 |
| `expected.action` | The right decision | text | `place` · `temporary_place` · `wait` · `escalate` |
| `expected.ward_id` | The ward of the placement | text or empty | a ward identifier |
| `expected.acceptable_wards` | Every ward that is right — more than one when two wards share a ladder level | list or empty | ward identifiers |
| `expected.bed_type` | The type of the bed | text or empty | `regular` · `observation` · `isolation` · `intensive_care` |
| `expected.awaiting_transfer_to` | For a temporary placement: the ward the patient waits to move to | text or empty | a ward identifier |
| `expected.wait_for` | For a wait: `ward_id`, `bed_number` and `because` | fields or empty | `because`: `cleaning` · `discharge_today` · `ward_note` |
| `expected.ladder_level` | How far from the target ward the placement is | integer or empty | 1 is the target ward |
| `expected.needs_typed_reason` | Whether the shift manager must write a reason (level 3 or farther) | true / false | |
| `expected.physician_approval_for_icu` | Whether intensive care needs a physician's approval (NEWS2 7 or more, no request) | true / false | |
| `expected.required_flags` | The flags the decision must carry | list of text | `prompt_injection` · `missing_vital_sign` · `severity_judgment_note` |
| `rationale` | Why this is the answer, in one line | text | |
| `ward_notes` | The private notes of the wards in this pair | list of text | Hebrew |
| `reports_alone_would_give` | The answer of a decider that reads the patient right but never asks the wards — in every pair it is wrong | text | |
| `approved_by` | Who approved the answer, and when | text | |
| `frozen` | The answer is final | true | |

### `contention_moments.yaml` — the moments of competition designed into a day

| Field | Meaning | Type | Allowed values |
|---|---|---|---|
| `<day>[].name` | What the moment tests | text | |
| `<day>[].time` | When the case is meant to open | text | `HH:MM` |
| `<day>[].bed` | The contested bed: `ward`, `bed` | fields | a ward and a bed number |
| `<day>[].patients` | The patients meant to compete for it | list of record identifiers | |

Who should get the bed is not written: the evaluation computes it at the moment, from the patients' true needs and the approved rule for two patients and one bed.

### `PC-<n>.yaml` — the expected answer of one paired case (7 files)

| Field | Meaning | Type |
|---|---|---|
| `id`, `template`, `patients` | Copied from `paired_cases.yaml` | |
| `priority` | The order in which the two patients are served | list of record identifiers |
| `answers` | One expected answer for each patient, with the fields of the table above | list |
| `approved_by`, `frozen` | As above | |

---

## 5. The hospital and the clinical rules — `config/`

Settings, not measurements: each value is written once and read through `src/ed_admission/config.py`.

| File | What it holds |
|---|---|
| `hospital.yaml` | The 5 wards and their beds (a regular ward: 10 regular beds, 2 observation beds, 1 isolation room · intensive care: 6 beds, 1 isolation room), and how long things take, in simulated minutes: cleaning 45 · a hold 30 · the shift manager's approval 15 · a phone call of the baseline 5. The case desk: how many cases are open at once (`concurrent_cases`) and when the bed is held (`hold_bed_when`) |
| `agents.yaml` | The single agent's model, the effort, the budget (per activation, per case, per run) and the prices of every kind of token, with the date they were checked |
| `clinical_rules/chief_complaints.yaml` | The closed list of 11 chief complaints, and the ward each one points to |
| `clinical_rules/proximity_ladder.yaml` | For each target ward, the wards from nearest to farthest |
| `clinical_rules/news2_levels.yaml` | The NEWS2 score → level of care |
| `clinical_rules/severity_judgment.yaml` | When the record raises the level NEWS2 points to, keeps it with a note, or which data counts — the approved situations reach the agents through `clinical_lists` |
| `clinical_rules/observation_conditions.yaml` | The 16 conditions that need an observation bed |
| `clinical_rules/isolation_conditions.yaml` | The 9 conditions that need the isolation room, and what is not isolation |
| `clinical_rules/structured_signs.yaml` | The signs of severity the baseline reads from the structured fields, with their sources |
| `clinical_rules/placement_rules.yaml` | The placement rules: who may use which bed, waiting, the shifts and the weekend, approvals, two patients for one bed, the limits on requests |

The sources of the clinical thresholds: `docs/design/sources.md`.

---

## 6. Run history — `outputs/runs.csv`, `outputs/admissions.csv`, `outputs/costs.csv`

Results, written by the measurement scripts (`src/ed_admission/database/run_log.py`). They accumulate from run to
run and are never uploaded to Git. A column is filled only for the kind of run it belongs to.

### `runs.csv` — one row per run

| Column | Meaning |
|---|---|
| `run_id` | Date, time and the name of what was run |
| `time` | When the run was made |
| `kind` | `system` — the whole decision · `agent` — one agent alone |
| `decider` | What decided: `code_coordinator`, `example_pipeline`, or a function |
| `agent` | The agent measured alone: `patient` · `department` |
| `version` | A fingerprint of the code, prompts, configuration and data — a rerun at the same version replaces its rows |
| `label` | What changed since the last run, in the runner's words |
| `match_rate` | Share of decisions that match the expected answer, 0–1 |
| `report_match_rate` | Share that match the answer a decider would give from the reports alone, 0–1 |
| `violations` | Hard-rule violations, a count |
| `need_met_rate` | Share of observation and isolation needs met or escalated, 0–1 |
| `off_service` | Placements outside the target ward, a count |
| `bed_not_available` | Placements in a bed the ward cannot really give, a count |
| `missing_flags` | Required flags that were not raised, a count |
| `escalations` | Decisions escalated to the shift manager, a count |
| `baseline match_rate`, `baseline violations`, `baseline need_met_rate` | The same numbers for the baseline, in the same run |
| `<day> minutes to bed` | Mean minutes from the admission decision to a bed, in that scripted day |
| `<day> minutes saved` | Mean minutes saved for each patient against the baseline, in that scripted day |
| `complete_trace_rate` | Share of cases with a complete trace, 0–1 |
| `groundedness` | Share of claims whose quote is in the record, 0–1 |
| `system` | Which system decided: `single_agent` · `multi_agent` (empty for a decider without a model) |
| `set` | The cases: `all` · `dev` · `test` · `none` (only scripted days) — `data/answer_key/dev_test_sets.yaml` |
| `days` | The scripted days in the run: `all` · `none` · one day |
| `seconds_p95` | The 95th percentile of the seconds per decision |
| `baseline bed_not_available`, `baseline mean_seconds`, `baseline <day> minutes to bed` | The baseline's, on the same cases |
| `<day> violations`, `<day> bed_not_available` | On that scripted day |
| `cost_cases` | The cases the run paid for — the pairs, the paired cases and the scripted days |
| `cost_total_usd` | What the whole run cost, in US dollars |
| `cost_mean_usd`, `cost_median_usd`, `cost_max_usd` | The cost of one case, in US dollars |
| `cases_over_case_cap` | Cases stopped by the per-case budget, a count |
| `partial_run` | Whether the run's budget was reached, so the run stopped early |
| `mean_seconds` | Mean time of one decision, in seconds |
| `target_ward`, `binding_request`, `severe`, `intensive_care`, `observation`, `isolation`, `flag …` | For an agent measured alone: the share it got right on each check, 0–1 |
| `errors` | Cases that ended in an error, a count |

### `admissions.csv` — one row per patient in a run

| Column | Meaning |
|---|---|
| `run_id`, `time`, `kind`, `decider`, `agent`, `version`, `label` | The run, as in `runs.csv` |
| `source` | Where the patient comes from: a set of pairs, the paired cases, or `day:<name>` |
| `case` | The template or the paired case |
| `patient_id` | The record |
| `action` | The decision: `place` · `temporary_place` · `wait` · `escalate` |
| `target_ward` | The patient's target ward, as the decider understood it |
| `ward_id`, `bed_number`, `bed_type` | The bed that was chosen |
| `ladder_level` | How far from the target ward |
| `temporary` | Whether the placement is temporary |
| `waits_for` | The ward the patient waits for |
| `flags` | The flags of the decision |
| `reason` | The decider's reason |
| `expected` | The expected answer, in short |
| `match` | Whether the decision matches it |
| `violations` | The hard rules the decision broke |
| `need_met` | Whether the observation or isolation need was met |
| `bed_not_available` | Whether the ward cannot really give the bed |
| `missing_flags` | Required flags that were not raised |
| `decision_seconds` | How long the decision took, in seconds |
| `error` | The error, if the case failed |
| `trace` | The identifier of the case's trace |
| `outcome` | In a scripted day: `placed` · `escalated` · `still waiting` |
| `admission_decision_at`, `bed_at`, `escalated_at` | Simulated clock times, in a scripted day |
| `minutes_to_bed`, `minutes_to_shift_manager`, `minutes_waited` | Simulated minutes, in a scripted day |
| `decider_minutes` | The part of the wait that the decider itself took |
| `shift_manager` | The shift manager's answer — always `approved` in the simulation |

### `costs.csv` — one row per case in a run of a decider that calls a model

| Column | Meaning |
|---|---|
| `run_id`, `time`, `kind`, `decider`, `agent`, `version`, `label` | The run, as in `runs.csv` |
| `source` | Where the case comes from: a set of pairs, the paired cases, or `day:<name>` |
| `case_id` | The case |
| `system` | `single_agent` · `multi_agent` |
| `ended` | How the case ended: `final` (a decision) · `budget` (the per-case budget) · `run_budget` (the run's budget) · `refused` · `failed` · `no_final` |
| `model_calls` | How many times the model was called |
| `tokens_in`, `tokens_out` | Tokens read and written by the model |
| `cost_usd` | What the case cost, in US dollars |
| `cost_<agent>_usd` | What each agent cost in that case, in US dollars — one column per agent |

`outputs/comparison_report.csv` and `outputs/comparison_report.md` are the comparison report
`scripts/compare_systems.py` makes from the run history: for each system its latest 3 test runs (mean and range of
every measure — quality, latency, cost — the baseline on the same cases, which system is better beyond the spread,
the stability of the decisions) and its latest run of each scripted day, with the protocol's reading.
