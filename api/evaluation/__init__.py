"""
Evaluation Framework for the Clinical Trial Semantic Layer.

Provides offline evaluation, golden dataset management, and continuous quality
monitoring for both the agent layer and the MCP tool layer.

Components:
    - eval_metrics:             Prometheus metric definitions for evaluation scores
    - golden_dataset_builder:   Extract + curate golden records from Phoenix traces
    - offline_evaluator:        Run DeepEval metrics against the golden dataset
    - argilla_client:           Push failed cases to Argilla for human review
"""
