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
- **2026-11-09, a known difference in the prompts (Lea approved), before any multi-agent run:** the Patient Agent's
  prompt keeps the single agent's wording for reading the record, and adds what the single agent's prompt does not
  have: three short examples of reading traps (negation and uncertainty; someone else's finding and the past; a
  field empty with the value in the text), a confidence per finding, the needs each finding backs, the Judge's
  correction round, and a refusal example (it never picks a bed). The examples teach reading, which both systems
  do, so they may favour the multi-agent system on the Judge's item 2 and on groundedness. The single agent's
  prompt is not changed — it was measured as it is. The report names this difference next to any result on
  reading the record.
- **2026-11-09, a clarification of how the days are shown (Lea approved), before any multi-agent run:** what is
  measured does not change, only how the report shows it. The three days of a shift (39 admissions) are shown
  together, day by day and pooled: the minutes to a bed over every patient placed, each day weighted by its placed
  patients. Day 4, competing for a bed, is shown apart: the moments decided right out of those reached as designed,
  and its violations, never its minutes to a bed. Two patients compete for one isolation room there, so one of them
  waits by design; a system that misses the isolation need does not wait, and would look faster for being less
  safe. The baseline is shown in both parts, from the same runs.
- **2026-11-12, deviation — the multi-agent system's 2 rounds made on the whole system (Lea decided), after its
  first test runs:** the single agent's 2 rounds each followed a run of the whole system on the development set
  (section 6). The multi-agent system's two changes so far — the Judge Agent's prompt (items 8 and 9) and the Patient
  Agent's check of its flags — were made on agents measured alone, before the whole system had run, so neither
  could see where the whole decision fails. Its first whole-system development run showed a kind of mistake the
  agents alone were never measured on: a bed in the target ward that frees soon (T15, T16, T21) is not offered as a
  wait — the Department Agent declines a bed that has no number in its note, or answers a condition with no
  minutes. Decided: those two changes count as building the agents (the model and prompt ladder of build step 5),
  and the multi-agent system gets **2 rounds on the whole system, as the single agent did**, each after a whole
  development run. **Round 1: the Department Agent moves from Haiku 5.5 to Sonnet 5.5** (the model ladder of
  DECISIONS, "start cheap, climb only where measurement asks for it" — its prompt already shows this case, example
  3, and it still misses it); round 2 is chosen from round 1's development run. The first three test runs (match
  58%, `20261112-181532`, `-183443`, `-185404`) stay in the report as the multi-agent system before its rounds;
  after the rounds the test set is run 3 times again. Two things the single agent did not have are named in the
  report: the multi-agent system's test results were seen before its rounds, and its development set was
  measured agent by agent as well. The rounds are chosen from the development set only.
- **2026-11-12, the multi-agent system's 2 whole-system rounds (Lea approved each), on the development set:**
  round 1 — the Department Agent on Sonnet 5.5 (run `20261112-205417`, match 68%, unchanged; the wards' answers
  closer to right: a bed soon now answered as a condition, but without its minutes, or with a bed the note does not
  name) · round 2 — `answer_ward` refused a condition with neither minutes nor the closed question (run
  `20261112-211545`, match 73%; the only decision that changed, P-019, changed by the Patient Agent's run-to-run
  variation; the Department Agent sent the refused answer again unchanged until its loop budget ran out, so P-025 was
  escalated). **Round 2 was reverted (Lea), on the development set:** it fixed none of the decisions it was made
  for. The multi-agent system is frozen with round 1 and run 3 times on the test set.
