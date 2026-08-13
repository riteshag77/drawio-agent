"""Durable diagram history — every successful generation, versioned per session.

Follows the same shape as ``diagram/style_cache.py``: plain ``sqlite3``, a
module-level lock, lazy schema creation, and every operation degrading to a
no-op on error. History is a convenience, never a reason for a diagram request
to fail, so nothing in here raises into the request path.

This is deliberately *not* an ORM. The schema is two tables and the queries are
five statements; SQLAlchemy/SQLModel would add a dependency and a session
lifecycle to manage for no reduction in the code that actually matters.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adapters.outbound import sqlite_util
from app.adapters.outbound.config.env_config import get_config

_db_lock = threading.Lock()
_db_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS diagram_versions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    version         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    prompt          TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    generation_path TEXT NOT NULL DEFAULT '',
    renderer_used   TEXT NOT NULL DEFAULT '',
    xml             TEXT NOT NULL,
    ir_json         TEXT,
    meta_json       TEXT,
    messages_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_session
    ON diagram_versions (session_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_history_created
    ON diagram_versions (created_at DESC);
"""


@dataclass(frozen=True)
class DiagramVersion:
    id: str
    session_id: str
    version: int
    created_at: str
    title: str
    prompt: str
    platform: str
    provider: str
    generation_path: str
    renderer_used: str
    xml: str
    ir: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] | None = None

    def summary(self) -> dict[str, Any]:
        """Metadata only — the XML payload is often large, so listings omit it."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "version": self.version,
            "created_at": self.created_at,
            "title": self.title,
            "prompt": self.prompt,
            "platform": self.platform,
            "provider": self.provider,
            "generation_path": self.generation_path,
            "renderer_used": self.renderer_used,
            "xml_chars": len(self.xml),
            "diagram_type": self.meta.get("diagram_type", "cloud"),
        }

    def full(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "xml": self.xml,
            "ir": self.ir,
            "meta": self.meta,
            "messages": self.messages or [],
        }


def _db_path() -> Path | None:
    raw = (get_config().history_db_path or "").strip()
    if not raw or not get_config().history_enabled:
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
    if _db_ready and path.is_file():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # NB: `with sqlite3.connect(...)` is a *transaction* context manager, not
        # a closing one — it commits but leaves the connection (and the file
        # handle) open. Close explicitly or the db file stays locked.
        conn = sqlite_util.connect(path)
        try:
            conn.executescript(_SCHEMA)
            # Best-effort migration for DBs created before messages_json existed —
            # CREATE TABLE IF NOT EXISTS above is a no-op against an existing file.
            try:
                conn.execute("ALTER TABLE diagram_versions ADD COLUMN messages_json TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None
    _db_ready = True
    return path


def _connect() -> sqlite3.Connection | None:
    path = _ensure_db()
    if path is None:
        return None
    conn = sqlite_util.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def save_version(
    *,
    session_id: str,
    xml: str,
    prompt: str = "",
    title: str = "",
    platform: str = "",
    provider: str = "",
    generation_path: str = "",
    renderer_used: str = "",
    ir: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> DiagramVersion | None:
    """Append a new version for ``session_id``. Returns None when disabled/unavailable."""
    if not session_id or not xml:
        return None

    record = DiagramVersion(
        id=uuid.uuid4().hex,
        session_id=session_id,
        version=0,  # replaced below, inside the lock
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        title=title,
        prompt=prompt[:2000],
        platform=platform,
        provider=provider,
        generation_path=generation_path,
        renderer_used=renderer_used,
        xml=xml,
        ir=ir,
        meta=meta or {},
        messages=messages,
    )

    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) AS v FROM diagram_versions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                next_version = int(row["v"]) + 1
                conn.execute(
                    """
                    INSERT INTO diagram_versions (
                        id, session_id, version, created_at, title, prompt, platform,
                        provider, generation_path, renderer_used, xml, ir_json, meta_json,
                        messages_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id, session_id, next_version, record.created_at, record.title,
                        record.prompt, record.platform, record.provider, record.generation_path,
                        record.renderer_used, record.xml,
                        json.dumps(record.ir) if record.ir is not None else None,
                        json.dumps(record.meta),
                        json.dumps(record.messages) if record.messages is not None else None,
                    ),
                )
                _prune_locked(conn, session_id)
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    return DiagramVersion(**{**record.__dict__, "version": next_version})


def _prune_locked(conn: sqlite3.Connection, session_id: str) -> None:
    """Drop the oldest versions beyond the retention cap. Caller holds the lock."""
    limit = get_config().history_max_versions_per_session
    conn.execute(
        """
        DELETE FROM diagram_versions
        WHERE session_id = ?
          AND id NOT IN (
              SELECT id FROM diagram_versions
              WHERE session_id = ?
              ORDER BY version DESC
              LIMIT ?
          )
        """,
        (session_id, session_id, limit),
    )


def _row_to_version(row: sqlite3.Row) -> DiagramVersion:
    return DiagramVersion(
        id=row["id"],
        session_id=row["session_id"],
        version=int(row["version"]),
        created_at=row["created_at"],
        title=row["title"] or "",
        prompt=row["prompt"] or "",
        platform=row["platform"] or "",
        provider=row["provider"] or "",
        generation_path=row["generation_path"] or "",
        renderer_used=row["renderer_used"] or "",
        xml=row["xml"],
        ir=json.loads(row["ir_json"]) if row["ir_json"] else None,
        meta=json.loads(row["meta_json"]) if row["meta_json"] else {},
        messages=json.loads(row["messages_json"]) if row["messages_json"] else None,
    )


def list_versions(session_id: str, limit: int = 50) -> list[DiagramVersion]:
    """Newest-first versions for one session."""
    with _db_lock:
        conn = _connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM diagram_versions WHERE session_id = ? ORDER BY version DESC LIMIT ?",
                (session_id, max(1, limit)),
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return [_row_to_version(r) for r in rows]


def get_version(version_id: str) -> DiagramVersion | None:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM diagram_versions WHERE id = ?", (version_id,)
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
    return _row_to_version(row) if row else None


def get_latest(session_id: str) -> DiagramVersion | None:
    versions = list_versions(session_id, limit=1)
    return versions[0] if versions else None


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """One row per session, newest activity first."""
    with _db_lock:
        conn = _connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """
                SELECT session_id,
                       COUNT(*)         AS versions,
                       MAX(created_at)  AS last_updated,
                       MAX(version)     AS latest_version
                FROM diagram_versions
                GROUP BY session_id
                ORDER BY last_updated DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            titles = {
                r["session_id"]: (r["title"] or r["prompt"] or "")
                for r in conn.execute(
                    """
                    SELECT session_id, title, prompt FROM diagram_versions
                    WHERE version = 1
                    """
                ).fetchall()
            }
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return [
        {
            "session_id": r["session_id"],
            "versions": int(r["versions"]),
            "latest_version": int(r["latest_version"]),
            "last_updated": r["last_updated"],
            "title": (titles.get(r["session_id"]) or "")[:120],
        }
        for r in rows
    ]


def delete_session(session_id: str) -> int:
    """Remove every version for a session. Returns rows deleted."""
    with _db_lock:
        conn = _connect()
        if conn is None:
            return 0
        try:
            with conn:
                cur = conn.execute("DELETE FROM diagram_versions WHERE session_id = ?", (session_id,))
                return cur.rowcount or 0
        except sqlite3.Error:
            return 0
        finally:
            conn.close()



def reset_ready_flag() -> None:
    """Test helper — force schema re-creation after the configured path changes."""
    global _db_ready
    _db_ready = False
