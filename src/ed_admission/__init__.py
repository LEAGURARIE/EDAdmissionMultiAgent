"""Emergency department admission placement — a multi-agent system in a simulated adult hospital.

  config.py      reads the configuration files (config/)
  environment/   the simulated hospital and the simulation engine
  contracts/     the typed records the system exchanges: the patient record, the ward request and answer
  tools/         what the agents call, with permissions in code (toolbox.py): news2, rank_placements, negotiation...
  agents/        the single agent: its loop, tools, budget and price check; prompts/ its prompt
  orchestration/ the placement case, its steps enforced in code; an example of the wiring
  observability/ one trace per case
  baselines/     the baseline: the Code Coordinator — today's procedure, written as code, without agents
  evaluation/    runs deciders on the pairs and the paired cases, builds the answers, the metrics — the only
                 code that reads the expected answers
Guide: docs/design/agent_interfaces.md

Owner: Lea Gur-Arie
"""
