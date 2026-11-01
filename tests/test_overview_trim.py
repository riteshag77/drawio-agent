"""Overview trim must keep badge-correlated technical steps."""

from __future__ import annotations

import html
import unittest

from app.adapters.outbound.diagram.architecture_docs import synthesize_ir_overview_body
from app.adapters.outbound.diagram.xml_diagnostics import _trim_overview_panel_value
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from tests.test_corridor_ir_layout import realistic_logical_ir


class OverviewTrimPreservesTechSteps(unittest.TestCase):
    def test_logical_technical_flow_survives_trim(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        body = synthesize_ir_overview_body(xml, "Logical", "Auth System")
        decoded = html.unescape(body)
        self.assertIn("Technical flow", decoded)
        self.assertIn("data-section=\"tech\"", decoded)
        self.assertIn("Orange badges", decoded)
        self.assertIn("Key facts", decoded)

        trimmed = _trim_overview_panel_value(decoded)
        self.assertIn("Technical flow", trimmed)
        self.assertIn("data-section=\"tech\"", trimmed)
        self.assertIn("Orange badges", trimmed)
        self.assertRegex(trimmed, r"1\.\s+")

    def test_gaps_still_preserved(self) -> None:
        val = (
            '<div style="font-size:22px;font-weight:bold;">Security</div>'
            '<div data-section="tech">1. Perimeter first.</div>'
            '<div data-section="gaps" style="color:#B85450;">Recommended gaps</div>'
            '<div data-section="gaps" style="color:#B85450;">• IdP</div>'
        )
        trimmed = _trim_overview_panel_value(val)
        self.assertIn("Recommended gaps", trimmed)
        self.assertIn("#B85450", trimmed)
        self.assertIn("Perimeter first", trimmed)


if __name__ == "__main__":
    unittest.main()
