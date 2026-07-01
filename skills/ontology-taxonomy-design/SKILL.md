---
name: ontology-taxonomy-design
description: Design ontologies and taxonomies for enterprise AI — hierarchy design, relationship types, versioning, governance, and alignment with standards (FHIR, OMOP, SKOS, OWL)
triggers:
  - design ontology
  - design taxonomy
  - ontology for AI
  - taxonomy for agents
  - semantic hierarchy
  - concept registry
  - ontology versioning
  - ontology governance
  - align with FHIR
  - align with OMOP
  - SKOS ontology
  - OWL ontology
---

# Ontology & Taxonomy Design

You are an enterprise architect specialized in ontology and taxonomy design for AI systems. Follow this framework.

## Step 1: Choose the Ontology Architecture

```
ONTOLOGY ARCHITECTURE DECISION TREE
───────────────────────────────────

Need real-time querying?
├─ YES → Graph-based (Neo4j / RDF triple store)
│        • Cypher / SPARQL queries
│        • Relationship traversal
│        • Inference engine optional
│
└─ NO → Registry-based (JSON / YAML / DB)
         • Fast lookup, no traversal needed
         • Simpler to version and distribute
         • Good for field→concept mappings

Need formal reasoning?
├─ YES → OWL + Reasoner (Pellet / HermiT)
│        • Class hierarchies, restrictions
│        • Automated classification
│        • Consistency checking
│
└─ NO → SKOS (Simple Knowledge Organization)
         • Broader/narrower, related
         • Labels, definitions, notes
         • Lightweight, human-friendly
```

## Step 2: Design the Concept Schema

```python
# CONCEPT SCHEMA (minimum viable)
CONCEPT_SCHEMA = {
    "concept_id": "string (unique, immutable)",
    "label": "string (human-readable)",
    "preferred_term": "string (primary name)",
    "alternate_terms": ["synonym1", "synonym2"],
    "definition": "string (1-3 sentences)",
    "code_system": "LOINC | SNOMED | RxNorm | ICD-10 | MeSH | Internal",
    "code": "string (code in the system)",
    "domain": "clinical | administrative | genomic | operational",
    "steward": "team or person responsible",
    "version": "semver",
    "status": "active | deprecated | draft",
    "broader_concept": "concept_id or null",
    "narrower_concepts": ["concept_id"],
    "related_concepts": ["concept_id"],
    "field_mappings": ["field_name in data model"],
    "allowed_values": ["enum values or null"],
    "validation_rule": "regex or null",
    "created": "ISO datetime",
    "last_updated": "ISO datetime"
}
```

## Step 3: Design Relationship Types

```
RELATIONSHIP TAXONOMY
─────────────────────

HIERARCHICAL
├─ broader_than / narrower_than (SKOS)
├─ is_a / subClassOf (OWL)
├─ part_of / has_part (mereology)
└─ member_of / has_member (collection)

ASSOCIATIVE
├─ related_to (SKOS)
├─ caused_by / causes (causality)
├─ treated_by / treats (therapeutic)
├─ measured_by / measures (observation)
├─ occurs_in / has_occurrence (context)
└─ contraindicated_with (safety)

EQUIVALENCE
├─ same_as (OWL)
├─ exact_match / close_match (SKOS)
├─ maps_to / mapped_from (crosswalk)
└─ equivalent_to (semantic)

VERSIONING
├─ replaces / replaced_by
├─ deprecates / deprecated_by
└─ derives_from / derived_into
```

## Step 4: Design Ontology Governance

```
GOVERNANCE WORKFLOW
───────────────────

PROPOSE → REVIEW → APPROVE → PUBLISH → MONITOR → DEPRECATE

PROPOSE
- Submit concept with schema filled
- Include use case and impact analysis
- Link to data model fields affected

REVIEW (Ontology Board)
- Check uniqueness (no duplicates)
- Validate against standards (FHIR, OMOP)
- Assess downstream impact
- Score: approved / needs-revision / rejected

APPROVE
- Assign concept_id (immutable)
- Set version = 1.0.0
- Status = active
- Record steward and approval date

PUBLISH
- Update concept registry
- Sync to Neo4j graph (if applicable)
- Notify downstream consumers
- Update field→concept mappings

MONITOR
- Usage metrics (queries referencing concept)
- Deprecation candidates (0 usage in 6 months)
- Conflict detection (overlapping definitions)

DEPRECATE
- Status = deprecated
- Add replaces/replaced_by links
- Grace period: 90 days before removal
- Migration guide for consumers
```

