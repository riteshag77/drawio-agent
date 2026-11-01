"""Admin-panel settings overrides — a writable layer on top of .env.

Lets the password-gated admin panel change provider API keys/base URLs and
the default provider from a browser instead of hand-editing `.env`.
Persisted to a JSON file and re-applied to ``os.environ`` once at startup
(``apply_all_to_environ``, called from ``init_config()``) plus immediately
in-process on every save — so a change takes effect without a restart.
Every value this store manages (API keys, base URLs, ``PROVIDER_DEFAULT``)
is read lazily per-request elsewhere in the app (never at import time), so
there is no import-ordering hazard to worry about here.

Fail-open, like ``history_store.py``/``template_store.py``: a corrupt or
missing settings file just means no overrides are active, never a startup
failure.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app.adapters.outbound.config.env_config import get_config

_lock = threading.Lock()


def _path() -> Path:
    return get_config().base_dir() / "data" / "admin_settings.json"


def _load() -> dict[str, str]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, str]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def apply_all_to_environ() -> None:
    """Re-apply every persisted override to ``os.environ`` — call once at startup."""
    for key, value in _load().items():
        if value:
            os.environ[key] = value


def get_overrides() -> dict[str, str]:
    return _load()


def is_overridden(key: str) -> bool:
    return key in _load()


def set_setting(key: str, value: str) -> None:
    """Persist and immediately apply one override. Empty value clears it."""
    with _lock:
        data = _load()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        _save(data)
    if value:
        os.environ[key] = value
    else:
        os.environ.pop(key, None)


def clear_settings() -> None:
    """Test helper — wipe every stored override (does not touch os.environ)."""
    with _lock:
        _save({})
