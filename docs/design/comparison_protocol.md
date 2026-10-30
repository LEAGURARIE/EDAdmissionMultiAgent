# Comparison protocol — the single agent against the multi-agent system

**Status:** approved by Lea and **frozen on 2026-10-15**, before any paid run — so no metric, set or rule can be
chosen after seeing a result. The set sizes (section 3) and the run caps that fit them (section 8) are the only
parts still to be filled in, at step 5 and before any run. A change after the first test run is recorded below
as a deviation, with the reason — never edited in place.

## 1. The two systems

| | Single agent | Multi-agent system |
|---|---|---|
| Who decides | one agent doing every agent's job (`agents/single_agent.py`) | a Coordinator with the Patient Agent, the Department Agents and the Judge |
| Tools | every tool of the system's agents: the record, NEWS2, the lists, its own assessment, every ward's report and notes, the ranking, `decide`, `escalate_to_human` | each agent its own tools, enforced in code |
| Model | Claude Opus 5.5 | each agent the model its own measurement supports; a fallback model when the main one fails |
| Prompt | one prompt, written in six components (`prompts/single_agent.md`) | one prompt per agent, written the same way |

The single agent is a **strong opponent, not a weakened one**: the same information as the whole multi-agent system
(it reads the wards' notes itself), the strongest model, and a carefully written prompt. A win against a weak
baseline would say nothing about the architecture.

The reference for both is the baseline — the Code Coordinator, today's procedure written as code.

## 2. The same task

Both systems are deciders with the same contract, `run(engine, event, waiting) -> list[Decision]`, on the same
cases, in the same environment: the task tells only the time, the patients' ids and when each admission was decided.
Every hard rule is checked by the same code (`tools/decide.py`) in both.

## 3. The sets

| Set | Use | Who may look |
|---|---|---|
| Development | improving each system (section 6) | anyone |
| Test | the comparison only | sealed until the final measurement |

The split is stratified (template, patient type, challenge, trap) and fixed in code before any run — step 5 of the
single agent's build. Both systems run on exactly the same cases; `scripts/compare_systems.py` refuses runs on
different cases. The paired cases and the four scripted days are measured as well.

**Filled in at step 5 (2026-10-15), before any run** — `src/ed_admission/evaluation/sets.py`, frozen in
`data/answer_key/dev_test_sets.yaml`, guarded by `tests/test_sets.py`:

| Set | What is in it | Cases per run |
|---|---|---|
| Development | 22 pairs, one from each template | 22 |
| Test | 22 other pairs, one from each template, and the 7 paired cases | 29 |
| Scripted days | the four days, each its own run, **once per system** | 22 · 12 · 12 · 6 |

- Every patient type, challenge and trap is in both sets. No patient is in both sets or twice in one, and no
  patient of a paired case is in the development set.
- The scripted days reuse 44 of the 74 patients, so 14 development patients also appear on a day. Keeping them out
  would have left three kinds uncovered in the development set, so coverage came first. On a day the patient is in
  another hospital state, and the days are not the test pairs.
- The runs: `scripts/run_metrics.py --set dev`, `--set test`, and `--set none --days <day>`.

## 4. The rubric — fixed in advance

The expected answers were frozen on 2026-10-06, before any agent was built. The metrics are the charter's:

| Column | Metric | Target |
|---|---|---|
| **Quality — safe** | hard-rule violations | 0 |
| | observation / isolation needs met or escalated | 100% |
| | placements in a bed the ward cannot give | 0 |
| **Quality — suitable** | decisions that match the expected answer | at least 85% |
| | contention moments decided right (day 4) | — |
| **Quality — tracked** | complete traces | 100% |
| | claims backed by a quote that is in the record (groundedness) | at least 95% |
| **Latency** | seconds per case — mean and 95th percentile | — |
| | minutes from the admission decision to a bed, on the scripted days | fewer than the baseline |
| **Cost** | USD per case — mean, median, maximum | — |
| | USD per run | — |

All three columns are reported for both systems and the baseline, always together — never quality alone.

## 5. How the result is read — decided now

1. **Safety first.** A system with more hard-rule violations, more unmet needs or more beds the ward cannot give
   does not win on the other columns.
2. **Then suitability**, by the share of matching decisions.
3. **Then latency and cost.** A system that is as safe and as suitable wins if it is faster or cheaper; when one is
   faster and the other cheaper, both are reported and neither is called the winner.
4. **A difference counts only when it is larger than the spread between repetitions** (section 7). Within the spread
   it is a tie, and is reported as one.
5. Cases the multi-agent system sent to its fallback model are reported apart; a run with many of them is not
   compared. A partial run (stopped by the run's budget) is not compared.

## 6. Improving each system — the same effort

Each system gets **2 rounds** of improvement on the development set, and no more: a run, a look at the kinds of
mistake, one change to the prompt or the tools, a run again. Every round is a run in the history with a label saying
what changed. The test set is not run during the rounds.

## 7. Repetitions — no temperature

The same case can get a different answer on another run. How each kind of model is held steady:

| Model | Temperature | What keeps it steady instead |
|---|---|---|
| A reasoning model (Claude Opus 5.5, Sonnet 5.5, Haiku 5.5 — they reject a temperature) | none is sent | the same model id, the same `effort`, the same prompt version and tool schemas in every run; and the repetitions below |
| A model that accepts a temperature (a non-reasoning model, if one is used) | 0, and a fixed seed where the model offers one | the same as above; temperature 0 narrows the spread but does not remove it, so it is repeated as well |

Every model, its temperature (or none) and its effort is written in `config/agents.yaml` and recorded with every
call, so a run says exactly what produced it.

**The test set is run 3 times for each system.** The report gives each metric's mean and its range over the three
runs, and the stability of the decisions: the share of cases that got the same decision in all three.

## 8. Budget accounting

| | Single agent | Multi-agent system |
|---|---|---|
| Per case | $1.00 | $1.20 |
| Per run | $12 | $14.40 |

The same prices (`config/agents.yaml`, checked monthly against Anthropic's page), the same counting (every kind of
token at its own price, every case a run pays for — the scripted days too), the same file (`costs.csv`).

**Why the multi-agent system has a 20% higher cap.** A cap is a safety ceiling, not an allowance to spend. Several
agents carry a fixed overhead the single agent does not: each agent has its own system prompt and tool definitions,
written to the cache and read again on every activation, and the agents' answers pass through the Coordinator. With
the same cap the multi-agent system could be stopped by its overhead alone, and lose cases for a reason that is not
the quality of its decisions. The higher cap does not buy it anything in the comparison: the cost column reports
what each system **really** spent, so the overhead counts against it there. If its mean cost per case turns out to
be under $1.00, the caps are made equal.

**The run cap fits the set** (filled in at step 5). The expected cost of a single-agent case is about $0.25,
between $0.17 and $0.35 (free token counts of a real case, 2026-10-15). A test run is 29 cases: about $7.25, and
$10.15 at the high end — so the run caps are $12 for the single agent and $14.40 for the multi-agent system, the
same 20% apart. The largest day run, 22 cases, fits too. The expected total, both systems, about $126 with every
agent on Opus; less with cheaper models where the multi-agent system's measurements allow.

## 9. Deviations and clarifications

- **2026-10-15, clarification (Lea approved):** "larger than the spread between repetitions" (section 5) is read as
  **the ranges do not overlap** — the worse value of the better system is still better than the best value of the
  other over the 3 repetitions. The most cautious rule three repetitions allow. Built in
  `src/ed_admission/evaluation/comparison_report.py`; the report is `scripts/compare_systems.py`.
- **2026-10-15, deviation — a measurement bug fixed (Lea approved), before any test run:** a ward note that tells
  of a bed freed but not yet updated freed, in the true state, the first occupied bed of its type — not the bed
  the note names. A decider that took the note's bed was scored "a bed the ward cannot give" (seen in the second
  development run: P-014 on T18). Now every such effect names its bed and the true state frees that bed; the four
  notes that named no bed (T8 twice, T12, T19) got the number of the bed the environment had freed for them, so
  their true state did not change. The expected answers are unchanged (they name a ward and a bed type, not a
  number); the frozen answer files keep the old wording of those notes in their `ward_notes` copy. Both systems
  face the same notes. The baseline, re-measured: unchanged — 45 of 118 decisions break a hard rule, 18 of 56
  needs met, 0 beds the ward cannot give, 38% match, 26 minutes to a bed. Guarded by
  `tests/test_templates.py`. The two development runs before it are not re-scored.
- **2026-10-15, deviation — approved clinical knowledge handed to the agents (Lea approved), after the 2 rounds
  and before any test run:** the severity judgments Lea approved on 2026-10-07 (when the record raises the level
  NEWS2 points to, keeps it with a note, or which data counts) were in DECISIONS.md only, so no agent of either
  system was ever given them; the single agent's third development run placed an observation patient whose vital
  signs worsened between measurements (P-063 on T2) in a regular bed. They are now in
  `config/clinical_rules/severity_judgment.yaml` and in `clinical_lists`, for both systems; the one still to be
  checked against the NEWS2 source (confusion that is not new) is not handed over. This is not a third round of
  improvement: it gives the system what was approved for it, the same for both systems; the prompt is unchanged.
  Not re-measured on the development set; the test runs measure it.
- **2026-10-15, a known contradiction kept for both systems (after the single agent's test):** confusion that is
  not new — the prompt's Constraint 7 and DECISIONS say keep the level with a note; the NEWS2 source and the answer
  key do not count it. The single agent followed its prompt in P-068 on T16 (an observation bed for a patient the
  answer calls not severe — one hard-rule violation in each repetition). Lea: no change; the multi-agent system
  gets the same rule, so the comparison is under the same instruction.
- **2026-10-15, deviation — internal medicine A or B scored by the decided rule (Lea approved: A and B are
  equivalent general internal medicine wards), before any test run:** the rule of 2026-10-08 says that when the
  record names neither, the target is the one with the lower occupancy; the whole-decision answers were built
  around the ward each template was made for, which a decider cannot know. 42 of the 104 pairs name neither; for
  27 the rule chooses the other ward, and the decision is now scored against the answer for that ward, derived at
  scoring time from the same hospital (`metrics.by_the_rules`); the frozen answer files are not changed. The 7
  paired cases keep their answers (both patients are built around one ward). The baseline, re-measured: match 56 of
  118 (47%, was 38%); the rest unchanged (45 hard rules broken, 18 of 56 needs met, 0 beds the ward cannot give,
  26 minutes). Some of the 27 pairs no longer meet the note their template planted for the original ward — what a
  real shift manager choosing by occupancy would also not meet. The development runs, re-scored without a model
  call: 73% · 73% · 86%.
