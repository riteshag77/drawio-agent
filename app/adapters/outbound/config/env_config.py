"""Environment-based configuration provider.

Reads all application settings from environment variables / .env file.
No hardcoded defaults — every value comes from env or is set explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


class EnvConfigAdapter:
    """Configuration sourced from environment variables and .env file."""

    _initialized = False

    def __init__(self, env_file: str | None = None) -> None:
        if not EnvConfigAdapter._initialized:
            # Prefer project-root .env so keys load regardless of cwd
            path = env_file or str(EnvConfigAdapter.base_dir() / ".env")
            load_dotenv(path)
            EnvConfigAdapter._initialized = True

    # ── Base directory ─────────────────────────────────────────────────
    @staticmethod
    def base_dir() -> Path:
        return Path(__file__).resolve().parent.parent.parent.parent.parent

    @staticmethod
    def static_dir() -> Path:
        return EnvConfigAdapter.base_dir() / "static"

    # ── ConfigPort implementation ──────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return os.getenv(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(os.getenv(key, str(default)))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(os.getenv(key, str(default)))

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = os.getenv(key)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "on")

    def get_list(self, key: str, default: list[str] | None = None, sep: str = ",") -> list[str]:
        val = os.getenv(key)
        if val is None:
            return default or []
        return [item.strip() for item in val.split(sep) if item.strip()]

    # ── Convenience accessors ──────────────────────────────────────────

    @property
    def app_title(self) -> str:
        return self.get("APP_TITLE", "Draw.io AI Architect")

    @property
    def host(self) -> str:
        return self.get("HOST", "0.0.0.0")

    @property
    def port(self) -> int:
        return self.get_int("PORT", 8000)

    @property
    def cors_origins(self) -> list[str]:
        return self.get_list("CORS_ORIGINS", ["*"])

    @property
    def api_key(self) -> str | None:
        """When set, require matching X-API-Key (or Authorization: Bearer) on API routes."""
        key = (self.get("API_KEY") or "").strip()
        return key or None

    @property
    def max_request_body_bytes(self) -> int:
        return self.get_int("MAX_REQUEST_BODY_BYTES", 2_000_000)

    @property
    def max_custom_message_len(self) -> int:
        return self.get_int("MAX_CUSTOM_MESSAGE_LEN", 5000)

    @property
    def llm_timeout_seconds(self) -> int:
        return self.get_int("LLM_TIMEOUT", 120)

    @property
    def llm_max_retries(self) -> int:
        """Retry attempts for a transient provider error (429/502/503/504), beyond the first try.

        Defaults to 0 — a provider returning 503 ("model overloaded/unavailable")
        is telling you it's already busy; waiting a few seconds and re-hitting the
        same overloaded model rarely helps and just adds latency/cost on top of
        an already-failed call. Raise this only if your provider's rate limits
        (429) are known to reset quickly and a retry is actually likely to help.
        """
        return max(0, self.get_int("LLM_MAX_RETRIES", 0))

    @property
    def llm_retry_backoff_base(self) -> float:
        """Seconds before the first retry; doubles each subsequent attempt up to llm_retry_backoff_max."""
        return max(0.0, self.get_float("BACKOFF_BASE", 2.0))

    @property
    def llm_retry_backoff_max(self) -> float:
        return max(0.0, self.get_float("BACKOFF_MAX", 30.0))

    @property
    def request_timeout_seconds(self) -> float:
        """Overall wall-clock budget for a single /generate, /chat, or /chat/stream request."""
        return max(30.0, self.get_float("REQUEST_TIMEOUT_SECONDS", 300.0))

    @property
    def enrichment_branch_timeout_seconds(self) -> float:
        """Per-branch budget for each cost/WAF/compliance/MCP task in the enrichment gather."""
        return max(10.0, self.get_float("ENRICHMENT_BRANCH_TIMEOUT_SECONDS", 150.0))

    @property
    def max_concurrent_llm(self) -> int:
        """Max simultaneous blocking LLM calls across requests.

        Default 5 so multi-view fan-out (3 views + ADR + RAID) is not secretly
        serialized behind a too-small semaphore. Raise to 6–7 when view quality
        repair passes are common so repairs do not wait on ADR/RAID.
        """
        return self.get_int("MAX_CONCURRENT_LLM", 5)

    @property
    def view_quality_min_score(self) -> int:
        """Minimum conformance score (0–100) before a focused view is accepted."""
        return max(0, min(100, self.get_int("VIEW_QUALITY_MIN_SCORE", 75)))

    @property
    def view_repair_max_passes(self) -> int:
        """Max LLM repair passes per focused view (0 disables repair)."""
        return max(0, self.get_int("VIEW_REPAIR_MAX_PASSES", 1))

    @property
    def view_llm_provider(self) -> str:
        """Optional provider key override for Logical/Security/Data flow calls."""
        return (self.get("VIEW_LLM_PROVIDER", "") or "").strip()

    @property
    def ssl_verify(self) -> bool:
        return self.get_bool("SSL_VERIFY", True)

    @property
    def drawio_lib_aws(self) -> str:
        return self.get("DRAWIO_LIB_AWS", "mxgraph.aws4")

    @property
    def drawio_lib_gcp(self) -> str:
        return self.get("DRAWIO_LIB_GCP", "mxgraph.gcp2")

    @property
    def drawio_lib_azure(self) -> str:
        return self.get("DRAWIO_LIB_AZURE", "img/lib/azure2")

    @property
    def provider_default(self) -> str:
        return self.get("PROVIDER_DEFAULT", "gemini")

    @property
    def diagram_renderer(self) -> str:
        """local | mcp | auto — how Draw.io XML is enriched after LLM architecture."""
        value = (self.get("DIAGRAM_RENDERER", "auto") or "auto").strip().lower()
        return value if value in ("local", "mcp", "auto") else "auto"

    @property
    def drawio_mcp_url(self) -> str:
        return self.get("DRAWIO_MCP_URL", "https://mcp.draw.io/mcp")

    @property
    def drawio_mcp_timeout(self) -> int:
        return self.get_int("DRAWIO_MCP_TIMEOUT", 30)

    @property
    def drawio_mcp_concurrency(self) -> int:
        """Max parallel MCP search_shapes sessions for cache misses."""
        return self.get_int("DRAWIO_MCP_CONCURRENCY", 4)

    @property
    def drawio_mcp_libavoid(self) -> bool:
        """When true, call create_diagram(libavoid) after style resolve (extra MCP hop).

        Defaults to True so mcp/auto diagrams get obstacle-avoiding edges unless
        explicitly disabled via env or per-request ``libavoid=false``.
        """
        return self.get_bool("DRAWIO_MCP_LIBAVOID", True)

    @property
    def drawio_style_cache_path(self) -> str:
        """Relative or absolute SQLite path for durable MCP style cache. Empty disables."""
        return self.get("DRAWIO_STYLE_CACHE_PATH", "data/mcp_style_cache.sqlite3")

    @property
    def captures_dir(self) -> str:
        """Directory where full, untruncated /chat responses are persisted for later playback."""
        return self.get("CAPTURES_DIR", "data/captures")

    @property
    def drawio_style_cache_reset(self) -> bool:
        """When true, clear MCP style cache once at process startup."""
        return self.get_bool("DRAWIO_STYLE_CACHE_RESET", False)

    @property
    def xml_repair_max_attempts(self) -> int:
        """Deprecated: LLM XML repair was removed. Kept for env compatibility; unused."""
        return max(0, self.get_int("XML_REPAIR_MAX_ATTEMPTS", 0))

    @property
    def gap_analysis_enabled(self) -> bool:
        """When true, run the pre-generation architecture gap-analysis LLM pass.

        Enriches Logical/Security/Data-flow/Network context with defaulted,
        industry-standard components (security, resilience, observability,
        data classification) before the primary XML generation call.
        """
        return self.get_bool("GAP_ANALYSIS_ENABLED", True)

    @property
    def libavoid_focused_views(self) -> bool:
        """When true, route Logical/Security/Data-flow edges via libavoid (MCP) too.

        Previously these pages were excluded from libavoid and left entirely to
        the local obstacle-aware router. Defaults to True; set false to fall
        back to local-only routing for focused views.
        """
        return self.get_bool("LIBAVOID_FOCUSED_VIEWS", True)

    @property
    def log_format(self) -> str:
        """text | json — file/console log line format."""
        value = (self.get("LOG_FORMAT", "text") or "text").strip().lower()
        return value if value in ("text", "json") else "text"

    @property
    def log_dir(self) -> str:
        """Directory for rotating log files."""
        return self.get("DRAWIO_AGENT_LOG_DIR", "logs")

    @property
    def log_retention_days(self) -> int:
        """How many rotated daily log files to keep before the oldest is deleted."""
        return max(1, self.get_int("LOG_RETENTION_DAYS", 14))


# Singleton for app-wide use
_config_instance: EnvConfigAdapter | None = None


def get_config() -> EnvConfigAdapter:
    global _config_instance
    if _config_instance is None:
        _config_instance = EnvConfigAdapter()
    return _config_instance
