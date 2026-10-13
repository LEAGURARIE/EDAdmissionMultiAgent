"""Evaluation: running deciders on the evaluation pairs and paired cases, and measuring them against the expected answers.

The only code allowed to read data/answer_key/ — the decider never does.

Owner: Lea Gur-Arie
"""
from ed_admission.evaluation.case_runner import CaseResult, PairedResult, load_pairs, load_paired, run_cases, run_paired
from ed_admission.evaluation import metrics
