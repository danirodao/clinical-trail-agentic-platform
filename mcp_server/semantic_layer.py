"""
Inline semantic context for Data MCP tool responses.

This module's ONLY responsibility is annotating tool response payloads with
the semantic_context envelope (field → concept + code-system mappings).

Ontology lookup tools (resolve_semantic_term, get_concept_definition, etc.)
are owned by the dedicated Semantic MCP service (semantic-mcp-server:8002).
Do NOT add lookup/disambiguation logic here.
"""

from __future__ import annotations

from typing import Any


SEMANTIC_LAYER_VERSION = "1.0.0"
ONTOLOGY_VERSION = "clinical-trials-ontology-v1"

# Minimal concept metadata needed to annotate field names in tool responses.
# The authoritative registry lives in semantic_mcp_server/ontology.py.
_CONCEPT_LABELS: dict[str, tuple[str, str]] = {
    # concept_id -> (label, code_system)
    "concept:trial.phase":       ("Clinical Trial Phase",       "internal"),
    "concept:trial.status":      ("Trial Overall Status",       "ClinicalTrials.gov"),
    "concept:patient.sex":       ("Patient Sex",                "internal"),
    "concept:patient.disposition": ("Patient Disposition Status", "internal"),
    "concept:condition.icd10":   ("Medical Condition ICD-10",   "ICD-10"),
    "concept:condition.snomed":  ("Medical Condition SNOMED CT", "SNOMED CT"),
    "concept:lab.loinc":         ("Lab Test LOINC Code",        "LOINC"),
    "concept:drug.rxnorm":       ("Drug RxNorm Code",           "RxNorm"),
    "concept:ae.meddra":         ("Adverse Event MedDRA Term",  "MedDRA"),
    "concept:ae.severity":       ("Adverse Event Severity",     "CTCAE"),
    "concept:access.level":      ("Data Access Level",          "internal"),
    "concept:cohort":            ("Patient Cohort",             "internal"),
}

FIELD_CONCEPT_MAP: dict[str, str] = {
    "phase":              "concept:trial.phase",
    "overall_status":     "concept:trial.status",
    "sex":                "concept:patient.sex",
    "disposition_status": "concept:patient.disposition",
    "icd10_code":         "concept:condition.icd10",
    "snomed_code":        "concept:condition.snomed",
    "loinc_code":         "concept:lab.loinc",
    "rxnorm_code":        "concept:drug.rxnorm",
    "meddra_pt":          "concept:ae.meddra",
    "event_term":         "concept:ae.meddra",
    "severity":           "concept:ae.severity",
    "access_level":       "concept:access.level",
    "cohort_id":          "concept:cohort",
}


def _collect_field_names(payload: Any, out: set[str]) -> None:
    if isinstance(payload, dict):
        for key, val in payload.items():
            out.add(str(key))
            _collect_field_names(val, out)
        return
    if isinstance(payload, list):
        for item in payload[:50]:
            _collect_field_names(item, out)


def build_inline_semantic_context(
    *,
    data: Any,
    metadata: dict[str, Any] | None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """
    Build the semantic_context block attached to every Data MCP tool response.
    Lets the agent interpret field meaning and code systems inline.
    """
    fields: set[str] = set()
    _collect_field_names(data, fields)
    if metadata:
        _collect_field_names(metadata, fields)

    field_semantics: list[dict[str, Any]] = []
    concept_ids: set[str] = set()

    for field_name in sorted(fields):
        concept_id = FIELD_CONCEPT_MAP.get(field_name)
        if not concept_id:
            continue
        label, code_system = _CONCEPT_LABELS.get(concept_id, (concept_id, "unknown"))
        concept_ids.add(concept_id)
        field_semantics.append(
            {
                "field": field_name,
                "concept_id": concept_id,
                "label": label,
                "code_system": code_system,
            }
        )

    return {
        "semantic_layer_version": SEMANTIC_LAYER_VERSION,
        "ontology_version": ONTOLOGY_VERSION,
        "tool": tool_name,
        "concepts": sorted(concept_ids),
        "field_semantics": field_semantics,
    }
