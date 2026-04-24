"""
Ontology registry — the authoritative in-process model that backs both the
Neo4j graph and the MCP tool responses.

The registry is the single source of truth. The Neo4j seeder reads from it,
so adding a concept here automatically propagates to the graph on next restart.
"""

from __future__ import annotations

from typing import Any

SEMANTIC_LAYER_VERSION = "1.0.0"
ONTOLOGY_VERSION = "clinical-trials-ontology-v1"

# ─────────────────────────────────────────────────────────────────────────────
# Concept registry
# ─────────────────────────────────────────────────────────────────────────────

CONCEPT_REGISTRY: dict[str, dict[str, Any]] = {
    "concept:trial.phase": {
        "label": "Clinical Trial Phase",
        "preferred_term": "trial phase",
        "definition": (
            "Regulatory stage of a clinical study: "
            "Phase 1 (safety/dosing), Phase 2 (preliminary efficacy), "
            "Phase 3 (comparative/pivotal), Phase 4 (post-market)."
        ),
        "code_system": "internal",
        "synonyms": ["phase", "study phase"],
        "allowed_values": ["Phase 1", "Phase 2", "Phase 3", "Phase 4"],
        "broader": None,
    },
    "concept:trial.status": {
        "label": "Trial Overall Status",
        "preferred_term": "overall status",
        "definition": "Current recruitment/completion state of a clinical trial.",
        "code_system": "ClinicalTrials.gov",
        "synonyms": ["trial status", "status", "recruitment status"],
        "allowed_values": [
            "Recruiting", "Completed", "Active, not recruiting",
            "Withdrawn", "Terminated", "Suspended",
        ],
        "broader": None,
    },
    "concept:patient.sex": {
        "label": "Patient Sex",
        "preferred_term": "sex",
        "definition": "Biological sex recorded as M (male) or F (female) in this dataset.",
        "code_system": "internal",
        "synonyms": ["gender", "biological sex", "sex at birth"],
        "allowed_values": ["M", "F"],
        "broader": None,
    },
    "concept:patient.disposition": {
        "label": "Patient Disposition Status",
        "preferred_term": "disposition status",
        "definition": "Current participation state of a patient in a trial.",
        "code_system": "internal",
        "synonyms": ["enrollment status", "patient status"],
        "allowed_values": ["Enrolled", "Completed", "Withdrawn", "Screen Failed"],
        "broader": None,
    },
    "concept:condition.icd10": {
        "label": "Medical Condition ICD-10 Code",
        "preferred_term": "icd10 code",
        "definition": (
            "International Classification of Diseases, 10th Revision code "
            "identifying a medical condition or diagnosis."
        ),
        "code_system": "ICD-10",
        "synonyms": ["icd", "diagnosis code", "condition code", "disease code"],
        "broader": None,
    },
    "concept:condition.snomed": {
        "label": "Medical Condition SNOMED CT Code",
        "preferred_term": "snomed code",
        "definition": "SNOMED Clinical Terms code for a medical concept.",
        "code_system": "SNOMED CT",
        "synonyms": ["snomed", "snomed ct"],
        "broader": "concept:condition.icd10",
    },
    "concept:lab.loinc": {
        "label": "Lab Test LOINC Code",
        "preferred_term": "loinc code",
        "definition": "Logical Observation Identifiers Names and Codes for lab tests and observations.",
        "code_system": "LOINC",
        "synonyms": ["lab code", "observation code", "test code", "loinc"],
        "broader": None,
    },
    "concept:drug.rxnorm": {
        "label": "Drug RxNorm Code",
        "preferred_term": "rxnorm code",
        "definition": "Normalized drug name code from the RxNorm vocabulary.",
        "code_system": "RxNorm",
        "synonyms": ["rx norm", "drug code", "medication code", "rxnorm"],
        "broader": None,
    },
    "concept:ae.meddra": {
        "label": "Adverse Event MedDRA Term",
        "preferred_term": "meddra preferred term",
        "definition": (
            "MedDRA (Medical Dictionary for Regulatory Activities) Preferred Term "
            "used to classify adverse events in clinical trials."
        ),
        "code_system": "MedDRA",
        "synonyms": ["adverse event term", "ae term", "meddra pt", "preferred term"],
        "broader": None,
    },
    "concept:ae.severity": {
        "label": "Adverse Event Severity",
        "preferred_term": "severity",
        "definition": "Graded severity of an adverse event.",
        "code_system": "CTCAE",
        "synonyms": ["grade", "ae grade", "toxicity grade"],
        "allowed_values": ["Mild", "Moderate", "Severe"],
        "broader": "concept:ae.meddra",
    },
    "concept:access.level": {
        "label": "Data Access Level",
        "preferred_term": "access level",
        "definition": (
            "Authorization scope: 'individual' allows patient-row data, "
            "'aggregate' restricts to summary statistics only (ceiling principle)."
        ),
        "code_system": "internal",
        "synonyms": ["individual access", "aggregate access", "ceiling principle"],
        "allowed_values": ["individual", "aggregate", "none"],
        "broader": None,
    },
    "concept:cohort": {
        "label": "Patient Cohort",
        "preferred_term": "cohort",
        "definition": "A named group of patients defined by one or more filter criteria.",
        "code_system": "internal",
        "synonyms": ["patient cohort", "cohort filter", "study cohort"],
        "broader": None,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Field → concept mapping
# ─────────────────────────────────────────────────────────────────────────────

FIELD_CONCEPT_MAP: dict[str, str] = {
    "phase": "concept:trial.phase",
    "overall_status": "concept:trial.status",
    "sex": "concept:patient.sex",
    "disposition_status": "concept:patient.disposition",
    "icd10_code": "concept:condition.icd10",
    "snomed_code": "concept:condition.snomed",
    "loinc_code": "concept:lab.loinc",
    "rxnorm_code": "concept:drug.rxnorm",
    "meddra_pt": "concept:ae.meddra",
    "event_term": "concept:ae.meddra",
    "severity": "concept:ae.severity",
    "access_level": "concept:access.level",
    "cohort_id": "concept:cohort",
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(value: str) -> str:
    return value.strip().lower()


def resolve_concepts(term: str, limit: int = 8) -> list[dict[str, Any]]:
    """Match a free-text term against concepts in the registry."""
    token = _normalize(term)
    if not token:
        return []

    matches: list[tuple[int, str, dict[str, Any]]] = []
    for concept_id, concept in CONCEPT_REGISTRY.items():
        label = _normalize(concept.get("label", ""))
        preferred = _normalize(concept.get("preferred_term", ""))
        synonyms = [_normalize(s) for s in concept.get("synonyms", [])]

        score = 0
        if token == preferred or token == label:
            score = 3
        elif token in synonyms:
            score = 2
        elif token in label or token in preferred:
            score = 1
        elif any(token in syn for syn in synonyms):
            score = 1

        if score:
            matches.append((score, concept_id, concept))

    matches.sort(key=lambda x: (-x[0], x[1]))
    return [
        {
            "concept_id": cid,
            "match_score": score,
            "label": concept.get("label"),
            "preferred_term": concept.get("preferred_term"),
            "definition": concept.get("definition"),
            "code_system": concept.get("code_system"),
            "allowed_values": concept.get("allowed_values", []),
            "synonyms": concept.get("synonyms", []),
        }
        for score, cid, concept in matches[:limit]
    ]


def get_cognitive_frame() -> dict[str, Any]:
    """Compact ontology frame for agent priming."""
    return {
        "semantic_layer_version": SEMANTIC_LAYER_VERSION,
        "ontology_version": ONTOLOGY_VERSION,
        "core_concepts": [
            {
                "concept_id": cid,
                "label": concept.get("label"),
                "preferred_term": concept.get("preferred_term"),
                "code_system": concept.get("code_system"),
                "allowed_values": concept.get("allowed_values", []),
            }
            for cid, concept in sorted(CONCEPT_REGISTRY.items())
        ],
        "field_concept_map": FIELD_CONCEPT_MAP,
    }
