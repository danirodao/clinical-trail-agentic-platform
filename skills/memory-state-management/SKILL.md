---
name: memory-state-management
description: Design memory and state management for AI agents — working/session/persistent tiers, vector memory, knowledge graph persistence, pruning, and consolidation strategies
triggers:
  - design agent memory
  - memory architecture
  - agent state management
  - vector memory
  - session memory
  - persistent memory
  - memory pruning
  - memory consolidation
  - knowledge graph memory
  - agent context management
---

# Memory & State Management for AI Agents

You are an enterprise architect specialized in memory architecture for AI agents. Follow this framework.

## Step 1: Define the Memory Tiers

```
┌──────────────────────────────────────────────┐
│           MEMORY ARCHITECTURE                  │
├──────────────────────────────────────────────┤
│ TIER 1: WORKING MEMORY (Ephemeral)            │
│ ───────────────────────────────────────────── │
│ • Scope: Single task / conversation turn      │
│ • Lifetime: Duration of one LLM call          │
│ • Content: Current query, tool results,       │
│   intermediate reasoning                      │
│ • Storage: LLM context window                 │
│ • Size limit: Model context window minus      │
│   system prompt and tool definitions          │
│ • Eviction: Automatic (context window limit)  │
├──────────────────────────────────────────────┤
│ TIER 2: SESSION MEMORY (Short-term)           │
│ ───────────────────────────────────────────── │
│ • Scope: Single user session / conversation   │
│ • Lifetime: Session duration (minutes-hours)  │
│ • Content: Conversation history, decisions,   │
│   user preferences expressed this session     │
│ • Storage: Redis / In-memory dict / DB        │
│ • Size limit: Last N turns or token cap       │
│ • Eviction: Session end or LRU when full      │
├──────────────────────────────────────────────┤
│ TIER 3: PERSISTENT MEMORY (Long-term)         │
│ ───────────────────────────────────────────── │
│ • Scope: Cross-session, cross-user            │
│ • Lifetime: Indefinite (until pruned)         │
│ • Content: User preferences, learned facts,   │
│   domain knowledge, successful patterns       │
│ • Storage: Vector DB + Knowledge Graph        │
│ • Size limit: Managed by pruning policies     │
│ • Eviction: Age-based, relevance-based,       │
│   or explicit user deletion                   │
└──────────────────────────────────────────────┘
```

## Step 2: Design the Memory Flow

```
MEMORY FLOW PER TASK
────────────────────

USER QUERY
    │
    ▼
┌─────────────────────────────┐
│ 1. RETRIEVE RELEVANT MEMORY │
│    ├─ Session: last N turns │
│    ├─ Vector: top-K similar │
│    └─ Graph: related facts  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ 2. ASSEMBLE CONTEXT         │
│    System Prompt            │
│    + Tool Definitions       │
│    + Session Memory (N turns)│
│    + Retrieved Memories (K) │
│    + User Query             │
│    = WORKING MEMORY         │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ 3. EXECUTE (LLM + Tools)    │
│    Agent reasons and acts   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ 4. CONSOLIDATE              │
│    ├─ Save turn to session  │
│    ├─ Extract facts → graph │
│    ├─ Embed interaction →   │
│    │  vector store          │
│    └─ Update preferences    │
└─────────────────────────────┘
```

## Step 3: Design Vector-Based Semantic Memory

```python
# VECTOR MEMORY SCHEMA
MEMORY_ENTRY = {
    "memory_id": "uuid",
    "user_id": "string",
    "session_id": "string",
    "type": "fact | preference | pattern | decision | feedback",
    "content": "string (the actual memory)",
    "embedding": [0.123, -0.456, ...],  # 1536-d or similar
    "metadata": {
        "timestamp": "ISO datetime",
        "source_task": "task_type",
        "confidence": 0.0-1.0,
        "access_count": 0,
        "last_accessed": "ISO datetime",
        "tags": ["tag1", "tag2"]
    }
}

# RETRIEVAL PATTERN
def retrieve_memories(query: str, user_id: str, top_k: int = 5):
    query_embedding = embed(query)
    
    # Hybrid retrieval
    vector_results = vector_db.search(
        embedding=query_embedding,
        filter={"user_id": user_id},
        top_k=top_k
    )
    
    # Boost by recency and access frequency
    for r in vector_results:
        r.score *= recency_boost(r.last_accessed)
        r.score *= frequency_boost(r.access_count)
    
    return sorted(vector_results, key=lambda r: r.score, reverse=True)
```

## Step 4: Design Knowledge Graph Persistence

