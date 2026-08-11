"""Saved prompt templates — user-authored, reusable chat prompts.

Follows the same shape as ``history_store.py``: plain ``sqlite3``, a
module-level lock, lazy schema creation, and every operation degrading to a
no-op on error. No user/tenant scoping — this app has no auth system, so (like
``history_store``/``capture_store``) templates are a single global table.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adapters.outbound.config.env_config import get_config

_db_lock = threading.Lock()
_db_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL,
    pinned      INTEGER NOT NULL DEFAULT 0,
    run_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_templates_pinned ON templates (pinned DESC, updated_at DESC);
"""


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    description: str
    prompt: str
    pinned: bool
    run_count: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "prompt": self.prompt,
            "pinned": self.pinned,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _db_path() -> Path | None:
    raw = (get_config().templates_db_path or "").strip()
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
    if _db_ready and path.is_file():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.executescript(_SCHEMA)
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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_template(row: sqlite3.Row) -> Template:
    return Template(
        id=row["id"],
        title=row["title"] or "",
        description=row["description"] or "",
        prompt=row["prompt"],
        pinned=bool(row["pinned"]),
        run_count=int(row["run_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_template(*, title: str, description: str = "", prompt: str) -> Template | None:
    if not prompt.strip():
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = Template(
        id=uuid.uuid4().hex,
        title=title,
        description=description,
        prompt=prompt,
        pinned=False,
        run_count=0,
        created_at=now,
        updated_at=now,
    )
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO templates (id, title, description, prompt, pinned, run_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (record.id, record.title, record.description, record.prompt, record.created_at, record.updated_at),
                )
        except sqlite3.Error:
            return None
        finally:
            conn.close()
    return record


def list_templates(query: str = "", limit: int = 200) -> list[Template]:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return []
        try:
            if query.strip():
                like = f"%{query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT * FROM templates
                    WHERE title LIKE ? OR prompt LIKE ? OR description LIKE ?
                    ORDER BY pinned DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, max(1, limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM templates ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return [_row_to_template(r) for r in rows]


def update_template(
    template_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    prompt: str | None = None,
    pinned: bool | None = None,
) -> Template | None:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
                if row is None:
                    return None
                current = _row_to_template(row)
                updated = Template(
                    id=current.id,
                    title=current.title if title is None else title,
                    description=current.description if description is None else description,
                    prompt=current.prompt if prompt is None else prompt,
                    pinned=current.pinned if pinned is None else pinned,
                    run_count=current.run_count,
                    created_at=current.created_at,
                    updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
                conn.execute(
                    """
                    UPDATE templates SET title=?, description=?, prompt=?, pinned=?, updated_at=?
                    WHERE id=?
                    """,
                    (updated.title, updated.description, updated.prompt, int(updated.pinned), updated.updated_at, template_id),
                )
                return updated
        except sqlite3.Error:
            return None
        finally:
            conn.close()


def bump_run_count(template_id: str) -> Template | None:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                conn.execute("UPDATE templates SET run_count = run_count + 1 WHERE id = ?", (template_id,))
                row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
                return _row_to_template(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()


def delete_template(template_id: str) -> bool:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return False
        try:
            with conn:
                cur = conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
                return bool(cur.rowcount)
        except sqlite3.Error:
            return False
        finally:
            conn.close()



def reset_ready_flag() -> None:
    """Test helper — force schema re-creation after the configured path changes."""
    global _db_ready
    _db_ready = False
