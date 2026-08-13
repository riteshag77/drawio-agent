"""Application bootstrap for configuration.

There is exactly one source of configuration: ``EnvConfigAdapter`` in
``app/adapters/outbound/config/env_config.py``. Read settings through
``get_config()`` at the point of use — every accessor there reads the
environment lazily, so a value is never stale.

This module only owns the two things that cannot be lazy: the one-time
``.env``/admin-settings load, and the static filesystem paths derived from
this file's own location.

Historically this module also re-exported snapshots (``HOST``, ``PORT``,
``LLM_TIMEOUT_SECONDS``, ...) captured at import time, with a
``_resolve_config()`` that rebound them after startup. That could not work:
rebinding a global here does not update a name another module already
imported, so late readers kept the pre-``.env`` value. Read ``get_config()``
instead.
"""

from __future__ import annotations

from pathlib import Path

from app.adapters.outbound.config.env_config import EnvConfigAdapter

_config_initialized: bool = False


def init_config() -> None:
    """Load .env, then layer admin-panel overrides on top. Idempotent."""
    global _config_initialized
    if _config_initialized:
        return
    EnvConfigAdapter()
    from app.adapters.outbound.admin_settings_store import apply_all_to_environ  # noqa: PLC0415

    apply_all_to_environ()
    _config_initialized = True


# ── Filesystem ──────────────────────────────────────────────────────────────
# Static: derived from this file's location, never from the environment.
BASE_DIR: Path = EnvConfigAdapter.base_dir()
STATIC_DIR: Path = EnvConfigAdapter.static_dir()
