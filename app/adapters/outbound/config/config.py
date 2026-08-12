"""A6: Unified configuration system — lazy per-request resolution.

Single source of truth for all config values. Replaces dual config timing
(import-time snapshots vs runtime get_config calls) with consistent lazy
per-request resolution.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


class Config:
    """Application configuration with lazy per-request resolution.
    
    All settings are resolved from environment variables, admin-store overrides,
    or defaults. No import-time snapshots; all values are fresh per access.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Initialize config and load .env file (once)."""
        if env_file is None:
            env_file = str(self.base_dir() / ".env")
        load_dotenv(env_file)
        self._admin_overrides: dict[str, Any] = {}
    
    # ──────────────────────────────────────────────────────────────────
    # Filesystem
    # ──────────────────────────────────────────────────────────────────
    
    @staticmethod
    def base_dir() -> Path:
        """Application root directory."""
        return Path(__file__).resolve().parent.parent.parent.parent
    
    @staticmethod
    def static_dir() -> Path:
        """Static assets directory."""
        return Config.base_dir() / "static"
    
    @staticmethod
    def config_dir() -> Path:
        """Config files directory."""
        return Config.base_dir() / "config"
    
    @staticmethod
    def data_dir() -> Path:
        """Data directory for SQLite, caches, etc."""
        return Config.base_dir() / "data"
    
    # ──────────────────────────────────────────────────────────────────
    # Private: resolve from env or admin overrides
    # ──────────────────────────────────────────────────────────────────
    
    def _get(self, key: str, default: Any = None) -> Any:
        """Get config value: admin override > environment > default."""
        if key in self._admin_overrides:
            return self._admin_overrides[key]
        return os.getenv(key, default)
    
    def _get_int(self, key: str, default: int = 0) -> int:
        """Get integer config value."""
        val = self._get(key)
        return int(val) if val is not None else default
    
    def _get_float(self, key: str, default: float = 0.0) -> float:
        """Get float config value."""
        val = self._get(key)
        return float(val) if val is not None else default
    
    def _get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean config value."""
        val = self._get(key)
        if val is None:
            return default
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    
    def _get_list(self, key: str, default: Optional[list[str]] = None, sep: str = ",") -> list[str]:
        """Get list config value (comma-separated by default)."""
        val = self._get(key)
        if val is None:
            return default or []
        return [item.strip() for item in str(val).split(sep) if item.strip()]
    
    def set_admin_override(self, key: str, value: Any) -> None:
        """Set an admin-store override (takes precedence over environment)."""
        self._admin_overrides[key] = value
    
    def clear_admin_overrides(self) -> None:
        """Clear all admin overrides (for testing)."""
        self._admin_overrides.clear()
    
    # ──────────────────────────────────────────────────────────────────
    # Application
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def app_title(self) -> str:
        """Application title for web UI."""
        return self._get("APP_TITLE", "Draw.io AI Architect")
    
    @property
    def host(self) -> str:
        """Server host."""
        return self._get("HOST", "0.0.0.0")
    
    @property
    def port(self) -> int:
        """Server port."""
        return self._get_int("PORT", 8000)
    
    @property
    def cors_origins(self) -> list[str]:
        """CORS allowed origins."""
        return self._get_list("CORS_ORIGINS", ["*"])
    
    @property
    def api_key(self) -> Optional[str]:
        """Optional API key gate (when set, require X-API-Key header)."""
        return self._get("API_KEY")
    
    # ──────────────────────────────────────────────────────────────────
    # Data Layer
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def database_path(self) -> Path:
        """Path to unified SQLite database."""
        db_file = self._get("DATABASE_PATH", "data/app.sqlite3")
        path = Path(db_file)
        if not path.is_absolute():
            path = self.base_dir() / path
        return path
    
    @property
    def enable_database_migrations(self) -> bool:
        """Auto-run Alembic migrations on startup."""
        return self._get_bool("ENABLE_DATABASE_MIGRATIONS", True)
    
    # ──────────────────────────────────────────────────────────────────
    # LLM & Provider
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def llm_timeout_seconds(self) -> int:
        """Timeout for individual LLM calls (seconds)."""
        return self._get_int("LLM_TIMEOUT_SECONDS", 300)
    
    @property
    def request_timeout_seconds(self) -> int:
        """Timeout for entire HTTP request (seconds)."""
        return self._get_int("REQUEST_TIMEOUT_SECONDS", 60)
    
    @property
    def max_concurrent_llm_calls_per_key(self) -> int:
        """Max concurrent LLM calls per API key."""
        return self._get_int("MAX_CONCURRENT_LLM_CALLS_PER_KEY", 3)
    
    @property
    def provider_fallback_order(self) -> list[str]:
        """Fallback order for LLM providers (comma-separated)."""
        return self._get_list("PROVIDER_FALLBACK_ORDER", ["anthropic", "openai"])
    
    # ──────────────────────────────────────────────────────────────────
    # Request Limits
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def max_request_body_bytes(self) -> int:
        """Max HTTP request body size (bytes)."""
        return self._get_int("MAX_REQUEST_BODY_BYTES", 100 * 1024 * 1024)  # 100 MB
    
    @property
    def max_custom_message_len(self) -> int:
        """Max length of custom system message."""
        return self._get_int("MAX_CUSTOM_MESSAGE_LEN", 8000)
    
    @property
    def rate_limit_requests_per_minute(self) -> int:
        """Rate limit: requests per minute (0 = disabled)."""
        return self._get_int("RATE_LIMIT_REQUESTS_PER_MINUTE", 0)
    
    # ──────────────────────────────────────────────────────────────────
    # Diagram Generation
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def diagram_ir_enabled(self) -> bool:
        """Use IR-based diagram generation (vs direct XML)."""
        return self._get_bool("DIAGRAM_IR_ENABLED", True)
    
    @property
    def diagram_ir_views(self) -> list[str]:
        """Which diagram views to generate IR for."""
        return self._get_list("DIAGRAM_IR_VIEWS", ["logical", "security", "data_flow"])
    
    @property
    def diagram_vlm_qa_enabled(self) -> bool:
        """Enable visual LLM QA validation of generated diagrams."""
        return self._get_bool("DIAGRAM_VLM_QA_ENABLED", False)
    
    @property
    def diagram_renderer(self) -> str:
        """Diagram renderer: 'auto', 'mcp', or 'local'."""
        return self._get("DIAGRAM_RENDERER", "auto")
    
    # ──────────────────────────────────────────────────────────────────
    # Draw.io MCP
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def drawio_mcp_url(self) -> Optional[str]:
        """Draw.io MCP server URL (for icon resolution)."""
        return self._get("DRAWIO_MCP_URL")
    
    @property
    def drawio_style_cache_reset(self) -> bool:
        """Force reset the draw.io style cache on startup."""
        return self._get_bool("DRAWIO_STYLE_CACHE_RESET", False)
    
    # ──────────────────────────────────────────────────────────────────
    # Features
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def enable_voice_commands(self) -> bool:
        """Enable voice command input."""
        return self._get_bool("ENABLE_VOICE_COMMANDS", True)
    
    @property
    def enable_attachment_analysis(self) -> bool:
        """Enable PDF/image attachment analysis."""
        return self._get_bool("ENABLE_ATTACHMENT_ANALYSIS", True)
    
    @property
    def enable_compliance_checks(self) -> bool:
        """Enable compliance framework assessments."""
        return self._get_bool("ENABLE_COMPLIANCE_CHECKS", True)
    
    @property
    def enable_cost_estimation(self) -> bool:
        """Enable cloud cost estimation."""
        return self._get_bool("ENABLE_COST_ESTIMATION", True)
    
    # ──────────────────────────────────────────────────────────────────
    # Observability
    # ──────────────────────────────────────────────────────────────────
    
    @property
    def enable_prometheus_metrics(self) -> bool:
        """Enable Prometheus metrics collection."""
        return self._get_bool("ENABLE_PROMETHEUS_METRICS", True)
    
    @property
    def trace_store_enabled(self) -> bool:
        """Store traces for debugging and analysis."""
        return self._get_bool("TRACE_STORE_ENABLED", True)
    
    @property
    def log_level(self) -> str:
        """Logging level: DEBUG, INFO, WARNING, ERROR."""
        return self._get("LOG_LEVEL", "INFO")


# ──────────────────────────────────────────────────────────────────────────────
# Global singleton
# ──────────────────────────────────────────────────────────────────────────────

_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the global configuration instance.
    
    Safe to call multiple times; config is initialized only once.
    """
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Reset the global config (useful for testing).
    
    WARNING: Only call this in test setup/teardown.
    """
    global _config
    _config = None
