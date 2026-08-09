"""Org governance/house-style rules — admin-authored, checked deterministically
against every generated IR.

Follows the same shape as ``history_store.py``/``template_store.py``: plain
``sqlite3``, a module-level lock, lazy schema creation, and every operation
degrading to a no-op on error. No user/tenant scoping — this app has no auth
system, so (like every other store here) rules are a single global table; see
``app/domain/entities/governance.py``'s module docstring for why that's an
explicit, documented v1 limitation rather than an oversight.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.adapters.outbound.config.env_config import get_config
from app.domain.entities.governance import GovernanceRule

_db_lock = threading.Lock()
_db_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS governance_rules (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    view            TEXT NOT NULL DEFAULT 'any',
    platform        TEXT NOT NULL DEFAULT 'any',
    enabled         INTEGER NOT NULL DEFAULT 1,
    message         TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_governance_view ON governance_rules (view, enabled);
"""

#: Fields carried in params_json — everything on GovernanceRule except the
#: identity/scope/message columns already broken out above.
_PARAM_FIELDS = (
    "corridor_id", "shape_type", "overview_field", "min_count",
    "min_protection", "boundary_only", "label_pattern",
)


def _db_path() -> Path | None:
    raw = (get_config().governance_rules_db_path or "").strip()
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


def _row_to_rule(row: sqlite3.Row) -> GovernanceRule:
    params = {}
    try:
        params = json.loads(row["params_json"]) or {}
    except (ValueError, TypeError):
        pass
    return GovernanceRule(
        id=row["id"],
        kind=row["kind"],
        view=row["view"] or "any",
        platform=row["platform"] or "any",
        enabled=bool(row["enabled"]),
        message=row["message"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        **{k: v for k, v in params.items() if k in _PARAM_FIELDS},
    )


def create_rule(rule: GovernanceRule) -> GovernanceRule | None:
    if not rule.message.strip():
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = rule.model_copy(update={
        "id": uuid.uuid4().hex, "created_at": now, "updated_at": now,
    })
    params_json = json.dumps({k: getattr(record, k) for k in _PARAM_FIELDS})
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO governance_rules
                        (id, kind, view, platform, enabled, message, params_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id, record.kind, record.view, record.platform,
                        int(record.enabled), record.message, params_json,
                        record.created_at, record.updated_at,
                    ),
                )
        except sqlite3.Error:
            return None
        finally:
            conn.close()
    return record


def list_rules(view: str | None = None, *, enabled_only: bool = False) -> list[GovernanceRule]:
    """All rules, or those scoped to ``view`` (which also includes 'any'-scoped
    rules — the caller decides applicability, this just filters the query)."""
    with _db_lock:
        conn = _connect()
        if conn is None:
            return []
        try:
            if view:
                rows = conn.execute(
                    "SELECT * FROM governance_rules WHERE view IN (?, 'any') ORDER BY created_at",
                    (view,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM governance_rules ORDER BY created_at").fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    rules = [_row_to_rule(r) for r in rows]
    return [r for r in rules if r.enabled] if enabled_only else rules


def update_rule(rule_id: str, **updates: object) -> GovernanceRule | None:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            with conn:
                row = conn.execute("SELECT * FROM governance_rules WHERE id = ?", (rule_id,)).fetchone()
                if row is None:
                    return None
                current = _row_to_rule(row)
                updated = current.model_copy(update={
                    **{k: v for k, v in updates.items() if v is not None and hasattr(current, k)},
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                params_json = json.dumps({k: getattr(updated, k) for k in _PARAM_FIELDS})
                conn.execute(
                    """
                    UPDATE governance_rules
                    SET kind=?, view=?, platform=?, enabled=?, message=?, params_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        updated.kind, updated.view, updated.platform, int(updated.enabled),
                        updated.message, params_json, updated.updated_at, rule_id,
                    ),
                )
                return updated
        except sqlite3.Error:
            return None
        finally:
            conn.close()


def delete_rule(rule_id: str) -> bool:
    with _db_lock:
        conn = _connect()
        if conn is None:
            return False
        try:
            with conn:
                cur = conn.execute("DELETE FROM governance_rules WHERE id = ?", (rule_id,))
                return bool(cur.rowcount)
        except sqlite3.Error:
            return False
        finally:
            conn.close()


def clear_rules() -> None:
    """Test helper — wipe every stored rule."""
    with _db_lock:
        conn = _connect()
        if conn is None:
            return
        try:
            with conn:
                conn.execute("DELETE FROM governance_rules")
        except sqlite3.Error:
            pass
        finally:
            conn.close()


def reset_ready_flag() -> None:
    """Test helper — force schema re-creation after the configured path changes."""
    global _db_ready
    _db_ready = False
