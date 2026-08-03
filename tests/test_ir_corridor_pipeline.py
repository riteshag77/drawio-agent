"""Corridor IR path (Logical / Security / Data-flow) wired through run_xml_pipeline.

Fresh multi-view generation is IR-only for every opted-in view. Failure fails
the request — no direct-XML fallback for primary or extra views.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram
from tests.test_corridor_compiler import (
    _non_routing_warnings,
    realistic_logical_ir,
    realistic_security_ir,
)
from tests.test_ir_pipeline import _IrEnabled, _no_styles
from tests.test_network_compiler import realistic_ir

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
        self.assertIn("overview_panel", cells)

    def test_security_as_primary_view_needs_no_repair(self) -> None:
        ir = realistic_security_ir()
        pre_repair = diagnose_xml(compile_corridor_ir(ir, page_name="Security"))
        self.assertEqual(pre_repair.errors, [], f"Security IR output needed repair: {pre_repair.errors}")
        self.assertEqual(
            _non_routing_warnings(pre_repair.warnings), [],
            f"Security IR output needed repair: {pre_repair.warnings}",
        )

    def test_corridor_ir_failure_fails_the_request(self) -> None:
        with _IrEnabled(views="logical"), \
                patch("app.core.services.agent.generate_corridor_ir", side_effect=RuntimeError("boom")), \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            with self.assertRaises(RuntimeError):
                _run(diagram_views=["logical"])
        direct.assert_not_called()

    def test_view_not_in_diagram_ir_views_is_rejected(self) -> None:
        with _IrEnabled(views="network"), \
                patch("app.core.services.agent.generate_corridor_ir") as corridor_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            with self.assertRaises(ValueError) as ctx:
                _run(diagram_views=["logical"])
        self.assertIn("DIAGRAM_IR_VIEWS", str(ctx.exception))
        corridor_gen.assert_not_called()
        direct.assert_not_called()


class ExtraViewFanOutTests(unittest.TestCase):
    """A corridor view generated as a *secondary* page alongside a Network primary."""

    def test_logical_as_extra_view_uses_ir_path(self) -> None:
        network_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[], ir_title="Network",
        )
        logical_diagram = XmlDiagram(
            xml=compile_corridor_ir(realistic_logical_ir(), page_name="Logical"),
            assistant_message="Generated Logical diagram.", suggestions=[], step_markers=[],
            ir_title="Logical",
        )

        def _from_ir(view, *_a, **_k):
            if view == "network":
                return network_diagram
            if view == "logical":
                return logical_diagram
            raise AssertionError(f"unexpected view {view!r}")

        with _IrEnabled(views="network,logical"), \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir", side_effect=_from_ir) as ir_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["network", "logical"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "ir")
        direct.assert_not_called()
        called_views = [c.args[0] for c in ir_gen.call_args_list]
        self.assertIn("network", called_views)
        self.assertIn("logical", called_views)
        self.assertIn("Logical", result["xml"])
        self.assertIn("domain_core", result["xml"])

    def test_extra_view_ir_failure_soft_degrades(self) -> None:
        """Primary succeeds; failed secondary tab is omitted + degraded_features."""
        network_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[], ir_title="Network",
        )
        logger = StepLogger()

        def _from_ir(view, *_a, **_k):
            if view == "network":
                return network_diagram
            raise RuntimeError("provider exploded")

        with _IrEnabled(views="network,logical"), \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir", side_effect=_from_ir), \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(diagram_views=["network", "logical"], logger=logger)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "ir")
        direct.assert_not_called()
        self.assertNotIn("domain_core", result["xml"])
        features = {d["feature"] for d in result.get("degraded_features") or []}
        self.assertIn("Additional view generation", features)


if __name__ == "__main__":
    unittest.main()
