# CHARTER — Multi-Agent ED Admission Placement

**Author:** Lea Gur-Arie · **Date:** 2026-10-04, updated 2026-11-14

## 1. Problem & user

The **ED shift manager** placing admitted patients faces long waits — about **4 h** on average in an Israeli tertiary hospital, with **two thirds** waiting over 2 h **[S3]**, while mortality rises from **2.5% to 4.5%** as boarding grows from under 2 h to over 12 h **[S1]** — and in a large US academic center **19.6%** of patients were placed in a ward that does not fit their problem, with **22.8%** longer stays **[S2]**; our system weighs clinical fit, observation and isolation needs, and bed availability together to find the optimal ward bed, shorten the wait, and record a full decision trail for every placement.

## 2. Success metric (set before building)

**What is measured:** 118 placement decisions with clinically approved, frozen expected answers, 3 scripted days with 39 admissions, and a 4th day of three moments when two patients compete for one bed.

**The baseline — the Code Coordinator:** today's procedure, written as code, with no agents. It reads the record's structured fields (vital signs, chief complaint, the ward the physician requested), looks at the wards' reports, **phones each ward** (5 minutes a call) to ask what it can really give, and ranks the beds by the rules. It does **not** read the physician's free text. The system is judged against it.

| | The question | Target | Code Coordinator |
|---|---|---|---|
| **Safe** | Decisions that put a patient in an unsafe bed (a hard rule broken) | 0 (0%) | 45 of 118 (38%) |
| | Patients who need observation or isolation and got it, or were escalated | 56 of 56 (100%) | 18 of 56 (32%) |
| | Placements in a bed the ward cannot really give | 0 (0%) | 0 of 100 (0%) |
| **Suitable** | Decisions that match the expected answer | ≥ 100 of 118 (85%) | 56 of 118 (47%) |
| **Fast** | Minutes from the admission decision to a bed | fewer than the baseline | 26 |

**Tracked:** 100% of cases with a complete trace · ≥ 95% of claims backed by a quote that is in the record. Baseline measured 2026-10-10 (match re-measured 2026-10-15).

## 3. Data + plan B

**Synthetic only:** a simulated adult hospital (5 wards; regular and observation beds, one isolation room per ward) and 74 synthetic ED records in Hebrew with English medical terms. **Size:** 22 ward states with private ward notes; 118 decisions (±6.5% precision at 85%; 0 violations in 118 → true rate ≤ 2.5%); 4 scripted days; frozen development and test sets. Every case tests both reading the record and coordinating with the wards. **License:** our own, no real patient data. **Proof:** the baseline runs end to end, in Colab too. **Plan B:** fewer, harder hand-written cases.

## 4. Architecture

```text
Simulated hospital ──(trigger: admission decision, record frozen)──▶ Coordinator = code (a LangGraph graph)
   Coordinator ⇄ Patient Agent      (Sonnet) the only one that reads the record: needs, flags, quotes
   Coordinator ⇄ Department Agent   (Sonnet) one per ward, rung by rung — its report and private notes
   Coordinator ⇄ Judge              code checks the rules, then the Judge Agent (Haiku) the reading
   No handoff between agents: every answer is a checked contract, and routing reads closed fields only.
▼ code: rank_placements (ladder) · the proposal · decide (a decision that breaks a hard rule is refused)
▼ recommendation → shift manager approval → bed assigned
```

## 5. The agent's chair

- **Decides what code cannot:** the Patient Agent reads the free text; the Department Agent, a ward's notes; the Judge Agent, the reading and the reason. Routing, ranking, the proposal and hard rules: code.
- **Tools**, each agent only its own, enforced in code: Patient — `read_patient_record`, `compute_news2`, `clinical_lists`, `record_assessment` · Department — `department_view`, `answer_ward` · Judge Agent — `read_case`, `give_verdict` · Coordinator — none, it is code. **Compared with** one agent (Opus) holding every tool.
- **On failure:** one retry, then escalation to the shift manager — never a guess. **Works without agents:** the Code Coordinator.

## 6. Risks & cut version

- **Misreading the record** → closed lists, every quote checked against the record, the hard rules checked in code before any decision. **Cost or latency** → agents on triggers only; request limits; a per-case budget. **Found:** a ward's free-text knowledge is lost at a closed handoff → reported, not tuned. **An agent that sees the expected answers** → only the evaluation code reads them, guarded by tests; an agent gets the patient's id and the decision time, nothing more.

**Cut version:** *"If only two weeks remain, we drop the single-agent comparison, and submit the multi-agent system on the cases and the scripted days."*

## 7. Milestones & hats

A personal project: Lea Gur-Arie holds every hat — product, agents, model, data.

**18.10:** cases, days, baseline, first metrics · **30.10:** agents integrated, metrics vs. the baseline, 1-min demo · **01.11:** freeze · **12.11:** final.

## References — full citations in `docs/design/sources.md`

