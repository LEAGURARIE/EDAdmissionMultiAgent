# CHARTER — Multi-Agent ED Admission Placement

**Team:** Lea Gur-Arie · Ron Levi · Galit Neufeld Krushinsky · Amir Shmaryahu · **Date:** 2026-10-04 · BIU DS23 final project

## 1. Problem & user

The **ED shift manager** placing admitted patients faces long waits — about **4 h** on average in an Israeli tertiary hospital, with **two thirds** waiting over 2 h **[S3]**, while mortality rises from **2.5% to 4.5%** as boarding grows from under 2 h to over 12 h **[S1]** — and in a large US academic center **19.6%** of patients were placed in a ward that does not fit their problem, with **22.8%** longer stays **[S2]**; our system weighs clinical fit, observation and isolation needs, and bed availability together to find the optimal ward bed, shorten the wait, and record a full decision trail for every placement.

## 2. Success metric (set before building)

On **60 case files** with clinically approved expected answers + **3 scripted days**, vs. two baselines on the same set: "first free bed" and rules only (no agents).

- **Quality of care:** **0** hard-rule violations · **≥ 85%** match (ward + bed type) · off-service placements **≤ half** of the first-free-bed baseline · **100%** of observation/isolation needs met or escalated.
- **Time:** admission decision → suitable bed **≥ 20% shorter** than first free bed.
- **Tracking:** **100%** of cases with a complete trace (agent inputs/outputs, ranking, critic verdict, human decision + reason) · **≥ 95%** groundedness (claims backed by a quote).

## 3. Data + plan B

**Synthetic only:** a simulated adult hospital (5 wards; regular and observation beds, one isolation room per ward) and synthetic ED records written by the team. **Size:** 60 case files (all must-have coverage-matrix situations; ±9% precision at 85%; 0 violations in 60 → true rate ≤ 5%) + 3 scripted days. **License:** our own, no real patient data. **Proof:** the engine already runs `data/scenarios/busy_day.yaml` with passing tests. **Plan B:** if generated records are too uniform, hand-write fewer, harder cases with a clinical reviewer.

## 4. Architecture

```text
Simulated hospital ──(trigger: admission decision, record frozen)──▶ Coordinator Agent
   patient side:  Clinical Problem · Severity · Supervision · Isolation
   hospital side: Department Agent (one instance per ward, in parallel)
▼ rank_placements (code): hard rules → target ward's proximity ladder → ranked options
▼ Safety Critic Agent → recommendation → shift manager approval → bed assigned
```

## 5. The agent's chair

- **Decides what code cannot:** reads free-text records to set the problem and target ward, escalates severity above NEWS2 with a quote, judges observation and isolation, and makes conditional ward offers. Ranking stays in code.
- **Tools:** `read_patient_record`, `compute_news2`, `get_department_status` (bound to one ward), `rank_placements`, `hold_bed`/`release_hold`, `escalate_to_human` — each agent only its own, enforced in tool code.
- **On failure:** one retry, then escalation to the shift manager — never a guess. **Works without agents:** the requested ward, or a fixed problem-to-ward table, goes straight into `rank_placements` with the same hard rules.

## 6. Risks & cut version

- **Misclassified problem or ward** → closed lists, mandatory quotes, low confidence → human, Safety Critic Agent. **Cost or latency** → agents only on triggers; per-case budget. **Different team environments** → shared repo, one Colab notebook, pinned requirements, Colab Secrets.

**Cut version:** *"If only two weeks remain, we drop the Department Agents' negotiation and the memory, and submit the patient-side agents + `rank_placements` + Safety Critic Agent on the scripted days."*

## 7. Milestones & hats

| Who | Hat | Responsible for (end to end: prompt, contract, tests, eval cases) |
|---|---|---|
| Lea Gur-Arie | Product | **Coordinator Agent (orchestrator)**; simulation environment, `rank_placements`, tools; README, metrics, demo, slides |
| Ron Levi | Agent | Clinical Problem Agent, Safety Critic Agent; cross-agent safety rules |
| Galit Neufeld Krushinsky | Model | Severity Agent, Supervision Agent; prompts and model choice |
| Amir Shmaryahu | Data | Isolation Agent, Department Agent; case files and scripted days |

**18.10:** case files, days, rules-only baseline, first metrics · **30.10:** agents integrated, metrics vs. baselines, 1-min demo · **01.11:** freeze · **12.11:** final.

## References

- **[S1]** Singer AJ, et al. The association between length of emergency department boarding and mortality. *Academic Emergency Medicine*. 2011. PubMed [22168198](https://pubmed.ncbi.nlm.nih.gov/22168198/)
- **[S2]** Song H, Tucker AL, et al. Capacity pooling in hospitals: the hidden consequences of off-service placement. *Management Science*. 2020;66(9):3825–3842. [doi:10.1287/mnsc.2019.3395](https://doi.org/10.1287/mnsc.2019.3395)
- **[S3]** Greenberg et al. Reduced hospitalization rates are not associated with increased mortality or readmission rates in an Israeli emergency department. *Israel Journal of Health Policy Research*. 2018. [PMC6247762](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247762/)
