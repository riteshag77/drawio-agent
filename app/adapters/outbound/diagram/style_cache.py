"""Durable MCP style cache (memory LRU + optional SQLite)."""

from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

from app.adapters.outbound.config.env_config import get_config

_STYLE_CACHE: OrderedDict[tuple[str, str], str] = OrderedDict()
_STYLE_CACHE_MAX = 512
_db_lock = threading.Lock()
_db_ready = False


def clear_style_cache() -> None:
    """Test helper — clears memory and truncates SQLite table if present."""
    _STYLE_CACHE.clear()
    path = _db_path()
    if path is None or not path.is_file():
        return
    with _db_lock:
        try:
            with sqlite3.connect(path) as conn:
                conn.execute("DELETE FROM mcp_styles")
                conn.commit()
        except sqlite3.Error:
            pass


def _db_path() -> Path | None:
    cfg = get_config()
    raw = (cfg.drawio_style_cache_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = get_config().base_dir() / path
    return path


def _ensure_db() -> Path | None:
    global _db_ready
    path = _db_path()
    if path is None:
        return None
    with _db_lock:
        if _db_ready and path.is_file():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_styles (
                    platform TEXT NOT NULL,
                    shape_type TEXT NOT NULL,
                    style TEXT NOT NULL,
                    PRIMARY KEY (platform, shape_type)
                )
                """
            )
            conn.commit()
        _db_ready = True
        return path


def cache_get(key: tuple[str, str]) -> str | None:
    if key in _STYLE_CACHE:
        _STYLE_CACHE.move_to_end(key)
        return _STYLE_CACHE[key]

    path = _ensure_db()
    if path is None:
        return None
    platform, shape_type = key
    with _db_lock:
        try:
            with sqlite3.connect(path) as conn:
                row = conn.execute(
                    "SELECT style FROM mcp_styles WHERE platform = ? AND shape_type = ?",
                    (platform, shape_type),
                ).fetchone()
        except sqlite3.Error:
            return None
    if not row:
        return None
    style = row[0]
    _mem_put(key, style)
    return style


def cache_put(key: tuple[str, str], value: str) -> None:
    _mem_put(key, value)
    path = _ensure_db()
    if path is None:
        return
    platform, shape_type = key
    with _db_lock:
        try:
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    INSERT INTO mcp_styles (platform, shape_type, style)
                    VALUES (?, ?, ?)
                    ON CONFLICT(platform, shape_type) DO UPDATE SET style = excluded.style
                    """,
                    (platform, shape_type, value),
                )
                conn.commit()
        except sqlite3.Error:
            pass


def _mem_put(key: tuple[str, str], value: str) -> None:
    if key in _STYLE_CACHE:
        _STYLE_CACHE.move_to_end(key)
    _STYLE_CACHE[key] = value
    while len(_STYLE_CACHE) > _STYLE_CACHE_MAX:
        _STYLE_CACHE.popitem(last=False)


def memory_empty() -> bool:
    return not _STYLE_CACHE
