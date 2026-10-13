# CHARTER — Multi-Agent ED Admission Placement

**Author:** Lea Gur-Arie · **Date:** 2026-10-04, updated 2026-10-10 · BIU DS23 final project

## 1. Problem & user

The **ED shift manager** placing admitted patients faces long waits — about **4 h** on average in an Israeli tertiary hospital, with **two thirds** waiting over 2 h **[S3]**, while mortality rises from **2.5% to 4.5%** as boarding grows from under 2 h to over 12 h **[S1]** — and in a large US academic center **19.6%** of patients were placed in a ward that does not fit their problem, with **22.8%** longer stays **[S2]**; our system weighs clinical fit, observation and isolation needs, and bed availability together to find the optimal ward bed, shorten the wait, and record a full decision trail for every placement.

## 2. Success metric (set before building)

**What is measured:** 118 placement decisions with clinically approved, frozen expected answers, and 3 scripted days with 39 admissions.

**The baseline — the Code Coordinator:** today's procedure, written as code, with no agents. It reads the record's structured fields (vital signs, chief complaint, the ward the physician requested), looks at the wards' reports, **phones each ward** (5 minutes a call) to ask what it can really give, and ranks the beds by the rules. It does **not** read the physician's free text. The system is judged against it.

| | The question | Target | Code Coordinator |
|---|---|---|---|
| **Safe** | Decisions that put a patient in an unsafe bed (a hard rule broken) | 0 (0%) | 45 of 118 (38%) |
| | Patients who need observation or isolation and got it, or were escalated | 56 of 56 (100%) | 18 of 56 (32%) |
| | Placements in a bed the ward cannot really give | 0 (0%) | 0 of 100 (0%) |
| **Suitable** | Decisions that match the expected answer | ≥ 100 of 118 (85%) | 45 of 118 (38%) |
| **Fast** | Minutes from the admission decision to a bed | fewer than the baseline | 26 |

**Tracked:** 100% of cases with a complete trace · ≥ 95% of claims backed by a quote that is in the record. Baseline measured 2026-10-10. **Changed 2026-10-10:** judged against the Code Coordinator instead of "first free bed", which no hospital uses; its two relative targets (off-service, 20% faster) are replaced by the targets above.

## 3. Data + plan B

**Synthetic only:** a simulated adult hospital (5 wards; regular and observation beds, one isolation room per ward) and 74 synthetic ED records in Hebrew with English medical terms. **Size:** 22 ward states with private ward notes; 118 decisions (±6.5% precision at 85%; 0 violations in 118 → true rate ≤ 2.5%); 3 scripted days. Every case tests both reading the record and coordinating with the wards. **License:** our own, no real patient data. **Proof:** the baseline runs end to end, in Colab too. **Plan B:** fewer, harder hand-written cases.

## 4. Architecture

```text
Simulated hospital ──(trigger: admission decision, record frozen)──▶ Coordinator
   Coordinator ⇄ Patient Agent      reads the record: target ward, level of care, bed type, flags, quotes
   Coordinator ⇄ Department Agent   one per ward: what it can really give — its report and private notes
   Coordinator                      sees every report and both agents' answers, never the record; decides
   No handoff between the agents: only the Coordinator calls an agent, and every answer returns to it.
▼ code: rank_placements (ladder, hard rules) · decide (a decision that breaks a hard rule is refused)
▼ recommendation → shift manager approval → bed assigned
```

## 5. The agent's chair

- **Decides what code cannot:** the Patient Agent reads the free text; the Department Agent reads its ward's notes; the Coordinator weighs the wards' answers and decides. Ranking and the hard rules stay in code.
- **Tools**, each agent only its own, enforced in code: Patient — `read_patient_record`, `compute_news2`, `clinical_lists` · Department — `department_view` · Coordinator — `assess_patient`, `all_department_status`, `rank_placements`, `ask_ward`, `hold_bed`, `decide`, `escalate_to_human`.
- **On failure:** one retry, then escalation to the shift manager — never a guess. **Works without agents:** the Code Coordinator.

## 6. Risks & cut version

- **Misreading the record** → closed lists, every quote checked against the record, the hard rules checked in code before any decision. **Cost or latency** → agents only on triggers; request limits; per-case budget. **An agent that sees the expected answers** → only the evaluation code reads them, guarded by tests; an agent gets the patient's id and the decision time, nothing more.

**Cut version:** *"If only two weeks remain, we drop the single-agent comparison, and submit the two agents and the Coordinator on the cases and the scripted days."*

## 7. Milestones & hats

A personal project: Lea Gur-Arie holds every hat — product, agents, model, data.

**18.10:** cases, days, baseline, first metrics · **30.10:** agents integrated, metrics vs. the baseline, 1-min demo · **01.11:** freeze · **12.11:** final.

## References — full citations in `docs/design/sources.md`

- **[S1]** Singer AJ, et al. *Academic Emergency Medicine*, 2011. PubMed [22168198](https://pubmed.ncbi.nlm.nih.gov/22168198/)
- **[S2]** Song H, Tucker AL, et al. *Management Science*, 2020;66(9):3825–3842. [doi:10.1287/mnsc.2019.3395](https://doi.org/10.1287/mnsc.2019.3395)
- **[S3]** Greenberg et al. *Israel Journal of Health Policy Research*, 2018. [PMC6247762](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247762/)
