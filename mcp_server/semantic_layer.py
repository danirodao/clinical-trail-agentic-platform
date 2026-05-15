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
ONTOLOGY_VERSION = "clinical-trials-ontology-v2"

# Minimal concept metadata needed to annotate field names in tool responses.
# The authoritative registry lives in semantic_mcp_server/ontology.py.
_CONCEPT_LABELS: dict[str, tuple[str, str]] = {
    # concept_id -> (label, code_system)
    # ── Trial ──
    "concept:trial.phase":           ("Clinical Trial Phase",       "internal"),
    "concept:trial.status":          ("Trial Overall Status",       "ClinicalTrials.gov"),
    "concept:trial.therapeutic_area":("Therapeutic Area",           "MeSH"),
    "concept:trial.study_type":      ("Study Type",                 "CDISC"),
    "concept:trial.allocation":      ("Allocation Method",          "CDISC"),
    "concept:trial.masking":         ("Masking / Blinding",         "CDISC"),
    "concept:trial.arm_type":        ("Treatment Arm Type",         "CDISC"),
    "concept:trial.sponsor":         ("Lead Sponsor",               "internal"),
    "concept:trial.enrollment":      ("Enrollment Count",           "internal"),
    "concept:trial.outcome_type":    ("Outcome Measure Type",       "ClinicalTrials.gov"),
    "concept:trial.eligibility_type":("Eligibility Criteria Type",  "ClinicalTrials.gov"),
    # ── Patient ──
    "concept:patient.sex":           ("Patient Sex",                "internal"),
    "concept:patient.age":           ("Patient Age",                "internal"),
    "concept:patient.race":          ("Patient Race",               "OMB"),
    "concept:patient.ethnicity":     ("Patient Ethnicity",          "OMB"),
    "concept:patient.disposition":   ("Patient Disposition Status", "internal"),
    "concept:patient.arm":           ("Patient Assigned Arm",       "internal"),
    # ── Condition ──
    "concept:condition.icd10":       ("Medical Condition ICD-10",   "ICD-10"),
    "concept:condition.snomed":      ("Medical Condition SNOMED CT","SNOMED CT"),
    # ── Lab / Vitals ──
    "concept:lab.loinc":             ("Lab Test LOINC Code",        "LOINC"),
    "concept:lab.test_name":         ("Laboratory Test Name",       "internal"),
    "concept:vitals.type":           ("Vital Sign Type",            "CDISC CDASH"),
    # ── Drug ──
    "concept:drug.rxnorm":           ("Drug RxNorm Code",           "RxNorm"),
    "concept:drug.route":            ("Drug Administration Route",  "NCI Thesaurus"),
    "concept:drug.intervention_type":("Intervention Type",          "CDISC"),
    # ── Adverse events ──
    "concept:ae.meddra":             ("Adverse Event MedDRA Term",  "MedDRA"),
    "concept:ae.soc":                ("MedDRA System Organ Class",  "MedDRA"),
    "concept:ae.severity":           ("Adverse Event Severity",     "CTCAE"),
    "concept:ae.causality":          ("Adverse Event Causality",    "CDISC"),
    "concept:ae.outcome":            ("Adverse Event Outcome",      "CDISC"),
    # ── Geography ──
    "concept:site.region":           ("Geographic Region",          "internal"),
    "concept:site.country":          ("Country",                    "ISO 3166"),
    # ── Access control ──
    "concept:access.level":          ("Data Access Level",          "internal"),
    "concept:cohort":                ("Patient Cohort",             "internal"),
}

FIELD_CONCEPT_MAP: dict[str, str] = {
    # ── Trial-level fields ──
    "phase":              "concept:trial.phase",
    "overall_status":     "concept:trial.status",
    "therapeutic_area":   "concept:trial.therapeutic_area",
    "study_type":         "concept:trial.study_type",
    "allocation":         "concept:trial.allocation",
    "masking":            "concept:trial.masking",
    "lead_sponsor":       "concept:trial.sponsor",
    "enrollment_count":   "concept:trial.enrollment",
    # ── Arm / Intervention ──
    "arm_type":           "concept:trial.arm_type",
    "arm_label":          "concept:trial.arm_type",
    "arm_assigned":       "concept:patient.arm",
    "intervention_type":  "concept:drug.intervention_type",
    "route":              "concept:drug.route",
    "rxnorm_code":        "concept:drug.rxnorm",
    # ── Eligibility / Outcomes ──
    "criteria_type":      "concept:trial.eligibility_type",
    "outcome_type":       "concept:trial.outcome_type",
    # ── Patient demographics ──
    "sex":                "concept:patient.sex",
    "age":                "concept:patient.age",
    "race":               "concept:patient.race",
    "ethnicity":          "concept:patient.ethnicity",
    "disposition_status": "concept:patient.disposition",
    "country":            "concept:site.country",
    "region":             "concept:site.region",
    # ── Conditions ──
    "icd10_code":         "concept:condition.icd10",
    "snomed_code":        "concept:condition.snomed",
    # ── Laboratory ──
    "loinc_code":         "concept:lab.loinc",
    "test_name":          "concept:lab.test_name",
    # ── Vital signs ──
    "vital_type":         "concept:vitals.type",
    # ── Adverse events ──
    "meddra_pt":          "concept:ae.meddra",
    "event_term":         "concept:ae.meddra",
    "ae_term":            "concept:ae.meddra",
    "meddra_soc":         "concept:ae.soc",
    "severity":           "concept:ae.severity",
    "causality":          "concept:ae.causality",
    "outcome":            "concept:ae.outcome",
    # ── Access control ──
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
