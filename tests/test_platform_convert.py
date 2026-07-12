"""Tests for platform-switch auto-regeneration."""

from __future__ import annotations

import unittest

from app.adapters.inbound.web.schemas import ChatRequest
from app.adapters.outbound.llm.prompt_builders import build_platform_conversion_block
from app.adapters.outbound.utils.chat import is_minimal_diagram


class PlatformConvertTests(unittest.TestCase):
    def test_chat_request_accepts_platform_convert_fields(self) -> None:
        req = ChatRequest(
            messages=[{"role": "user", "content": "Convert to GCP"}],
            platform="gcp",
            platform_convert=True,
            previous_platform="aws",
        )
        self.assertTrue(req.platform_convert)
        self.assertEqual(req.previous_platform, "aws")
        self.assertEqual(req.platform, "gcp")

    def test_conversion_block_includes_platforms(self) -> None:
        block = build_platform_conversion_block("aws", "gcp")
        self.assertIn("PLATFORM CONVERSION", block)
        self.assertIn("Amazon Web Services", block)
        self.assertIn("Google Cloud Platform", block)
        self.assertIn("shape_type", block)
        self.assertIn("#4285F4", block)

    def test_conversion_block_includes_xml_when_no_architecture(self) -> None:
        xml = "<mxfile>" + ("x" * 400) + "</mxfile>"
        block = build_platform_conversion_block("aws", "azure", current_xml=xml)
        self.assertIn("draw.io XML", block)
        self.assertIn("<mxfile>", block)

    def test_platform_convert_with_substantial_xml(self) -> None:
        """platform_convert flag is set even when current_xml would normally prefer edit path."""
        from app.adapters.outbound.diagram.draftsman import generate_xml
        from app.domain.entities import ArchitectureDiagram, Node

        arch = ArchitectureDiagram(
            nodes=[Node(id="alb1", label="ALB", shape_type="aws_alb", step_number=1)],
            edges=[],
            suggestions=[],
        )
        sample_xml = generate_xml(arch, platform="aws")
        self.assertFalse(is_minimal_diagram(sample_xml))
        req = ChatRequest(
            messages=[{"role": "user", "content": "Convert to Azure"}],
            platform="azure",
            platform_convert=True,
            previous_platform="aws",
            current_xml=sample_xml,
        )
        self.assertTrue(req.platform_convert)


if __name__ == "__main__":
    unittest.main()
