"""Emergency department admission placement — a multi-agent system in a simulated adult hospital.

  config.py      reads the configuration files (config/)
  environment/   the simulated hospital and the simulation engine
  contracts/     the typed records the system exchanges: the patient record, the ward request and answer
  tools/         what the agents call, with permissions in code (toolbox.py): news2, rank_placements, negotiation...
  agents/        one file per agent (placeholders — the team builds them); prompts/ one prompt per agent
  orchestration/ the placement case, its steps enforced in code; an example of the wiring
  observability/ one trace per case
  baselines/     placement without agents: first free bed, rules only, simulated coordinator (today)
  evaluation/    runs deciders on the pairs and the paired cases, builds the answers, the metrics — the only
                 code that reads the expected answers
Guide for the team: docs/design/agent_interfaces.md

Owner: integrator
"""
