"""ELK sidecar — correctness, concurrency, and every way it can fail.

The sidecar is a performance optimisation on the critical path of every
diagram, so the bar is that it produces byte-identical layouts to the per-call
spawn and that any failure degrades to that spawn rather than to a failed
request. The failure paths matter more than the happy path.
"""

from __future__ import annotations

import os
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.adapters.outbound.config import env_config
from app.adapters.outbound.diagram import elk_bridge
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError, run_elk, shutdown_elk_sidecar

_NODE = shutil.which("node")


def _graph(n: int = 8, e: int = 6) -> dict:
    return {
        "id": "root",
        "layoutOptions": {"elk.algorithm": "layered", "elk.direction": "RIGHT"},
        "children": [{"id": f"n{i}", "width": 120, "height": 60} for i in range(n)],
        "edges": [
            {"id": f"e{i}", "sources": [f"n{i % n}"], "targets": [f"n{(i * 3 + 1) % n}"]}
            for i in range(e)
        ],
    }


def _positions(result: dict) -> list[tuple[str, float, float]]:
    return sorted((c["id"], c.get("x", 0), c.get("y", 0)) for c in result.get("children", []))


def _strip_internal(obj):
    """Drop elkjs's ``$H`` field before comparing two layout results.

    ``$H`` is an internal object counter that elkjs increments for every object
    it creates in the JS process. A fresh per-call process starts it near zero
    while a long-lived sidecar keeps counting, so the two paths legitimately
    disagree on it. It carries no layout meaning and is never read on the
    Python side (verified by grep across app/), unlike every geometry field
    below it, which must match exactly.
    """
    if isinstance(obj, dict):
        return {k: _strip_internal(v) for k, v in obj.items() if k != "$H"}
    if isinstance(obj, list):
        return [_strip_internal(v) for v in obj]
    return obj


class _Env:
    def __init__(self, **values: str) -> None:
        self.values = values
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        return False


@unittest.skipIf(_NODE is None, "node not on PATH")
class ElkSidecarTests(unittest.TestCase):
    def tearDown(self) -> None:
        shutdown_elk_sidecar()

    def test_sidecar_and_spawn_agree_exactly(self) -> None:
        """The fast path must not change a single coordinate.

        Compares the whole result — node geometry and every edge section —
        not just positions, because an edge routed differently would be just
        as wrong as a node moved.
        """
        for graph in (_graph(), _graph(20, 30), _graph(3, 2)):
            with _Env(ELK_SIDECAR_ENABLED="true"):
                via_sidecar = run_elk(graph)
            with _Env(ELK_SIDECAR_ENABLED="false"):
                via_spawn = run_elk(graph)
            self.assertEqual(
                _strip_internal(via_sidecar),
                _strip_internal(via_spawn),
                "sidecar layout diverged from the per-call spawn",
            )

    def test_repeated_layouts_on_a_warm_process_stay_stable(self) -> None:
        """State must not accumulate across requests and drift the result."""
        graph = _graph(12, 14)
        with _Env(ELK_SIDECAR_ENABLED="true"):
            first = _strip_internal(run_elk(graph))
            for _ in range(4):
                self.assertEqual(_strip_internal(run_elk(graph)), first)

    def test_process_is_reused_across_calls(self) -> None:
        """The whole point: one process, not one per call."""
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            first_pid = elk_bridge._sidecar._proc.pid
            for _ in range(3):
                run_elk(_graph())
            self.assertEqual(elk_bridge._sidecar._proc.pid, first_pid)

    def test_concurrent_callers_get_their_own_layouts(self) -> None:
        """Serialized behind a lock, so responses must never cross wires."""
        sizes = [4, 7, 11, 5, 9]
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())  # warm
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda n: run_elk(_graph(n, n - 1)), sizes))
        for want, result in zip(sizes, results):
            self.assertEqual(len(result["children"]), want)

    def test_a_dead_process_is_restarted(self) -> None:
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            first_pid = elk_bridge._sidecar._proc.pid
            elk_bridge._sidecar._proc.kill()
            elk_bridge._sidecar._proc.wait(timeout=5)

            result = run_elk(_graph())  # must transparently recover
            self.assertEqual(len(result["children"]), 8)
            self.assertNotEqual(elk_bridge._sidecar._proc.pid, first_pid)

    def test_shutdown_stops_the_process(self) -> None:
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            proc = elk_bridge._sidecar._proc
            shutdown_elk_sidecar()
            self.assertIsNone(elk_bridge._sidecar._proc)
            self.assertIsNotNone(proc.poll(), "process should have exited")

    def test_shutdown_is_idempotent(self) -> None:
        shutdown_elk_sidecar()
        shutdown_elk_sidecar()

    def test_disabled_never_starts_a_process(self) -> None:
        with _Env(ELK_SIDECAR_ENABLED="false"):
            run_elk(_graph())
        self.assertIsNone(elk_bridge._sidecar._proc)

    def test_a_bad_graph_fails_without_killing_the_process(self) -> None:
        """A caller's broken graph must not cost every later caller the fast path."""
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            pid = elk_bridge._sidecar._proc.pid

            with self.assertRaises(ElkLayoutError):
                # Edge referencing a node that does not exist.
                run_elk({"id": "root", "children": [{"id": "a", "width": 10, "height": 10}],
                         "edges": [{"id": "e", "sources": ["a"], "targets": ["ghost"]}]})

            self.assertEqual(elk_bridge._sidecar._proc.pid, pid, "process should have survived")
            self.assertEqual(len(run_elk(_graph())["children"]), 8)

    def test_sidecar_failure_falls_back_to_spawn(self) -> None:
        """A broken sidecar degrades to the slow path, not to a failed request."""
        with _Env(ELK_SIDECAR_ENABLED="true"):
            with patch.object(
                elk_bridge._sidecar, "layout", side_effect=ElkLayoutError("simulated wedge")
            ):
                result = run_elk(_graph())
        self.assertEqual(len(result["children"]), 8, "fallback should still produce a layout")

    def test_timeout_kills_the_wedged_process(self) -> None:
        """A late reply would be read as the next request's response, so the
        pipe cannot be reused — the process must be discarded, not retried."""
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            proc = elk_bridge._sidecar._proc
            with patch.object(elk_bridge, "_read_line_with_timeout", return_value=None):
                with self.assertRaises(ElkLayoutError):
                    elk_bridge._sidecar.layout(_graph(), _NODE, 0.1)
            self.assertIsNone(elk_bridge._sidecar._proc)
            self.assertIsNotNone(proc.poll(), "wedged process should have been killed")

    def test_desynchronised_response_is_rejected(self) -> None:
        """Returning another request's layout would be silent corruption."""
        with _Env(ELK_SIDECAR_ENABLED="true"):
            run_elk(_graph())
            with patch.object(
                elk_bridge, "_read_line_with_timeout",
                return_value='{"id": 99999, "ok": true, "result": {"children": []}}',
            ):
                with self.assertRaises(ElkLayoutError) as caught:
                    elk_bridge._sidecar.layout(_graph(), _NODE, 5)
            self.assertIn("desynchronised", str(caught.exception))
            self.assertIsNone(elk_bridge._sidecar._proc)
