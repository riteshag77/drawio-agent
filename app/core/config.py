"""Central application configuration and filesystem paths.

All environment-driven settings are resolved here so the rest of the codebase
imports typed constants instead of reading ``os.getenv`` in scattered places.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env as early as possible so every downstream import sees the values.
load_dotenv()

# ── Filesystem ──────────────────────────────────────────────────────────────
# config.py lives at <root>/app/core/config.py, so the project root is three up.
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
STATIC_DIR: Path = BASE_DIR / "static"

# ── Application metadata ────────────────────────────────────────────────────
APP_TITLE: str = "Draw.io AI Architect"

# ── Pipeline tuning ─────────────────────────────────────────────────────────
MAX_STRUCTURAL_RETRIES: int = int(os.getenv("MAX_VALIDATION_RETRIES", "1"))
MAX_VLM_RETRIES: int = int(os.getenv("MAX_VLM_RETRIES", "2"))
MAX_CUSTOM_MESSAGE_LEN: int = 5000

# ── Server ──────────────────────────────────────────────────────────────────
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
