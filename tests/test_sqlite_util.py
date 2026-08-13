"""Shared SQLite connection tuning — WAL and busy-timeout behaviour.

These are concurrency regressions, not schema tests: every store is reached
from threadpool workers via ``asyncio.to_thread``, so the properties that
matter are "a reader is not blocked by an open writer" and "a blocked writer
waits instead of raising".
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from app.adapters.outbound import sqlite_util


class SqliteUtilTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "probe.sqlite3"
        with closing(sqlite_util.connect(self.path)) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.commit()

    def test_journal_mode_is_wal(self) -> None:
        with closing(sqlite_util.connect(self.path)) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_busy_timeout_is_set(self) -> None:
        with closing(sqlite_util.connect(self.path)) as conn:
            timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout_ms, int(sqlite_util.BUSY_TIMEOUT_SECONDS * 1000))

    def test_reader_is_not_blocked_by_an_open_writer(self) -> None:
        """The default rollback journal fails this: the writer's lock excludes readers."""
        with closing(sqlite_util.connect(self.path)) as writer:
            writer.execute("INSERT INTO t (v) VALUES ('pending')")  # opens a write txn
            with closing(sqlite_util.connect(self.path)) as reader:
                rows = reader.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            # Uncommitted, so invisible — but the read itself must succeed.
            self.assertEqual(rows, 0)
            writer.commit()

        with closing(sqlite_util.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)

    def test_concurrent_writers_from_threads_all_commit(self) -> None:
        """Without a busy timeout, contended writers raise 'database is locked'."""
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def write(n: int) -> None:
            try:
                barrier.wait(timeout=10)
                with closing(sqlite_util.connect(self.path)) as conn:
                    conn.execute("INSERT INTO t (v) VALUES (?)", (str(n),))
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 — the assertion is "none of these"
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        with closing(sqlite_util.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 8)

    def test_degrades_instead_of_raising_when_pragmas_fail(self) -> None:
        """WAL is unavailable on some filesystems — a connection must still work."""

        class _Grumpy(sqlite3.Connection):
            def execute(self, *args, **kwargs):  # type: ignore[override]
                if args and str(args[0]).startswith("PRAGMA"):
                    raise sqlite3.OperationalError("pragma unsupported here")
                return super().execute(*args, **kwargs)

        conn = sqlite3.connect(self.path, factory=_Grumpy)
        try:
            sqlite_util._apply_pragmas(conn)  # must not raise
        finally:
            conn.close()
