"""Contracts: the typed input and output of every agent and tool.

Every hand-off between agents goes through a contract defined here.
A contract change affects the whole team, so it is reviewed on its own.

Built: patient_record.py — the medical record the system may see · negotiation.py — what the Coordinator asks a
ward, and what the ward's Department Agent answers. The decision contract: environment/decision.py.

Owner: integrator
"""
