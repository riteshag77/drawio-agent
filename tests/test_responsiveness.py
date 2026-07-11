"""Fast-path / responsiveness helpers."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.adapters.outbound.diagram.drawio_mcp_client import (
    clear_style_cache,
    resolve_shape_styles,
)
from app.adapters.outbound.patterns.file_repository import get_pattern_repository


class PatternRepoCacheTests(unittest.TestCase):
    def test_singleton_repo(self) -> None:
        a = get_pattern_repository()
        b = get_pattern_repository()
        self.assertIs(a, b)
        self.assertIsNotNone(a.get_by_id("three_tier_web"))


class McpStyleCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        clear_style_cache()

    async def test_parallel_resolve_uses_cache_on_second_call(self) -> None:
        calls: list[str] = []

        async def fake_search(query: str, *, limit: int = 5):
            calls.append(query)
            await asyncio.sleep(0.01)
            return [f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{query.replace(' ', '_')};"]

        with patch(
            "app.adapters.outbound.diagram.drawio_mcp_client.search_shapes",
            new=AsyncMock(side_effect=fake_search),
        ):
            first = await resolve_shape_styles(["aws_lambda", "aws_s3", "aws_lambda"], platform="aws")
            self.assertEqual(len(first), 2)
            self.assertEqual(len(calls), 2)  # lambda + s3 once each
            second = await resolve_shape_styles(["aws_lambda", "aws_s3"], platform="aws")
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 2)  # cache hit — no new searches


if __name__ == "__main__":
    unittest.main()
