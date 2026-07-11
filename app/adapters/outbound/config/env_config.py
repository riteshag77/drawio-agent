"""Environment-based configuration provider.

Reads all application settings from environment variables / .env file.
No hardcoded defaults — every value comes from env or is set explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.domain.ports.config_port import ConfigPort


class EnvConfigAdapter(ConfigPort):
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

    @staticmethod
    def patterns_data_dir() -> Path:
        return EnvConfigAdapter.base_dir() / "app" / "adapters" / "outbound" / "patterns" / "data"

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
    def max_structural_retries(self) -> int:
        return self.get_int("MAX_STRUCTURAL_RETRIES", 1)

    @property
    def max_vlm_retries(self) -> int:
        return self.get_int("MAX_VLM_RETRIES", 2)

    @property
    def max_custom_message_len(self) -> int:
        return self.get_int("MAX_CUSTOM_MESSAGE_LEN", 5000)

    @property
    def backoff_base_seconds(self) -> float:
        return self.get_float("BACKOFF_BASE", 1.0)

    @property
    def backoff_max_seconds(self) -> float:
        return self.get_float("BACKOFF_MAX", 16.0)

    @property
    def llm_timeout_seconds(self) -> int:
        return self.get_int("LLM_TIMEOUT", 120)

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
    def leaf_node_width(self) -> int:
        return self.get_int("LEAF_NODE_W", 60)

    @property
    def leaf_node_height(self) -> int:
        return self.get_int("LEAF_NODE_H", 60)

    @property
    def leaf_cell_width(self) -> int:
        return self.get_int("LEAF_CELL_W", 168)

    @property
    def leaf_gap(self) -> int:
        return self.get_int("LEAF_GAP", 32)

    @property
    def container_pad(self) -> int:
        return self.get_int("CONTAINER_PAD", 28)

    @property
    def container_pad_top(self) -> int:
        return self.get_int("CONTAINER_PAD_TOP", 48)

    @property
    def container_gap(self) -> int:
        return self.get_int("CONTAINER_GAP", 48)

    @property
    def root_top(self) -> int:
        return self.get_int("ROOT_TOP", 40)

    @property
    def max_cols(self) -> int:
        return self.get_int("MAX_COLS", 4)

    @property
    def default_container_w(self) -> int:
        return self.get_int("DEFAULT_CONTAINER_W", 320)

    @property
    def default_container_h(self) -> int:
        return self.get_int("DEFAULT_CONTAINER_H", 240)

    @property
    def tier_column_w(self) -> int:
        return self.get_int("TIER_COLUMN_W", 300)

    @property
    def az_column_w(self) -> int:
        return self.get_int("AZ_COLUMN_W", 320)

    @property
    def default_page_w(self) -> int:
        return self.get_int("DEFAULT_PAGE_W", 1169)

    @property
    def default_page_h(self) -> int:
        return self.get_int("DEFAULT_PAGE_H", 827)

    @property
    def face_slots(self) -> tuple[float, ...]:
        raw = self.get_list("FACE_SLOTS", ["0.5", "0.35", "0.65", "0.25", "0.75"])
        return tuple(float(v) for v in raw)

    @property
    def edge_color_aws(self) -> str:
        return self.get("EDGE_COLOR_AWS", "#ED7100")

    @property
    def edge_color_gcp(self) -> str:
        return self.get("EDGE_COLOR_GCP", "#4285F4")

    @property
    def edge_color_azure(self) -> str:
        return self.get("EDGE_COLOR_AZURE", "#0078D4")

    @property
    def llm_default_model(self) -> str:
        return self.get("LLM_DEFAULT_MODEL", "gpt-4o")

    @property
    def provider_default(self) -> str:
        return self.get("PROVIDER_DEFAULT", "openai")

    @property
    def validation_default_model(self) -> str:
        return self.get("VALIDATION_MODEL", "gemini-2.5-flash")

    @property
    def enable_vlm_validation(self) -> bool:
        return self.get_bool("ENABLE_VLM_VALIDATION", True)

    @property
    def min_real_diagram_length(self) -> int:
        return self.get_int("MIN_REAL_DIAGRAM_LENGTH", 300)

    @property
    def max_chat_turns_full_history(self) -> int:
        return self.get_int("MAX_CHAT_TURNS_FULL_HISTORY", 4)

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


# Singleton for app-wide use
_config_instance: EnvConfigAdapter | None = None


def get_config() -> EnvConfigAdapter:
    global _config_instance
    if _config_instance is None:
        _config_instance = EnvConfigAdapter()
    return _config_instance
