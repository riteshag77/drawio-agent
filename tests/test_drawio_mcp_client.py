"""Tests for the draw.io MCP client's miss-batching behavior (chunked sessions)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.adapters.outbound.diagram.drawio_mcp_client import (
    DrawioMcpError,
    _chunk,
    resolve_shape_styles,
)


class ChunkTests(unittest.TestCase):
    def test_chunk_respects_max_chunks(self) -> None:
        self.assertEqual(len(_chunk(["a", "b", "c", "d", "e"], 2)), 2)

    def test_chunk_never_exceeds_item_count(self) -> None:
        self.assertEqual(_chunk(["a"], 5), [["a"]])

    def test_chunk_preserves_all_items_in_order(self) -> None:
        items = [f"s{i}" for i in range(9)]
        flat = [x for group in _chunk(items, 3) for x in group]
        self.assertEqual(flat, items)

    def test_single_chunk_when_max_chunks_is_one(self) -> None:
        self.assertEqual(_chunk(["a", "b"], 1), [["a", "b"]])

    def test_empty_items_returns_the_empty_list_as_one_chunk(self) -> None:
        self.assertEqual(_chunk([], 4), [[]])


def _fake_config(concurrency: int = 4) -> MagicMock:
    cfg = MagicMock()
    cfg.drawio_mcp_concurrency = concurrency
    cfg.drawio_lib_aws = "mxgraph.aws4"
    cfg.drawio_lib_gcp = "mxgraph.gcp2"
    cfg.drawio_lib_azure = "img/lib/azure2"
    return cfg


class ResolveShapeStylesBatchingTests(unittest.IsolatedAsyncioTestCase):
    async def test_misses_are_batched_into_at_most_concurrency_sessions(self) -> None:
        """6 misses with concurrency=2 must cost at most 2 MCP sessions, not 6."""
        calls_log: list[list] = []

        async def fake_mcp_call_tools(calls):
            calls_log.append(list(calls))
            return [
                f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.svc{i};" for i in range(len(calls))
            ]

        with patch(
            "app.adapters.outbound.diagram.drawio_mcp_client._mcp_call_tools", fake_mcp_call_tools,
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_get", return_value=None,
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_put",
        ) as mock_put, patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.get_config", return_value=_fake_config(2),
        ):
            shape_types = [f"aws_svc{i}" for i in range(6)]
            resolved = await resolve_shape_styles(shape_types, platform="aws")

        self.assertLessEqual(len(calls_log), 2, calls_log)
        # Every call batch should carry more than one query once chunked (6 items / 2 chunks = 3 each)
        self.assertTrue(any(len(batch) > 1 for batch in calls_log), calls_log)
        self.assertEqual(len(resolved), 6)
        self.assertEqual(mock_put.call_count, 6)

    async def test_one_failing_chunk_does_not_lose_other_chunks_results(self) -> None:
        async def fake_mcp_call_tools(calls):
            query = calls[0][1]["query"]
            if "bad" in query:
                raise DrawioMcpError("simulated MCP failure")
            return ["shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ok;" for _ in calls]

        with patch(
            "app.adapters.outbound.diagram.drawio_mcp_client._mcp_call_tools", fake_mcp_call_tools,
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_get", return_value=None,
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_put",
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.get_config", return_value=_fake_config(2),
        ):
            shape_types = ["aws_bad1", "aws_bad2", "aws_ok1", "aws_ok2"]
            resolved = await resolve_shape_styles(shape_types, platform="aws")

        self.assertNotIn("aws_bad1", resolved)
        self.assertNotIn("aws_bad2", resolved)
        self.assertIn("aws_ok1", resolved)
        self.assertIn("aws_ok2", resolved)

    async def test_cache_hits_never_reach_mcp(self) -> None:
        async def fake_mcp_call_tools(calls):
            raise AssertionError("MCP should not be called when the cache already has every shape")

        with patch(
            "app.adapters.outbound.diagram.drawio_mcp_client._mcp_call_tools", fake_mcp_call_tools,
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_get",
            return_value="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;",
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.cache_put",
        ), patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.get_config", return_value=_fake_config(),
        ):
            resolved = await resolve_shape_styles(["aws_lambda"], platform="aws")

        self.assertEqual(resolved, {"aws_lambda": "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;"})


if __name__ == "__main__":
    unittest.main()