## Step 5: Align with Industry Standards

```
STANDARDS ALIGNMENT MATRIX
──────────────────────────

┌──────────────┬──────────────────┬─────────────────┐
│ Standard     │ Best For         │ Mapping Pattern │
├──────────────┼──────────────────┼─────────────────┤
│ FHIR         │ Clinical data    │ concept →       │
│              │ exchange         │ FHIR Resource   │
├──────────────┼──────────────────┼─────────────────┤
│ OMOP CDM     │ Observational    │ concept →       │
│              │ research         │ OMOP concept_id │
├──────────────┼──────────────────┼─────────────────┤
│ SKOS         │ Knowledge org   │ Native format   │
│              │ systems          │                 │
├──────────────┼──────────────────┼─────────────────┤
│ OWL          │ Formal reasoning │ Native format   │
├──────────────┼──────────────────┼─────────────────┤
│ SNOMED CT    │ Clinical terms   │ concept.code    │
│              │                  │ = SNOMED code   │
├──────────────┼──────────────────┼─────────────────┤
│ LOINC        │ Lab/observations │ concept.code    │
│              │                  │ = LOINC code    │
├──────────────┼──────────────────┼─────────────────┤
│ RxNorm       │ Medications      │ concept.code    │
│              │                  │ = RxNorm code   │
├──────────────┼──────────────────┼─────────────────┤
│ ICD-10       │ Diagnoses        │ concept.code    │
│              │                  │ = ICD-10 code   │
├──────────────┼──────────────────┼─────────────────┤
│ MeSH         │ Literature       │ concept →       │
│              │ indexing         │ MeSH heading    │
├──────────────┼──────────────────┼─────────────────┤
│ Dublin Core  │ Metadata         │ concept →       │
│              │                  │ DC properties   │
└──────────────┴──────────────────┴─────────────────┘
```

## Step 6: Output the Ontology Blueprint

```
ONTOLOGY BLUEPRINT: [Domain Name]
═══════════════════════════════════

ARCHITECTURE
- Storage: [Neo4j / RDF Store / JSON Registry / Hybrid]
- Query language: [Cypher / SPARQL / Python dict lookup]
- Reasoning: [None / OWL Reasoner / Custom rules]
- Version: [semver]

CONCEPT INVENTORY
- Total concepts: [N]
- Active: [N] | Draft: [N] | Deprecated: [N]
- Code systems used: [list]
- Avg synonyms per concept: [N]

RELATIONSHIP DENSITY
- Hierarchical edges: [N]
- Associative edges: [N]
- Equivalence edges: [N]
- Crosswalks to standards: [N]

GOVERNANCE
- Ontology Board: [members / cadence]
- Review SLA: [N days]
- Deprecation grace period: [N days]
- Usage monitoring: [enabled / disabled]

STANDARDS ALIGNMENT
┌──────────────┬──────────┬──────────┐
│ Standard     │ Concepts │ Coverage │
├──────────────┼──────────┼──────────┤
│ FHIR         │ [N]      │ [%]      │
│ OMOP         │ [N]      │ [%]      │
│ SNOMED       │ [N]      │ [%]      │
│ LOINC        │ [N]      │ [%]      │
│ RxNorm       │ [N]      │ [%]      │
│ ICD-10       │ [N]      │ [%]      │
└──────────────┴──────────┴──────────┘

VERSION HISTORY
- v1.0.0: [date] — Initial release ([N] concepts)
- v1.1.0: [date] — Added [domain] ([N] concepts)
- v2.0.0: [date] — Major restructure, [N] deprecated
```

## Rules

- Every concept must have a unique, immutable concept_id — never reuse IDs
- Always define broader/narrower relationships — flat lists are not ontologies
- Align with at least one industry standard (FHIR, OMOP, or SKOS) — don't invent from scratch
- Version with semver: MAJOR for breaking changes, MINOR for additions, PATCH for fixes
- Deprecate, never delete — downstream systems depend on concept stability
- Governance board is mandatory for ontologies with >50 concepts
- Crosswalks to external standards must be maintained and tested on every version
- Field→concept mappings must be validated against the actual data model schema