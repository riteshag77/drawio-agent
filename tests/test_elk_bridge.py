"""Tests for the ELK (elkjs) subprocess bridge."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError, run_elk


class RunElkTests(unittest.TestCase):
    def test_lays_out_a_simple_graph(self) -> None:
        graph = {
            "id": "root",
            "layoutOptions": {"elk.algorithm": "layered", "elk.direction": "RIGHT"},
            "children": [
                {"id": "n1", "width": 78, "height": 78},
                {"id": "n2", "width": 78, "height": 78},
            ],
            "edges": [{"id": "e1", "sources": ["n1"], "targets": ["n2"]}],
        }
        result = run_elk(graph)
        by_id = {c["id"]: c for c in result["children"]}
        self.assertIn("x", by_id["n1"])
        self.assertIn("x", by_id["n2"])
        self.assertNotEqual(by_id["n1"]["x"], by_id["n2"]["x"])
        edge = result["edges"][0]
        self.assertTrue(edge["sections"])

    def test_routes_around_an_obstacle_node(self) -> None:
        graph = {
            "id": "root",
            "layoutOptions": {
                "elk.algorithm": "layered",
                "elk.direction": "RIGHT",
                "elk.edgeRouting": "ORTHOGONAL",
            },
            "children": [
                {"id": "src", "width": 78, "height": 78},
                {"id": "mid", "width": 78, "height": 78},
                {"id": "tgt", "width": 78, "height": 78},
            ],
            "edges": [
                {"id": "e1", "sources": ["src"], "targets": ["mid"]},
                {"id": "e2", "sources": ["mid"], "targets": ["tgt"]},
                {"id": "e3", "sources": ["src"], "targets": ["tgt"]},
            ],
        }
        result = run_elk(graph)
        by_id = {c["id"]: c for c in result["children"]}
        edges = {e["id"]: e for e in result["edges"]}
        e3 = edges["e3"]["sections"][0]
        mid = by_id["mid"]
        mid_x0, mid_x1 = mid["x"], mid["x"] + mid["width"]
        mid_y0, mid_y1 = mid["y"], mid["y"] + mid["height"]

        points = [e3["startPoint"], *e3.get("bendPoints", []), e3["endPoint"]]
        # For every consecutive segment of the (orthogonal) route, it must not
        # pass through mid's box — i.e. whenever the segment's x-span overlaps
        # mid's x-span, its y must lie entirely outside mid's y-span.
        for (x1, y1), (x2, y2) in zip(
            [(p["x"], p["y"]) for p in points], [(p["x"], p["y"]) for p in points[1:]]
        ):
            seg_x0, seg_x1 = sorted((x1, x2))
            x_overlaps_mid = seg_x0 < mid_x1 and mid_x0 < seg_x1
            if x_overlaps_mid:
                seg_y0, seg_y1 = sorted((y1, y2))
                self.assertTrue(
                    seg_y1 <= mid_y0 or seg_y0 >= mid_y1,
                    f"segment ({x1},{y1})->({x2},{y2}) cuts through mid box "
                    f"({mid_x0},{mid_y0})-({mid_x1},{mid_y1})",
                )

    def test_missing_node_executable_raises_elk_layout_error(self) -> None:
        with patch("app.adapters.outbound.diagram.elk_bridge.shutil.which", return_value=None):
            with self.assertRaises(ElkLayoutError):
                run_elk({"id": "root"})

    def test_subprocess_timeout_raises_elk_layout_error(self) -> None:
        with patch("app.adapters.outbound.diagram.elk_bridge.shutil.which", return_value="node"):
            with patch(
                "app.adapters.outbound.diagram.elk_bridge.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="node", timeout=8),
            ):
                with self.assertRaises(ElkLayoutError):
                    run_elk({"id": "root"})

    def test_nonzero_exit_raises_elk_layout_error(self) -> None:
        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "boom"

        with patch("app.adapters.outbound.diagram.elk_bridge.shutil.which", return_value="node"):
            with patch("app.adapters.outbound.diagram.elk_bridge.subprocess.run", return_value=_Proc()):
                with self.assertRaises(ElkLayoutError):
                    run_elk({"id": "root"})

    def test_unparseable_output_raises_elk_layout_error(self) -> None:
        class _Proc:
            returncode = 0
            stdout = "not json"
            stderr = ""

        with patch("app.adapters.outbound.diagram.elk_bridge.shutil.which", return_value="node"):
            with patch("app.adapters.outbound.diagram.elk_bridge.subprocess.run", return_value=_Proc()):
                with self.assertRaises(ElkLayoutError):
                    run_elk({"id": "root"})


if __name__ == "__main__":
    unittest.main()
