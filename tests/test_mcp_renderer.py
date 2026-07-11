"""Tests for MCP style overrides and renderer mode selection."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.adapters.outbound.diagram.mcp_renderer import _normalize_renderer, render_architecture_xml
from app.adapters.outbound.diagram.styles import build_style, clear_style_overrides, set_style_overrides
from app.domain.entities import ArchitectureDiagram, Node


class StyleOverrideTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_style_overrides()

    def test_override_wins(self) -> None:
        set_style_overrides({"aws_lambda": "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;"})
        style = build_style("aws_lambda")
        self.assertIn("resIcon=mxgraph.aws4.lambda", style)
        clear_style_overrides()
        style2 = build_style("aws_lambda")
        self.assertIn("lambda", style2.lower())


class McpRendererTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        clear_style_overrides()

    def test_normalize_renderer(self) -> None:
        self.assertEqual(_normalize_renderer("LOCAL"), "local")
        self.assertEqual(_normalize_renderer("nope"), "auto")

    async def test_local_skips_mcp(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[Node(id="a", label="Lambda", shape_type="aws_lambda", step_number=1)],
            edges=[],
            suggestions=[],
        )
        with patch("app.adapters.outbound.diagram.mcp_renderer.resolve_shape_styles", new_callable=AsyncMock) as mock_resolve:
            xml, used, preview = await render_architecture_xml(arch, "aws", diagram_renderer="local")
            mock_resolve.assert_not_called()
            self.assertEqual(used, "local")
            self.assertIsNone(preview)
            self.assertIn("mxGraphModel", xml)

    async def test_mcp_fallback_on_error(self) -> None:
        from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError

        arch = ArchitectureDiagram(
            nodes=[Node(id="a", label="Lambda", shape_type="aws_lambda", step_number=1)],
            edges=[],
            suggestions=[],
        )
        with patch(
            "app.adapters.outbound.diagram.mcp_renderer.resolve_shape_styles",
            new_callable=AsyncMock,
            side_effect=DrawioMcpError("down"),
        ):
            xml, used, _ = await render_architecture_xml(arch, "aws", diagram_renderer="auto")
            self.assertEqual(used, "local")
            self.assertIn("mxGraphModel", xml)


if __name__ == "__main__":
    unittest.main()
