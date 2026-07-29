"""Corridor IR path (Logical / Security / Data-flow) wired through run_xml_pipeline.

Two integration points, mirroring test_ir_pipeline.py's Network coverage:
1. A corridor view as the *primary* page.
2. A corridor view as a *secondary* (extra) page alongside a different primary
   — the fan-out path (_run_view / _generate_ir_view_page), which is new in
   this session and untested by test_ir_pipeline.py.

Both must produce a real diagram end-to-end, and any failure in the IR path
must degrade to direct-XML rather than fail the request — same safety
contract as Network's IR path, extended to three more views.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram
from tests.test_corridor_compiler import (
    _non_routing_warnings,
    realistic_logical_ir,
    realistic_security_ir,
)
from tests.test_ir_pipeline import _FALLBACK_XML, _IrEnabled, _no_styles

import unittest


def _run(**kwargs):
    defaults = dict(
        user_prompt="Claims processing platform with Okta SSO and Workday integration",
        provider="gemini",
        platform="aws",
        logger=StepLogger(),
        diagram_renderer="local",
    )
    defaults.update(kwargs)
    return asyncio.run(xml_pipeline.run_xml_pipeline(**defaults))


class PrimaryCorridorViewTests(unittest.TestCase):
    def test_logical_as_primary_view_produces_a_diagram(self) -> None:
        ir = realistic_logical_ir()
        with _IrEnabled(views="logical"), \
                patch("app.core.services.agent.generate_corridor_ir", return_value=ir), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["logical"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "ir")
        cells = {c.get("id") for c in ET.fromstring(result["xml"]).iter("mxCell")}
        self.assertIn("domain_core", cells)
        self.assertIn("workday", cells)
        # Gap A (Fix 3): IR compilers never emit overview_panel themselves —
        # the primary-page flow must inject one, same as every other page.
        self.assertIn("overview_panel", cells)

    def test_security_as_primary_view_needs_no_repair(self) -> None:
        """No *geometry* issue should need fixing in compiler output. Edge-
        routing warnings (box/parallel overlap) are excluded — a repair-layer
        concern by design, never the compiler's job; see
        tests/test_edge_overlap_checks.py for dedicated coverage."""
        ir = realistic_security_ir()
        pre_repair = diagnose_xml(compile_corridor_ir(ir, page_name="Security"))
        self.assertEqual(pre_repair.errors, [], f"Security IR output needed repair: {pre_repair.errors}")
        self.assertEqual(
            _non_routing_warnings(pre_repair.warnings), [],
            f"Security IR output needed repair: {pre_repair.warnings}",
        )

    def test_corridor_ir_failure_falls_back_to_direct_xml(self) -> None:
        fallback = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(views="logical"), \
                patch("app.core.services.agent.generate_corridor_ir", side_effect=RuntimeError("boom")), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=fallback) as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["logical"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "direct_xml")
        direct.assert_called_once()

    def test_view_not_in_diagram_ir_views_stays_on_direct_xml(self) -> None:
        """Only views explicitly opted into DIAGRAM_IR_VIEWS use the IR path."""
        fallback = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(views="network"), \
                patch("app.core.services.agent.generate_corridor_ir") as corridor_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=fallback), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["logical"])

        self.assertEqual(result["generation_path"], "direct_xml")
        corridor_gen.assert_not_called()


class ExtraViewFanOutTests(unittest.TestCase):
    """A corridor view generated as a *secondary* page alongside a different primary."""

    def _network_primary_diagram(self) -> XmlDiagram:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="Network" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="cloud" value="AWS Cloud" style="rounded=1;container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="800" height="500" as="geometry"/></mxCell>'
            '<mxCell id="ecs" value="ECS" shape_type="aws_ecs" style="rounded=0;" vertex="1" parent="cloud">'
            '<mxGeometry x="80" y="120" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="Overview" style="rounded=1;arcSize=2;" vertex="1" parent="1">'
            '<mxGeometry x="1000" y="40" width="400" height="200" as="geometry"/></mxCell>'
            "</root></mxGraphModel></diagram></mxfile>"
        )
        return XmlDiagram(xml=xml, assistant_message="ok", suggestions=[], step_markers=[])

    def test_logical_as_extra_view_uses_ir_path(self) -> None:
        """Primary (Network) stays direct-XML; the Logical *extra* view uses IR."""
        primary = self._network_primary_diagram()
        logical_xml_diagram = XmlDiagram(
            xml=compile_corridor_ir(realistic_logical_ir(), page_name="Logical"),
            assistant_message="Generated Logical diagram.", suggestions=[], step_markers=[],
        )
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=primary), \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=logical_xml_diagram) as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["network", "logical"])

        self.assertEqual(result["status"], "success")
        # Primary page used direct XML (generation_path reflects the primary only).
        self.assertEqual(result["generation_path"], "direct_xml")
        # But the Logical extra view was generated via the IR dispatcher.
        ir_gen.assert_called_once()
        self.assertEqual(ir_gen.call_args.args[0], "logical")
        self.assertIn("Logical", result["xml"])
        self.assertIn("domain_core", result["xml"])

    def test_extra_view_ir_failure_falls_back_to_direct_xml_generation(self) -> None:
        """A broken IR call for the extra view must not drop that view or fail the request."""
        primary = self._network_primary_diagram()
        logical_direct = XmlDiagram(
            xml=(
                '<mxfile host="app.diagrams.net"><diagram name="Logical" id="d2">'
                "<mxGraphModel><root>"
                '<mxCell id="0"/><mxCell id="1" parent="0"/>'
                '<mxCell id="domain_core" value="Core" style="rounded=1;container=1;" vertex="1" parent="1">'
                '<mxGeometry x="40" y="40" width="200" height="150" as="geometry"/></mxCell>'
                '<mxCell id="cap1" value="Capability" vertex="1" parent="domain_core">'
                '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
                '<mxCell id="overview_panel" value="Overview" style="rounded=1;arcSize=2;" vertex="1" parent="1">'
                '<mxGeometry x="900" y="40" width="400" height="180" as="geometry"/></mxCell>'
                "</root></mxGraphModel></diagram></mxfile>"
            ),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=primary), \
                patch.object(
                    xml_pipeline, "generate_xml_diagram_from_ir",
                    side_effect=RuntimeError("provider exploded"),
                ), \
                patch.object(xml_pipeline, "_generate_single_view_page", return_value=logical_direct.xml) as direct_view, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["network", "logical"])

        self.assertEqual(result["status"], "success")
        direct_view.assert_called_once()
        self.assertIn("Logical", result["xml"])

    def test_extra_view_ir_disabled_by_default_uses_direct_xml(self) -> None:
        primary = self._network_primary_diagram()
        logical_direct = XmlDiagram(
            xml=(
                '<mxfile host="app.diagrams.net"><diagram name="Logical" id="d2">'
                "<mxGraphModel><root>"
                '<mxCell id="0"/><mxCell id="1" parent="0"/>'
                '<mxCell id="domain_core" value="Core" style="rounded=1;container=1;" vertex="1" parent="1">'
                '<mxGeometry x="40" y="40" width="200" height="150" as="geometry"/></mxCell>'
                '<mxCell id="cap1" value="Capability" vertex="1" parent="domain_core">'
                '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
                '<mxCell id="overview_panel" value="Overview" style="rounded=1;arcSize=2;" vertex="1" parent="1">'
                '<mxGeometry x="900" y="40" width="400" height="180" as="geometry"/></mxCell>'
                "</root></mxGraphModel></diagram></mxfile>"
            ),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        with _IrEnabled(enabled=False), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=primary), \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "_generate_single_view_page", return_value=logical_direct.xml), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["network", "logical"])

        self.assertEqual(result["status"], "success")
        ir_gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
