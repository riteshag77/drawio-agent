"""Smoke tests for DIAGRAM_VLM_QA fail-open corrective pass."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.diagram_vlm_qa import run_visual_qa
from tests.test_corridor_ir_layout import realistic_logical_ir


class VisualQaTests(unittest.TestCase):
    def test_fail_open_returns_xml(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        logger = MagicMock()
        out = run_visual_qa(xml, logger=logger)
        self.assertIn("mxGraphModel", out)
        logger.log.assert_called()


if __name__ == "__main__":
    unittest.main()
