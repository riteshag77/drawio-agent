"""Central application configuration — delegates to the EnvConfigAdapter.

All environment-driven settings are resolved here so the rest of the codebase
imports typed constants instead of reading ``os.getenv`` in scattered places.

12-factor principle: config is loaded from environment, never from module-level
side effects. Call ``init_config()`` explicitly at application startup.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.outbound.config.env_config import EnvConfigAdapter, get_config

_config_initialized: bool = False


def init_config() -> None:
    """Load .env file once at startup. Idempotent — safe to call multiple times."""
    global _config_initialized
    if _config_initialized:
        return
    EnvConfigAdapter()
    _config_initialized = True


# ── Filesystem ──────────────────────────────────────────────────────────────
BASE_DIR: Path = EnvConfigAdapter.base_dir()
STATIC_DIR: Path = EnvConfigAdapter.static_dir()

# ── Application metadata ────────────────────────────────────────────────────
APP_TITLE: str = "Draw.io AI Architect"

# ── Pipeline tuning (overridable via env) ───────────────────────────────────
def _cached_config():
    return get_config()

MAX_STRUCTURAL_RETRIES: int = _cached_config().max_structural_retries
MAX_VLM_RETRIES: int = _cached_config().max_vlm_retries
MAX_CUSTOM_MESSAGE_LEN: int = _cached_config().max_custom_message_len
BACKOFF_BASE_SECONDS: float = _cached_config().backoff_base_seconds
BACKOFF_MAX_SECONDS: float = _cached_config().backoff_max_seconds
LLM_TIMEOUT_SECONDS: int = _cached_config().llm_timeout_seconds

# ── Server ──────────────────────────────────────────────────────────────────
HOST: str = _cached_config().host
PORT: int = _cached_config().port
CORS_ORIGINS: list[str] = _cached_config().cors_origins


def _resolve_config() -> None:
    """Called once after init_config() to re-read env overrides."""
    global MAX_STRUCTURAL_RETRIES, MAX_VLM_RETRIES, HOST, PORT, CORS_ORIGINS
    global LLM_TIMEOUT_SECONDS, BACKOFF_BASE_SECONDS, BACKOFF_MAX_SECONDS
    cfg = get_config()
    MAX_STRUCTURAL_RETRIES = cfg.max_structural_retries
    MAX_VLM_RETRIES = cfg.max_vlm_retries
    HOST = cfg.host
    PORT = cfg.port
    LLM_TIMEOUT_SECONDS = cfg.llm_timeout_seconds
    BACKOFF_BASE_SECONDS = cfg.backoff_base_seconds
    BACKOFF_MAX_SECONDS = cfg.backoff_max_seconds
    CORS_ORIGINS = cfg.cors_origins
