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
    def max_xml_chars(self) -> int:
        return self.get_int("MAX_XML_CHARS", 500_000)

    @property
    def max_message_chars(self) -> int:
        return self.get_int("MAX_MESSAGE_CHARS", 20_000)

    @property
    def max_custom_message_len(self) -> int:
        return self.get_int("MAX_CUSTOM_MESSAGE_LEN", 5000)

    @property
    def llm_timeout_seconds(self) -> int:
        return self.get_int("LLM_TIMEOUT", 120)

    @property
    def max_concurrent_llm(self) -> int:
        """Max simultaneous blocking LLM calls across requests."""
        return self.get_int("MAX_CONCURRENT_LLM", 4)

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
    def drawio_origin(self) -> str:
        return self.get("DRAWIO_ORIGIN", "https://embed.diagrams.net")

    @property
    def drawio_themes(self) -> list[str]:
        return self.get_list("DRAWIO_THEMES", ["kennedy", "atlas", "dark", "min", "sketch", "simple"])

    @property
    def llm_default_model(self) -> str:
        return self.get("LLM_DEFAULT_MODEL", "gpt-4o")

    @property
    def provider_default(self) -> str:
        return self.get("PROVIDER_DEFAULT", "openai")

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
        """When true, call create_diagram(libavoid) after style resolve (extra MCP hop)."""
        return self.get_bool("DRAWIO_MCP_LIBAVOID", False)

    @property
    def drawio_style_cache_path(self) -> str:
        """Relative or absolute SQLite path for durable MCP style cache. Empty disables."""
        return self.get("DRAWIO_STYLE_CACHE_PATH", "data/mcp_style_cache.sqlite3")

    @property
    def drawio_style_cache_reset(self) -> bool:
        """When true, clear MCP style cache once at process startup."""
        return self.get_bool("DRAWIO_STYLE_CACHE_RESET", False)


# Singleton for app-wide use
_config_instance: EnvConfigAdapter | None = None


def get_config() -> EnvConfigAdapter:
    global _config_instance
    if _config_instance is None:
        _config_instance = EnvConfigAdapter()
    return _config_instance
