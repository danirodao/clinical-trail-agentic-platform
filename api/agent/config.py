import os
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    # ── Data MCP Server ───────────────────────────────────────────────────────
    mcp_server_url: str = field(
        default_factory=lambda: os.getenv(
            "MCP_SERVER_URL", "http://mcp-server:8001/sse"
        )
    )
    mcp_bearer_token: str = field(
        default_factory=lambda: os.getenv("MCP_BEARER_TOKEN", "")
    )

    # ── Semantic MCP Server ───────────────────────────────────────────────────
    semantic_mcp_server_url: str = field(
        default_factory=lambda: os.getenv(
            "SEMANTIC_MCP_SERVER_URL", "http://semantic-mcp-server:8002/sse"
        )
    )
    semantic_mcp_enabled: bool = field(
        default_factory=lambda: os.getenv("SEMANTIC_MCP_ENABLED", "true").lower() == "true"
    )

    # ── LLM ───────────────────────────────────────────────────────────────────
    simple_model: str = "gpt-4o-mini"          # Single-tool, straightforward queries
    complex_model: str = "gpt-4o"              # Multi-tool, cross-trial, comparative
    temperature: float = 0.0                   # Deterministic for clinical data
    max_tokens: int = 2048

    # ── ReAct Loop Guards ─────────────────────────────────────────────────────
    max_iterations: int = 15                   # Hard stop on tool-call loops
    simple_query_max_iterations: int = 15      # Match complex limit to prevent premature cutoff
    max_history_turns: int = 5                 # Multi-turn conversation sliding window
    max_tool_output_chars: int = 1000          # Limit for older tool results in history

    # ── Complexity Classification ─────────────────────────────────────────────
    # Queries containing these keywords are routed to GPT-4o
    complex_keywords: tuple = (
        "compare", "versus", "vs", "across", "between",
        "correlation", "trend", "over time", "relationship",
        "why", "mechanism", "predict", "all trials",
        "each trial", "both trials",
    )
    simple_token_threshold: int = 25           # Queries under this word count → simple

    # ── Observability ─────────────────────────────────────────────────────────
    phoenix_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "PHOENIX_ENDPOINT", "http://phoenix:6006/v1/traces"
        )
    )
    phoenix_project: str = "clinical-trial-agent"
    enable_tracing: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TRACING", "true").lower() == "true"
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    log_tool_results: bool = field(
        default_factory=lambda: os.getenv("LOG_TOOL_RESULTS", "false").lower() == "true"
    )

    # ── Rate Limiting (enforced at API layer, referenced here for docs) ────────
    per_user_rpm: int = 20
    per_org_rpm: int = 100

    # ── Evaluation Flywheel ───────────────────────────────────────────────────
    prod_sampling_rate: float = field(
        default_factory=lambda: float(os.getenv("PROD_SAMPLING_RATE", "0.10"))
    )


# Singleton — imported by all agent modules
agent_config = AgentConfig()