```
KNOWLEDGE GRAPH MEMORY MODEL
────────────────────────────

NODE TYPES
├─ User: {user_id, preferences, role}
├─ Entity: {name, type, properties}
├─ Fact: {statement, confidence, source}
├─ Decision: {context, choice, outcome}
├─ Pattern: {trigger, action, success_rate}
└─ Session: {session_id, start_time, summary}

RELATIONSHIP TYPES
├─ (User)-[PREFERS]->(Entity)        // user preferences
├─ (User)-[KNOWS]->(Fact)            // learned facts
├─ (User)-[MADE]->(Decision)         // past decisions
├─ (Decision)-[LED_TO]->(Outcome)    // decision outcomes
├─ (Pattern)-[SUCCEEDED_IN]->(Task)  // successful patterns
├─ (Entity)-[RELATED_TO]->(Entity)   // domain relationships
└─ (Fact)-[DERIVED_FROM]->(Source)   // provenance

CONSOLIDATION QUERY (Cypher)
MATCH (u:User {id: $user_id})
MATCH (u)-[:KNOWS]->(f:Fact)
WHERE f.confidence > 0.7
  AND f.last_accessed < datetime() - duration('P30D')
SET f.confidence = f.confidence * 0.9
// Facts not accessed in 30 days lose 10% confidence
```

## Step 5: Design Memory Pruning & Consolidation

```
PRUNING POLICIES
────────────────

AGE-BASED PRUNING
├─ Session memory: Delete after session end + 24h
├─ Vector memories: Archive if not accessed in 90 days
├─ Graph facts: Deprecate if confidence < 0.3
└─ Patterns: Remove if success_rate < 0.5 after 10 uses

RELEVANCE-BASED PRUNING
├─ Compute relevance score = recency × frequency × confidence
├─ Prune bottom 10% when storage exceeds threshold
└─ Never prune memories marked as "pinned" by user

CONSOLIDATION STRATEGIES
────────────────────────

DAILY CONSOLIDATION
├─ Merge duplicate facts (same subject-predicate-object)
├─ Update confidence based on corroboration
├─ Summarize session clusters into session summaries
└─ Decay unused memories (confidence *= 0.95)

WEEKLY DEEP CONSOLIDATION
├─ Identify patterns from repeated decision sequences
├─ Generalize entity-specific facts to category-level
├─ Resolve contradictions (flag for human review)
└─ Rebuild embeddings for updated memories
```

## Step 6: Output the Memory Blueprint

```
MEMORY BLUEPRINT: [Agent System Name]
═══════════════════════════════════════

TIER 1: WORKING MEMORY
- Context window budget: [N] tokens
- Allocation:
  • System prompt: [N] tokens ([%])
  • Tool definitions: [N] tokens ([%])
  • Session memory: [N] tokens ([%])
  • Retrieved memories: [N] tokens ([%])
  • User query + response: remaining

TIER 2: SESSION MEMORY
- Storage: [Redis / In-memory / Postgres]
- Max turns stored: [N]
- Summarization: [none / rolling / on-session-end]
- Session TTL: [N] hours

TIER 3: PERSISTENT MEMORY
- Vector store: [Qdrant / Pinecone / Weaviate / Milvus]
- Embedding model: [text-embedding-3-small / ada-002 / custom]
- Embedding dimensions: [N]
- Knowledge graph: [Neo4j / Neptune / ArangoDB]
- Max memories per user: [N]
- Pruning cadence: [daily / weekly / on-threshold]

CONSOLIDATION
- Daily consolidation: [enabled / disabled]
- Weekly deep consolidation: [enabled / disabled]
- Contradiction resolution: [automatic / human-review / disabled]

MEMORY BUDGET (per task)
┌────────────────────┬──────────┬──────────┐
│ Component          │ Tokens   │ % Budget │
├────────────────────┼──────────┼──────────┤
│ System Prompt      │ [N]      │ [%]      │
│ Tool Definitions   │ [N]      │ [%]      │
│ Session History    │ [N]      │ [%]      │
│ Retrieved Memories │ [N]      │ [%]      │
│ User + Response    │ [N]      │ [%]      │
├────────────────────┼──────────┼──────────┤
│ TOTAL              │ [N]      │ 100%     │
└────────────────────┴──────────┴──────────┘
```

## Rules

- Working memory is precious — every token in context must earn its place
- Session memory must be summarized before insertion, not raw conversation dumps
- Vector memory retrieval must be hybrid: semantic similarity + recency + frequency
- Knowledge graph is for structured facts; vector store is for unstructured memories — use both
- Pruning is mandatory — unbounded memory growth degrades retrieval quality and increases cost
- Consolidation must merge duplicates and decay stale memories — memory is not append-only
- Never store PII/PHI in persistent memory without explicit user consent and encryption
- Memory budget must be defined per task type — different tasks need different memory profiles