- **[S1]** Singer AJ, et al. *Academic Emergency Medicine*, 2011. PubMed [22168198](https://pubmed.ncbi.nlm.nih.gov/22168198/)
- **[S2]** Song H, Tucker AL, et al. *Management Science*, 2020;66(9):3825–3842. [doi:10.1287/mnsc.2019.3395](https://doi.org/10.1287/mnsc.2019.3395)
- **[S3]** Greenberg et al. *Israel Journal of Health Policy Research*, 2018. [PMC6247762](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247762/)

---

## Checkpoint 1 — 18.10.2026

1. **What works end to end:** the simulated hospital, 74 synthetic records, 118 placement decisions with frozen expected answers, 3 scripted days, and the baseline — the Code Coordinator: today's procedure, written as code, with no agents. One command (`scripts/run_baseline.py`) or [one Colab notebook](https://colab.research.google.com/github/LEAGURARIE/EDAdmissionMultiAgent/blob/main/notebooks/ED_Admission_Colab.ipynb). 922 tests pass.
2. **The metric against the target** (the baseline): a hard rule broken in 38% of decisions (target 0%) · needs met 32% (100%) · a bed the ward cannot give 0% (0%) · match 38% (85%) · 26 minutes to a bed (the system must be faster).
3. **Decided since the charter, and why:** two agents and a Coordinator instead of six agents — reading the record was split between agents that needed the same text, and the safety checks are fixed rules, so they moved to code · one baseline, today's procedure, instead of "first free bed", which no hospital uses · a personal project.
4. **What is stuck:** nothing. The agents are not built yet: each agent's code and system prompt is reviewed first; an Anthropic API key is needed to run them.
5. **Next three steps:** build the Patient Agent and measure it on the 74 records · build the Department Agent and measure it on the ward requests · build the Coordinator, compare with a single agent, and record the 1-minute demo for 30.10.

## Checkpoint 2 — 30.10.2026

1. **What works end to end:** everything in Checkpoint 1, and the single agent — one agent doing every agent's job: it reads the record and every ward's report and notes, ranks the options and decides. Improved in 2 rounds on 22 development cases, then measured 3 times on 29 test cases it had never seen, and once on the 4 scripted days (`scripts/run_metrics.py --decider single_agent --set test --repeat 3 --paid`). 1,090 tests pass.
2. **The metric against the target** (test cases, mean of 3 runs; the baseline on the same cases): a hard rule broken in 1 decision per run (target 0; baseline 11) · needs met 100% (100%; 47%) · a bed the ward cannot give 0 (0; 0) · match 76%, range 72–78% (85%; 42%) · the same decision in all 3 runs 94% · $0.12 and 33 seconds a case. The scripted days: no hard rule broken, and all 3 moments of competing for a bed decided right.
3. **Decided since Checkpoint 1, and why:** a fair comparison written and frozen before any paid run — the same task, cases and budget accounting for both systems, 2 improvement rounds each, 3 repetitions, and a difference counts only beyond the spread · the single agent is a strong opponent: it reads every ward's notes itself · a measurement bug fixed and recorded as a deviation (a note that frees a bed now frees the bed it names) · internal medicine A or B, when the record names neither, scored by the occupancy rule (the baseline's match 38% → 47%) · the multi-agent system redesigned: a Coordinator that only activates the agents, and a Judge that checks a placement before it is decided.
4. **What is stuck:** the multi-agent system is not built yet. The one broken rule is the same case every run: a known contradiction in the rules about chronic confusion, kept on purpose for both systems.
5. **Next three steps:** build the Patient Agent and the Department Agent, and measure each alone · build the Coordinator and the Judge, and run the multi-agent system on the same test cases 3 times · the comparison report, the monthly cost in the README, and the 1-minute demo.

## Final update — 14.11.2026

1. **What works end to end:** everything in Checkpoint 2, and the multi-agent system — the Patient Agent, the Department Agent (one per ward) and the Judge Agent, with the Coordinator and the Judge's first layer in code. Each agent measured alone, then 2 rounds on the whole system on the 22 development cases, then frozen and run 3 times on the 29 test cases and once on the 4 scripted days, under the same protocol as the single agent (`scripts/compare_systems.py`). 1,241 tests pass.
2. **The metric against the target** (test cases, mean of 3 runs; single agent · multi-agent system · baseline): a hard rule broken in 1 decision per run · 1 · 11 (target 0) · needs met 100% · 100% · 47% (100%) · a bed the ward cannot give 0 · 0 · 0 (0) · match 76% · 60% · 42% (85%) · claims backed by the record 91% · 100% (95%) · minutes to a bed on the 3 days 21 · 16 · 26 · $0.12 · $0.04 a case — $104 · $38 a month at 900 admissions. Equally safe; the single agent more suitable; the multi-agent system better grounded, faster to a bed and 2.7 times cheaper.
3. **Decided since Checkpoint 2, and why:** the Coordinator is code, not an agent — routing, ranking and the hard rules must be the same every time · the Judge in two layers, code first, so the Judge Agent never approves what code blocked · the multi-agent system gets 2 rounds on the whole system, like the single agent: round 1, the Department Agent on Sonnet, kept; round 2 reverted — it fixed nothing.
4. **What is stuck:** the gap in suitability. Two side checks, kept out of the comparison, found its cause: every agent on Opus — the same match at 3 times the cost; a Judge Agent check for a better option left — no decision fixed. What a ward says only in free text ("a bed frees in about an hour") does not reach the decision, because the parts hand over closed fields; the same choice keeps every claim grounded and every step traceable.
5. **Next three steps:** the GDPR and the EU AI Act — how the system meets each, checked against the official texts · the demo cases from the demo screen · the final upload, every file reviewed first.
