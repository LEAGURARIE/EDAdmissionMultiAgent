# data/answer_key — expected answers (metrics only)

`pairs.yaml` — the first set, 60 pairs: which template each record is placed in, and its true target ward.
`pairs_set2.yaml` — 44 more pairs: 30 in templates T13-T22 (ward notes, ping-pong, the shift rule) and the added records P-061-P-074. What each private ward note means for the beds is written next to it in the template (`note_effects`).
`paired_cases.yaml` — two patients, one bed; answers in `PC-<n>.yaml`.
`contention_moments.yaml` — the moments of competition in day 4: the bed and the two patients, never the winner — it is computed at the moment.
`dev_test_sets.yaml` — the development and test sets of the comparison, frozen 2026-10-15 (built by `src/ed_admission/evaluation/sets.py`; `docs/design/comparison_protocol.md`).
`judge_cases.yaml` — 12 cases for measuring the Judge Agent alone (rubric items 2, 8 and 9; four each, right and wrong), built from real record sentences and ward notes, patients of the development set or of no set — **approved as they are by Lea, 2026-11-10** (`status`).
`clinical_inputs.yaml` — per record: true patient type, challenge, traps, wording, corrected vital signs.
The answers were built from the rules by `src/ed_admission/evaluation/answer_key_builder.py`, approved by Lea and frozen;
the one-time build and freeze steps were removed afterwards (2026-11-14), so a frozen answer cannot be rebuilt by mistake.
One file per pair — a record placed in a ward-state template, named `<case_id>__<template>.yaml`.
Each file also lists the text traps planted in the record (`traps`) — they are written here only, never in the record.
**Only the metrics code may read this folder.** The environment, tools, baselines and agents never do;
`tests/test_answer_key_isolation.py` fails if any other code refers to it.
It is read to measure; nothing of it may reach an agent — not in code, not in a prompt
(`docs/design/agent_interfaces.md`, section 7).
Format: `docs/design/case_file_example.md`. Templates: `docs/design/ward_state_templates.md`.
