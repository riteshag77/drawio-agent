"""Shared SQLite connection tuning for every store in this package.

Each store opens a short-lived connection per call, and those calls reach
SQLite from arbitrary threadpool workers — ``routes.py`` and ``admin_routes.py``
wrap ~40 store calls in ``asyncio.to_thread``, so two requests genuinely do
touch the same database file at the same time.

Two SQLite defaults make that unsafe:

* **Rollback journal** — a single writer locks the whole database, so a
  concurrent reader fails rather than reading the last committed state.
* **``busy_timeout = 0``** — a caller that finds the database locked does not
  wait at all; it raises ``sqlite3.OperationalError: database is locked``
  immediately.

``connect()`` fixes both: WAL so readers never block on the writer (and vice
versa), plus a busy timeout so a contended writer waits its turn instead of
failing. Use it everywhere instead of ``sqlite3.connect``.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("drawio-agent")

#: How long a blocked caller waits for the lock before raising. Generous
#: because every write here is small and short — reaching this means something
#: is genuinely wedged, not merely busy.
BUSY_TIMEOUT_SECONDS = 5.0


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection tuned for concurrent threadpool access.

    The caller still owns the connection and must close it.
    """
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    _apply_pragmas(conn)
    return conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # journal_mode is persisted in the database header, so this is a no-op read
    # on every connection after the first. The rest are per-connection and must
    # be re-applied each time.
    #
    # Best-effort: WAL is unavailable on some filesystems (network shares) and
    # on read-only files. Falling back to the default journal mode still gives
    # a working connection, so degrade rather than fail the request.
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
        conn.execute("PRAGMA journal_mode = WAL")
        # Safe to relax under WAL: a crash can lose the last commits but cannot
        # corrupt the database. These stores hold regenerable history/caches.
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        logger.debug("SQLite pragma setup degraded to defaults: %s", exc)