- **2026-11-13, deviation — a third arm: the multi-agent system with Opus 5.5 in every agent (Lea decided), after
  the comparison:** the single agent runs on Opus 5.5, the multi-agent system on Sonnet 5.5, Sonnet 5.5 and
  Haiku 5.5, so the gap in match (76% against 60%) mixes the design with the models. To separate them, the
  multi-agent system is run once more with the same model as the single agent in all three agents — the same
  prompts, the same code, the same limits; only the three model lines of `config/agents.yaml` change, for these
  runs only, and are restored after. It is a check of where the gap comes from, not a contender: it gets **no
  rounds**. One run on the development set (to see that nothing breaks with Opus), then 3 runs on the test set.
  The frozen multi-agent system (round 1) stays the system compared in the report; this arm is reported beside
  it. Estimated cost: about $18.
- **2026-11-13, the Opus arm's result:** test runs `20261113-213546`, `-215934`, `-222334` —
  match 58%, 64%, 61% (mean 61%) against 60% (58–61%) for the frozen system on its cheaper models;
  the ranges overlap entirely. A stronger model in every agent did not close the gap to the single agent (76%); it
  tripled the cost ($0.131 a case). The gap comes from the design, not the models. Spent: $14.0.
- **2026-11-13, deviation — round 2, a second attempt (Lea decided), after the test results were seen:** round 2
  (`answer_ward` refusing a condition without minutes) was reverted on 2026-11-12. The Opus arm showed the misses are
  in the method between the agents: a ward that knows a bed frees soon says so in its reason, its closed fields do
  not carry it, and the code goes on to a temporary bed. The second attempt is **rubric item 12, "no better option
  left"**, checked by the Judge Agent: the case file — now with each ward's rung, the target ward and the waiting
  rule — shows no option that meets the need and the rules and is better than the proposal, by the order of the
  rules. Worded as a principle, not for the cases seen (Lea: the agents must act in every situation); its example
  is a situation no template has. When it fails, the Judge names the ward (`missed_ward`, checked in code), only
  that ward is asked again, and code proposes from its answer. **Named in the report:** this is the third change
  tried on the multi-agent system's whole-system rounds, and it was chosen after its test results were seen — the
  single agent had neither. It is measured on the development set first; it is kept only if it fixes decisions
  there, and then the test set is run 3 times and the days again.
- **2026-11-14, round 2's second attempt reverted (Lea decided):** the development run `20261113-225831` with rubric
  item 12 matched 68% — the same as round 1; no decision changed for the better, and one got worse: T21, a temporary
  bed before, was escalated because the Judge Agent's verdict failed the handoff check twice. In T15 and T16 the Judge
  Agent quoted the target ward's "about an hour" but passed item 12, reading it as not clearly within the allowed
  wait. By the rule set above (kept only if it fixes decisions on the development set), item 12 was removed from the
  rubric, the Judge Agent's prompt, the verdict, the case file, the Coordinator and the tests; the version fingerprint
  is back to `f177bc7f43`, the frozen system. The test set was not run with it. **Named in the report as a finding:**
  neither a stronger model in every agent (the Opus arm) nor a general check by the Judge Agent closed the gap — what
  a ward knows and says only in free text is lost between the agents. Spent: $0.82.
- **2026-11-14, side checks kept out of the comparison (Lea approved):** the report takes each system's latest 3
  complete test runs and latest day runs, but never a run whose label starts with a side-check prefix
  (`SIDE_CHECKS` in `evaluation/comparison_report.py`: "Opus arm"); the side checks are reported apart, as checks of
  where the gap comes from. The report again reads the frozen system's runs `20261112-213908`, `20261113-175416`,
  `-181450`.
- **2026-11-14, the side checks in the report (Lea approved):** `comparison_report.md` gets a section "Where the gap
  comes from": each side check (`SIDE_CHECKS` — "Stronger models", the Opus arm; "Judge Agent looks for a better option", round 2's second
  attempt, which was rubric item 12), set by set,
  against the frozen system on the same set (the version of its chosen test runs) — match, hard-rule violations,
  escalations, cost a case; beyond the spread only when both sides have at least two runs, a single run is shown
  as "one run — no spread". On the test set the Opus arm is the same on every quality measure and 3x the cost.
