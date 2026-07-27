"""Diagram version history — persistence, versioning, pruning, isolation."""

from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.adapters.outbound import history_store
from app.adapters.outbound.config import env_config

_XML = '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>'


class _TempHistory:
    """Point the store at a throwaway SQLite file."""

    def __init__(self, max_versions: int | None = None) -> None:
        self.max_versions = max_versions
        self._saved: dict[str, str | None] = {}
        self._dir: tempfile.TemporaryDirectory | None = None

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        env = {
            "DIAGRAM_HISTORY_ENABLED": "true",
            "DIAGRAM_HISTORY_PATH": str(Path(self._dir.name) / "history.sqlite3"),
        }
        if self.max_versions is not None:
            env["DIAGRAM_HISTORY_MAX_VERSIONS"] = str(self.max_versions)
        for key, value in env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        history_store.reset_ready_flag()
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        history_store.reset_ready_flag()
        if self._dir:
            self._dir.cleanup()
        return False


def _save(session: str, **kw):
    return history_store.save_version(session_id=session, xml=_XML, **kw)


class VersioningTests(unittest.TestCase):
    def test_versions_increment_per_session(self) -> None:
        with _TempHistory():
            self.assertEqual(_save("s1").version, 1)
            self.assertEqual(_save("s1").version, 2)
            self.assertEqual(_save("s1").version, 3)

    def test_sessions_are_isolated(self) -> None:
        with _TempHistory():
            _save("alpha")
            _save("alpha")
            self.assertEqual(_save("beta").version, 1)
            self.assertEqual(len(history_store.list_versions("alpha")), 2)
            self.assertEqual(len(history_store.list_versions("beta")), 1)

    def test_list_versions_is_newest_first(self) -> None:
        with _TempHistory():
            for _ in range(3):
                _save("s")
            self.assertEqual([v.version for v in history_store.list_versions("s")], [3, 2, 1])

    def test_get_latest_returns_highest_version(self) -> None:
        with _TempHistory():
            _save("s", prompt="first")
            _save("s", prompt="second")
            latest = history_store.get_latest("s")
            self.assertEqual(latest.version, 2)
            self.assertEqual(latest.prompt, "second")

    def test_round_trips_xml_ir_and_meta(self) -> None:
        with _TempHistory():
            saved = _save(
                "s", prompt="build me a vpc", platform="aws", provider="claude",
                generation_path="ir", renderer_used="mcp",
                ir={"view": "network", "cloud": {"id": "c"}},
                meta={"diagram_views": ["network"]},
            )
            loaded = history_store.get_version(saved.id)
            self.assertEqual(loaded.xml, _XML)
            self.assertEqual(loaded.ir, {"view": "network", "cloud": {"id": "c"}})
            self.assertEqual(loaded.meta, {"diagram_views": ["network"]})
            self.assertEqual(loaded.generation_path, "ir")
            self.assertEqual(loaded.platform, "aws")

    def test_summary_omits_xml_payload(self) -> None:
        with _TempHistory():
            summary = _save("s").summary()
            self.assertNotIn("xml", summary)
            self.assertEqual(summary["xml_chars"], len(_XML))


class RetentionTests(unittest.TestCase):
    def test_oldest_versions_pruned_beyond_cap(self) -> None:
        with _TempHistory(max_versions=3):
            for _ in range(6):
                _save("s")
            versions = history_store.list_versions("s")
            self.assertEqual([v.version for v in versions], [6, 5, 4])

    def test_pruning_does_not_touch_other_sessions(self) -> None:
        with _TempHistory(max_versions=2):
            for _ in range(5):
                _save("noisy")
            _save("quiet")
            self.assertEqual(len(history_store.list_versions("noisy")), 2)
            self.assertEqual(len(history_store.list_versions("quiet")), 1)


class SessionListingTests(unittest.TestCase):
    def test_lists_sessions_with_counts(self) -> None:
        with _TempHistory():
            _save("a", title="Alpha diagram")
            _save("a")
            _save("b", title="Beta diagram")
            by_id = {s["session_id"]: s for s in history_store.list_sessions()}
            self.assertEqual(by_id["a"]["versions"], 2)
            self.assertEqual(by_id["a"]["latest_version"], 2)
            self.assertEqual(by_id["a"]["title"], "Alpha diagram")
            self.assertEqual(by_id["b"]["versions"], 1)

    def test_delete_session_removes_all_versions(self) -> None:
        with _TempHistory():
            _save("doomed")
            _save("doomed")
            _save("kept")
            self.assertEqual(history_store.delete_session("doomed"), 2)
            self.assertEqual(history_store.list_versions("doomed"), [])
            self.assertEqual(len(history_store.list_versions("kept")), 1)


class ResilienceTests(unittest.TestCase):
    def test_disabled_history_is_a_silent_no_op(self) -> None:
        saved = os.environ.get("DIAGRAM_HISTORY_ENABLED")
        os.environ["DIAGRAM_HISTORY_ENABLED"] = "false"
        env_config._config_instance = None
        history_store.reset_ready_flag()
        try:
            self.assertIsNone(_save("s"))
            self.assertEqual(history_store.list_versions("s"), [])
            self.assertEqual(history_store.list_sessions(), [])
        finally:
            if saved is None:
                os.environ.pop("DIAGRAM_HISTORY_ENABLED", None)
            else:
                os.environ["DIAGRAM_HISTORY_ENABLED"] = saved
            env_config._config_instance = None
            history_store.reset_ready_flag()

    def test_empty_inputs_are_rejected_without_error(self) -> None:
        with _TempHistory():
            self.assertIsNone(history_store.save_version(session_id="", xml=_XML))
            self.assertIsNone(history_store.save_version(session_id="s", xml=""))

    def test_unknown_version_id_returns_none(self) -> None:
        with _TempHistory():
            self.assertIsNone(history_store.get_version("does-not-exist"))

    def test_concurrent_writes_do_not_lose_or_duplicate_versions(self) -> None:
        """Version numbering must stay consistent under parallel requests."""
        with _TempHistory(max_versions=100):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: _save("busy"), range(40)))
            self.assertTrue(all(r is not None for r in results))
            versions = sorted(v.version for v in history_store.list_versions("busy", limit=100))
            self.assertEqual(versions, list(range(1, 41)), "version numbers collided or were skipped")


if __name__ == "__main__":
    unittest.main()
