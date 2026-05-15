"""
Ontology registry — the authoritative in-process model that backs both the
Neo4j graph and the MCP tool responses.

The registry is the single source of truth. The Neo4j seeder reads from it,
so adding a concept here automatically propagates to the graph on next restart.
"""

from __future__ import annotations

from typing import Any

SEMANTIC_LAYER_VERSION = "1.0.0"
ONTOLOGY_VERSION = "clinical-trials-ontology-v2"

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
        "broader": "concept:ae.soc",
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

    # ── Priority 1: Critical gaps ──────────────────────────────────────────

    "concept:vitals.type": {
        "label": "Vital Sign Type",
        "preferred_term": "vital sign",
        "definition": (
            "A physiological measurement collected during clinical visits. "
            "Standard types: systolic blood pressure (SYSBP), diastolic blood "
            "pressure (DIABP), heart rate (HR), temperature (TEMP), weight (WEIGHT)."
        ),
        "code_system": "CDISC CDASH",
        "synonyms": ["vital", "vitals", "vital signs", "blood pressure", "heart rate"],
        "allowed_values": ["SYSBP", "DIABP", "HR", "TEMP", "WEIGHT"],
        "broader": None,
    },
    "concept:trial.therapeutic_area": {
        "label": "Therapeutic Area",
        "preferred_term": "therapeutic area",
        "definition": "The medical specialty or disease domain targeted by a clinical trial.",
        "code_system": "MeSH",
        "synonyms": ["indication area", "disease area", "therapy area", "specialty"],
        "allowed_values": ["Oncology", "Cardiology", "Endocrinology"],
        "broader": None,
    },
    "concept:lab.test_name": {
        "label": "Laboratory Test Name",
        "preferred_term": "lab test name",
        "definition": "Human-readable name of a laboratory test (e.g., Hemoglobin, HbA1c, Creatinine).",
        "code_system": "internal",
        "synonyms": ["lab name", "test name", "laboratory test", "lab test"],
        "broader": "concept:lab.loinc",
    },

    # ── Priority 2: Study design & AE enrichment ──────────────────────────

    "concept:trial.study_type": {
        "label": "Study Type",
        "preferred_term": "study type",
        "definition": "The nature of the investigation: Interventional, Observational, or Expanded Access.",
        "code_system": "CDISC",
        "synonyms": ["study design type", "investigation type"],
        "allowed_values": ["Interventional", "Observational", "Expanded Access"],
        "broader": None,
    },
    "concept:trial.allocation": {
        "label": "Allocation Method",
        "preferred_term": "allocation",
        "definition": "Method of assigning participants to arms: Randomized or Non-Randomized.",
        "code_system": "CDISC",
        "synonyms": ["randomization", "randomization method"],
        "allowed_values": ["Randomized", "Non-Randomized", "N/A"],
        "broader": None,
    },
    "concept:trial.masking": {
        "label": "Masking / Blinding",
        "preferred_term": "masking",
        "definition": "Blinding strategy to reduce bias in outcome assessment.",
        "code_system": "CDISC",
        "synonyms": ["blinding", "blind", "double blind", "open label"],
        "allowed_values": [
            "None (Open Label)", "Single", "Double", "Triple", "Quadruple",
        ],
        "broader": None,
    },
    "concept:trial.arm_type": {
        "label": "Treatment Arm Type",
        "preferred_term": "arm type",
        "definition": "Classification of a treatment arm within a clinical trial.",
        "code_system": "CDISC",
        "synonyms": ["arm", "treatment arm", "study arm", "arm label"],
        "allowed_values": [
            "Experimental", "Active Comparator",
            "Placebo Comparator", "Sham Comparator", "No Intervention",
        ],
        "broader": None,
    },
    "concept:ae.soc": {
        "label": "MedDRA System Organ Class",
        "preferred_term": "system organ class",
        "definition": (
            "The highest level of the MedDRA hierarchy grouping adverse events "
            "by the body system affected (e.g., Gastrointestinal disorders, "
            "Nervous system disorders)."
        ),
        "code_system": "MedDRA",
        "synonyms": ["soc", "organ class", "body system", "meddra soc"],
        "broader": None,
    },
    "concept:ae.causality": {
        "label": "Adverse Event Causality",
        "preferred_term": "causality",
        "definition": "Assessment of the causal relationship between the study drug and an adverse event.",
        "code_system": "CDISC",
        "synonyms": ["causal relationship", "relatedness", "drug relationship"],
        "allowed_values": ["Related", "Possibly Related", "Unlikely Related", "Not Related"],
        "broader": None,
    },
    "concept:ae.outcome": {
        "label": "Adverse Event Outcome",
        "preferred_term": "ae outcome",
        "definition": "The resolution status of an adverse event at the time of reporting.",
        "code_system": "CDISC",
        "synonyms": ["event outcome", "resolution", "ae resolution"],
        "allowed_values": ["Recovered", "Recovering", "Not Recovered", "Fatal", "Unknown"],
        "broader": None,
    },
    "concept:trial.outcome_type": {
        "label": "Outcome Measure Type",
        "preferred_term": "outcome type",
        "definition": "Classification of a study endpoint as primary, secondary, or other.",
        "code_system": "ClinicalTrials.gov",
        "synonyms": ["endpoint type", "measure type", "primary endpoint", "secondary endpoint"],
        "allowed_values": ["primary", "secondary", "other"],
        "broader": None,
    },
    "concept:trial.eligibility_type": {
        "label": "Eligibility Criteria Type",
        "preferred_term": "criteria type",
        "definition": "Whether a criterion defines who CAN (inclusion) or CANNOT (exclusion) participate.",
        "code_system": "ClinicalTrials.gov",
        "synonyms": ["inclusion", "exclusion", "eligibility", "enrollment criteria"],
        "allowed_values": ["inclusion", "exclusion"],
        "broader": None,
    },

    # ── Priority 3: Demographics, geography, drug details, trial metadata ─

    "concept:patient.age": {
        "label": "Patient Age",
        "preferred_term": "age",
        "definition": "Age of the patient at the time of enrollment, in years.",
        "code_system": "internal",
        "synonyms": ["patient age", "age at enrollment"],
        "broader": None,
    },
    "concept:patient.race": {
        "label": "Patient Race",
        "preferred_term": "race",
        "definition": "Self-reported racial category per OMB standards.",
        "code_system": "OMB",
        "synonyms": ["racial category", "patient race"],
        "allowed_values": [
            "White", "Black or African American", "Asian",
            "American Indian or Alaska Native",
            "Native Hawaiian or Other Pacific Islander", "Other", "Unknown",
        ],
        "broader": None,
    },
    "concept:patient.ethnicity": {
        "label": "Patient Ethnicity",
        "preferred_term": "ethnicity",
        "definition": "Self-reported ethnicity per OMB standards.",
        "code_system": "OMB",
        "synonyms": ["ethnic group", "patient ethnicity"],
        "allowed_values": ["Hispanic or Latino", "Not Hispanic or Latino", "Unknown"],
        "broader": None,
    },
    "concept:patient.arm": {
        "label": "Patient Assigned Arm",
        "preferred_term": "arm assigned",
        "definition": "The treatment arm to which a patient was randomized or assigned.",
        "code_system": "internal",
        "synonyms": ["assigned arm", "treatment group", "patient arm"],
        "broader": "concept:trial.arm_type",
    },
    "concept:site.region": {
        "label": "Geographic Region",
        "preferred_term": "region",
        "definition": "Geographic region where a trial site or patient is located.",
        "code_system": "internal",
        "synonyms": ["geographic region", "site region", "study region"],
        "allowed_values": ["North America", "Europe", "Asia-Pacific", "Latin America"],
        "broader": None,
    },
    "concept:site.country": {
        "label": "Country",
        "preferred_term": "country",
        "definition": "Country where a trial site is located or where a patient resides.",
        "code_system": "ISO 3166",
        "synonyms": ["nation", "site country", "patient country"],
        "broader": "concept:site.region",
    },
    "concept:drug.route": {
        "label": "Drug Administration Route",
        "preferred_term": "route",
        "definition": "The path by which a drug is administered (e.g., Oral, Intravenous, Subcutaneous).",
        "code_system": "NCI Thesaurus",
        "synonyms": ["administration route", "route of administration"],
        "allowed_values": ["Oral", "Intravenous", "Subcutaneous", "Intramuscular", "Topical"],
        "broader": "concept:drug.rxnorm",
    },
    "concept:drug.intervention_type": {
        "label": "Intervention Type",
        "preferred_term": "intervention type",
        "definition": "Category of the clinical intervention being tested.",
        "code_system": "CDISC",
        "synonyms": ["drug type", "treatment type", "therapy type"],
        "allowed_values": [
            "Drug", "Biological", "Device", "Procedure",
            "Radiation", "Behavioral", "Dietary Supplement",
        ],
        "broader": None,
    },
    "concept:trial.sponsor": {
        "label": "Lead Sponsor",
        "preferred_term": "lead sponsor",
        "definition": "The organization primarily responsible for conducting the clinical trial.",
        "code_system": "internal",
        "synonyms": ["sponsor", "primary sponsor", "trial sponsor"],
        "broader": None,
    },
    "concept:trial.enrollment": {
        "label": "Enrollment Count",
        "preferred_term": "enrollment count",
        "definition": "The total number of participants enrolled (or planned) in a clinical trial.",
        "code_system": "internal",
        "synonyms": ["enrollment", "sample size", "number of patients", "n"],
        "broader": None,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Field → concept mapping
# ─────────────────────────────────────────────────────────────────────────────

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
    # ── Arm / Intervention fields ──
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
