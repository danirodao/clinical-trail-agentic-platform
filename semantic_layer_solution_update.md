# Semantic Layer as a First-Class Citizen

## Why this update
This platform already has a data mesh and a graph store, but semantic meaning is currently implicit in tool descriptions and prompt text. This update makes semantics explicit, machine-readable, and always available during agent reasoning.

## Target state
1. Ontology and semantic model are first-class runtime artifacts.
2. Agent uses ontology as a cognitive frame before data retrieval.
3. Every MCP data tool response carries semantic context inline.
4. Knowledge graph is used as a live ontology and concept relationship store.
5. Semantic capability is exposed through MCP tools (with an option for a dedicated Semantic MCP service).

## What is now implemented in this repository

### 1) Inline semantic context in all MCP responses
- Shared response utility now injects `semantic_context` into every tool output.
- Implemented in: mcp_server/utils.py
- Semantic context includes:
  - `semantic_layer_version`
  - `ontology_version`
  - mapped `concepts`
  - `field_semantics` (field -> concept + code system)

### 2) Seed ontology + semantic model runtime module
- Added semantic layer primitives in: mcp_server/semantic_layer.py
- Includes:
  - curated `CONCEPT_REGISTRY`
  - `FIELD_CONCEPT_MAP`
  - ambiguity resolver `resolve_concepts(...)`
  - cognitive frame payload `get_cognitive_frame()`

### 3) New MCP semantic tools
- Added tool module: mcp_server/tools/semantic_layer.py
- New tools:
  - `get_semantic_cognitive_frame`
  - `resolve_semantic_term`
  - `get_concept_definition`
- Registered in MCP server startup: mcp_server/server.py

### 4) Agent prompt protocol update
- Added explicit semantic operating rules in: api/agent/prompts.py
- Agent is now instructed to:
  - use semantic tools when terms are ambiguous
  - consume `semantic_context` from every tool response

## Enterprise architecture recommendation

### Recommended topology
Use two MCP servers in production:
1. Data MCP (existing): retrieval and analytics tools.
2. Semantic MCP (new service): ontology, taxonomy, concept resolution, term governance, mapping lineage.

This separation improves:
- governance boundaries
- release cadence (ontology updates independent of query tools)
- ownership model (data engineering vs semantics/governance team)
- scalability and lifecycle management

### Semantic MCP responsibilities
- Concept registry APIs
- Synonym and ambiguity resolution
- Cross-code-system mappings (ICD-10, SNOMED CT, LOINC, RxNorm, MedDRA)
- Semantic compatibility checks across tools
- Versioned ontology publication and deprecation notices

## Knowledge graph as live ontology store

### Current and next
- Current graph holds clinical entity relationships.
- Next step is adding ontology nodes/edges and governance metadata:
  - Concept nodes: `:Concept {concept_id, label, code_system, version}`
  - Term nodes: `:Term {term, language, normalized}`
  - Mapping edges: `(:Term)-[:REFERS_TO]->(:Concept)`, `(:Concept)-[:NARROWER_THAN]->(:Concept)`
  - Provenance edges: `(:Concept)-[:DEFINED_IN]->(:OntologyRelease)`

### Runtime behavior
1. Agent receives user query.
2. If ambiguity detected, it calls Semantic MCP `resolve_semantic_term`.
3. Agent selects data tools with clarified concept meaning.
4. Data MCP response returns data + inline semantic context.
5. Agent synthesizes answer with semantic grounding and code-system-aware wording.

## Contract for tool responses
All data tools should follow this envelope:

```json
{
  "status": "success",
  "data": {"...": "..."},
  "metadata": {"...": "..."},
  "semantic_context": {
    "semantic_layer_version": "1.0.0",
    "ontology_version": "clinical-trials-ontology-v1",
    "concepts": ["concept:trial.phase"],
    "field_semantics": [
      {
        "field": "phase",
        "concept_id": "concept:trial.phase",
        "label": "Clinical Trial Phase",
        "code_system": "internal"
      }
    ]
  }
}
```

## Governance model
1. Ontology Stewardship Board approves concept lifecycle changes.
2. Semantic versioning policy:
   - MAJOR for breaking concept changes
   - MINOR for new concepts/relationships
   - PATCH for metadata fixes
3. Backward compatibility windows enforced in Semantic MCP.
4. Audit trail for concept and mapping updates.

## Rollout plan
1. Phase 1 (done): seed semantic layer + inline semantic context + semantic MCP tools in current server.
2. Phase 2: move semantic tools into dedicated Semantic MCP service and connect API agent to both MCP endpoints.
3. Phase 3: persist ontology in Neo4j as governed graph model with versioned releases.
4. Phase 4: add semantic quality metrics in Grafana:
   - ambiguity resolution hit rate
   - concept mismatch rate
   - unresolved term rate
   - ontology version drift across services

## Key design decisions
1. Keep semantic context in tool responses rather than external lookups for every answer path.
2. Treat ontology resolution as a pre-query cognitive operation.
3. Keep security model unchanged (semantic layer complements authz, does not bypass it).
4. Use graph-native ontology storage for explainability and lineage.
