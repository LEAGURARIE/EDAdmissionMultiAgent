# Case file — example

**Status:** 2026-10-07. The three files below are copied unchanged from the project — a real pair, not a mock-up.
If a format changes, this page is rebuilt from the files.

A case is a **pair**: one record placed in one ward-state template. Three files, in three folders:

| File | Folder | Who reads it |
|---|---|---|
| The ED record, frozen at the admission decision | `data/records/` | The system, only through the `read_patient_record` tool |
| The ward-state template, with the wards' private notes | `data/templates/` | The environment builds the hospital from it. The system sees only the ward reports; each Department Agent also sees its own ward's notes |
| The expected answer | `data/answer_key/` | **Metrics code only.** A test fails if any other code refers to this folder |

Every pair tests **both**: reading the record (the challenge and the traps) and coordination between the agents
(the private notes make the ward reports alone lead to a wrong answer).

## Example — P-043 in template T9

### `data/records/P-043.yaml` — what the system sees

```yaml
case_id: P-043
patient:
  age: 67
  sex: female
triage_level: 3
chief_complaint: abdominal_pain

vitals:
  respiratory_rate: 18
  oxygen_saturation: 97
  supplemental_oxygen: false
  spo2_scale_2: false
  systolic_bp: 122
  heart_rate: 92
  consciousness: alert
  temperature: 37.8

labs:
  wbc: 13.8
  hemoglobin: 12.6
  sodium: 135
  potassium: 3.4
  creatinine: 1.1
  glucose: 108
  crp: 88
  lactate: 1.4

background_conditions: "HTN, אשפוז לפני שבועיים עם דלקת ריאות שטופלה באנטיביוטיקה"
medications: [amlodipine 5mg]
allergies: []
functional_status: independent

physician_request:
  ward: null

triage_note: "כאבי בטן ושלשולים."

physician_note: |
  בת 67 HTN, שוחררה לפני 10 ימים מאשפוז בפנימית עם דלקת ריאות שטופלה ב-ceftriaxone ואחר כך amoxi-clav.
  4 ימים כאבי בטן מתכווצים ושלשולים מימיים 8-10 ביום, ללא דם, חום קל. ב.ג. רגישות מפושטת קלה בבטן
  ללא גירוי צפקי. CT בטן - עיבוי דופן המעי הגס, ללא נקב, ללא הרחבה. נבדקה ע"י כירורג - ללא בעיה כירורגית
  בדיקת צואה ל-C. difficile נשלחה, תשובה ממתינה - לשלול C. diff על רקע האנטיביוטיקה. K 3.4 - תיקון
  נוזלים iv, vancomycin פומי אמפירי

  קוליטיס, חשד ל-C. difficile אחרי טיפול אנטיביוטי. לאשפוז
```

### `data/templates/T9.yaml` — the hospital at that moment

```yaml
# T9 — internal medicine full at night. Design: docs/design/ward_state_templates.md.
# 23:00. Both internal medicine wards are full with no discharge expected; room only in cardiology
# and general surgery (ladder levels 3 and 4 for internal medicine).
# Private note: cardiology takes only cardiac patients tonight.
id: T9
name: פנימיות מלאות בלילה
day: Thursday
time: "23:00"
relative_to: none

wards:
  internal_medicine_a: {regular_free: 0, observation_free: 0, isolation_free: false}
  internal_medicine_b: {regular_free: 0, observation_free: 0, isolation_free: false}
  cardiology:
    regular_free: 1
    observation_free: 1
    isolation_free: true
    notes:
      - "הלילה אחות אחת על כל המחלקה ושני מטופלים אחרי צנתור דחוף. מקבלים רק מטופלים קרדיולוגיים עד הבוקר."
    note_effects:
      - {effect: unavailable, bed_type: regular}
      - {effect: unavailable, bed_type: observation}
      - {effect: unavailable, bed_type: isolation}
  general_surgery:     {regular_free: 2, observation_free: 1, isolation_free: true}

intensive_care:
  intensive_care_free: 1
  isolation_free: true
```

### `data/answer_key/P-043__T9.yaml` — metrics only

```yaml
case_id: P-043
template: T9
patient_type: C
challenge: 3
situation: null
traps:
- 2
wording: admit_only
target_ward: internal_medicine_a
needs:
  severe: false
  observation: false
  isolation: true
  intensive_care: false
news2: 1
expected:
  action: temporary_place
  ward_id: general_surgery
  acceptable_wards:
  - general_surgery
  bed_type: isolation
  awaiting_transfer_to: internal_medicine_a
  wait_for: null
  ladder_level: 4
  needs_typed_reason: true
  physician_approval_for_icu: false
  required_flags: []
rationale: no isolation bed in the target ward — nearest on the ladder, level 4
ward_notes:
- 'cardiology: הלילה אחות אחת על כל המחלקה ושני מטופלים אחרי צנתור דחוף. מקבלים רק
  מטופלים קרדיולוגיים עד הבוקר.'
reports_alone_would_give: temporary_place cardiology, isolation
approved_by: Lea Gur-Arie, 2026-10-07
frozen: true
```

## Reading the example — for the reader only, not part of the files

| | What is needed | Where it is |
|---|---|---|
| **Reading** | Isolation: C. difficile is suspected (the record says "לשלול C. diff" — "rule out C. diff"). The chief complaint, abdominal pain, does not say so | Challenge 3, misleading complaint · trap 2, uncertainty |
| **Target ward** | The physician wrote only "לאשפוז" ("for admission"), so the agents classify it: internal medicine A | Wording `admit_only` |
| **Coordination** | The reports show a free isolation room in cardiology. Cardiology's private note: tonight it takes only cardiac patients | `ward_notes` in the answer |
| **The answer** | General surgery's isolation room, ladder level 4, temporary, awaiting transfer, with a typed reason | `expected` |
| **A decider that never asks** | Cardiology's isolation room — wrong | `reports_alone_would_give` |
