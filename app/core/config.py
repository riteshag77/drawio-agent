"""Central application configuration and filesystem paths.

All environment-driven settings are resolved here so the rest of the codebase
imports typed constants instead of reading ``os.getenv`` in scattered places.

12-factor principle: config is loaded from environment, never from module-level
side effects. Call ``init_config()`` explicitly at application startup.
"""

from __future__ import annotations

import os
from pathlib import Path

_config_initialized: bool = False


def init_config() -> None:
    """Load .env file once at startup. Idempotent — safe to call multiple times."""
    global _config_initialized
    if _config_initialized:
        return
    from dotenv import load_dotenv
    load_dotenv()
    _config_initialized = True


# ── Filesystem ──────────────────────────────────────────────────────────────
# config.py lives at <root>/app/core/config.py, so the project root is three up.
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
STATIC_DIR: Path = BASE_DIR / "static"

# ── Application metadata ────────────────────────────────────────────────────
APP_TITLE: str = "Draw.io AI Architect"

# ── Pipeline tuning ─────────────────────────────────────────────────────────
MAX_STRUCTURAL_RETRIES: int = 1
MAX_VLM_RETRIES: int = 2
MAX_CUSTOM_MESSAGE_LEN: int = 5000
BACKOFF_BASE_SECONDS: float = 1.0
BACKOFF_MAX_SECONDS: float = 16.0
LLM_TIMEOUT_SECONDS: int = 120

# ── Server ──────────────────────────────────────────────────────────────────
HOST: str = "0.0.0.0"
PORT: int = 8000
CORS_ORIGINS: list[str] = ["*"]


def _resolve_config() -> None:
    """Called once after init_config() to override defaults from env."""
    global MAX_STRUCTURAL_RETRIES, MAX_VLM_RETRIES, HOST, PORT, CORS_ORIGINS
    global LLM_TIMEOUT_SECONDS, BACKOFF_BASE_SECONDS, BACKOFF_MAX_SECONDS
    MAX_STRUCTURAL_RETRIES = int(os.getenv("MAX_VALIDATION_RETRIES", str(MAX_STRUCTURAL_RETRIES)))
    MAX_VLM_RETRIES = int(os.getenv("MAX_VLM_RETRIES", str(MAX_VLM_RETRIES)))
    HOST = os.getenv("HOST", HOST)
    PORT = int(os.getenv("PORT", str(PORT)))
    LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT", str(LLM_TIMEOUT_SECONDS)))
    BACKOFF_BASE_SECONDS = float(os.getenv("BACKOFF_BASE", str(BACKOFF_BASE_SECONDS)))
    BACKOFF_MAX_SECONDS = float(os.getenv("BACKOFF_MAX", str(BACKOFF_MAX_SECONDS)))
    origins = os.getenv("CORS_ORIGINS")
    if origins:
        CORS_ORIGINS = [o.strip() for o in origins.split(",") if o.strip()]
