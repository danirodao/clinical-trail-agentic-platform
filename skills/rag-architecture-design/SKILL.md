---
name: rag-architecture-design
description: Design RAG (Retrieval-Augmented Generation) architectures — chunking strategies, embedding models, vector DB selection, hybrid search, reranking, and multimodal retrieval
triggers:
  - design a RAG system
  - RAG architecture
  - vector search design
  - chunking strategy
  - embedding model selection
  - hybrid search
  - reranking pipeline
  - multimodal RAG
  - retrieval pipeline
  - Qdrant
  - vector database
---

# RAG Architecture Design

You are an enterprise architect specialized in RAG (Retrieval-Augmented Generation) system design. Follow this framework.

## Step 1: Classify the RAG Pattern

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| **Naive RAG** | Chunk → Embed → Retrieve → Generate | Simple Q&A, prototypes |
| **Hybrid RAG** | Vector + Keyword + Metadata filters | Enterprise search, mixed query types |
| **Agentic RAG** | Agent decides retrieval strategy per query | Complex multi-step research |
| **Graph RAG** | Knowledge graph + vector hybrid | Ontology-heavy domains, relationship queries |
| **Multimodal RAG** | Text + Image + Table + Chart retrieval | Clinical reports, financial docs |
| **Self-Reflective RAG** | Retrieve → Generate → Verify → Correct | High-accuracy requirements, compliance |

## Step 2: Design the Chunking Strategy

```
CHUNKING DECISION TREE
───────────────────────
Document type?
├─ Structured (FHIR, HL7) → Field-level chunking
│   chunk = one clinical finding / observation
├─ Semi-structured (PDF reports) → Section-level chunking
│   chunk = one section (Assessment, Plan, Labs)
├─ Unstructured (notes, articles) → Semantic chunking
│   chunk = one coherent idea (200-500 tokens)
└─ Tabular (lab panels) → Row-group chunking
    chunk = one panel result with context

CHUNK METADATA (mandatory)
{
  "chunk_id": "uuid",
  "doc_id": "parent document",
  "chunk_type": "section|field|semantic|table",
  "section_header": "Assessment",
  "page_number": 3,
  "token_count": 350,
  "concept_ids": ["adverse_event", "lab_result"],
  "code_systems": ["MedDRA", "LOINC"],
  "access_level": "confidential",
  "trial_id": "T12345"
}
```

## Step 3: Select the Embedding Model

| Model | Dims | Max Tokens | Best For | Cost |
|-------|------|------------|----------|------|
| `text-embedding-3-small` | 512/1536 | 8191 | General, cost-sensitive | $0.02/1M |
| `text-embedding-3-large` | 256/1024/3072 | 8191 | High accuracy | $0.13/1M |
| `voyage-3-large` | 1024 | 32000 | Long documents | $0.14/1M |
| `stella_en_400M_v5` | 1024 | 8192 | On-prem, open source | Free |
| `NV-Embed-v2` | 4096 | 32768 | Highest MTEB score | Free |

**Selection criteria**: domain specificity, document length, latency budget, privacy (on-prem vs API).

## Step 4: Design the Retrieval Pipeline

```
┌─────────────────────────────────────────────────────┐
│              RETRIEVAL PIPELINE                      │
├─────────┬──────────┬──────────┬──────────┬──────────┤
│ Query   │ Query    │ Multi-   │ Reranker │ Result   │
│ →       │ Expansion │ Vector   │ →        │ Fusion    │
│         │ (synonyms│ Search    │ (cross-  │ (RRF/     │
│         │  concepts)│ (dense + │ encoder) │ weighted) │
│         │          │ sparse + │          │           │
│         │          │ metadata)│          │           │
└─────────┴──────────┴──────────┴──────────┴──────────┘

HYBRID SEARCH WEIGHTS (default)
- Dense (vector similarity): 0.5
- Sparse (BM25 keyword):    0.3
- Metadata filter boost:    0.2

RERANKING
- Cross-encoder model: ms-marco-MiniLM-L-6-v2 or Cohere Rerank v3
- Rerank top-K: 2x final K (retrieve 20, rerank to top 10)
```

## Step 5: Design Access-Aware Retrieval

```
ACCESS-AWARE RETRIEVAL PATTERN
──────────────────────────────
1. User query arrives with AccessContext
2. Query is enriched with authorized filters:
   - trial_id IN (authorized_trials)
   - access_level <= user.clearance_level
   - NOT concept_id IN (restricted_concepts)
3. Vector search runs WITH metadata pre-filter
4. Results are post-filtered for row-level security
5. Only authorized chunks reach the LLM
```

## Step 6: Output the RAG Blueprint

```
RAG ARCHITECTURE BLUEPRINT: [System Name]
═══════════════════════════════════════

PATTERN: [Naive / Hybrid / Agentic / Graph / Multimodal / Self-Reflective]

VECTOR DATABASE
- Engine: [Qdrant / Pinecone / Weaviate / Milvus / pgvector]
- Collection count: [N]
- Avg vectors/collection: [N]
- Dimension: [N]
- Distance metric: [Cosine / Dot / Euclidean]

CHUNKING STRATEGY
- Method: [Fixed-size / Semantic / Section-level / Field-level]
- Chunk size: [N] tokens
- Overlap: [N] tokens / [N]%
- Metadata schema: [fields]

EMBEDDING MODEL
- Model: [name]
- Dimension: [N]
- On-prem/API: [choice + rationale]
- Batch size: [N]
- Rate limit strategy: [approach]

RETRIEVAL PIPELINE
- Query expansion: [synonym / concept / none]
- Search type: [dense-only / hybrid / hybrid+rerank]
- Reranker: [model / none]
- Default top-K: [N]
- Max top-K: [N]

ACCESS CONTROL
- Pre-filter: [metadata filter injection]
- Post-filter: [row-level check]
- Restricted concepts: [list or reference]

PERFORMANCE TARGETS
- P50 latency: [N]ms
- P95 latency: [N]ms
- Recall@10: [target]
- MRR: [target]

TOKEN BUDGET
- Avg chunks per query: [N]
- Avg tokens per chunk: [N]
- Avg context tokens: [N]
- Optimization: [techniques]
```

## Rules

- Always include metadata in chunks — bare text chunks are an anti-pattern
- Hybrid search (dense + sparse) is mandatory for enterprise; pure vector is insufficient
- Access filters must be applied at query time, not post-retrieval
- Chunk overlap is essential for semantic chunking (20-30% overlap)
- Embedding model choice must consider privacy: on-prem required for PHI/PII
- Reranking is worth the latency cost for top-10 precision; skip for top-100 recall
- Always design for chunk versioning and re-embedding strategy
- If the user has existing documents, analyze their structure before recommending chunking