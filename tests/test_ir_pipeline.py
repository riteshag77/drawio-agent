"""IR path wired through run_xml_pipeline (mocked LLM).

Covers the two things that decide whether this is safe to enable in production:
the IR path actually produces a diagram end-to-end, and any failure in it
degrades to the direct-XML path instead of failing the request.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.adapters.outbound.config import env_config
from app.adapters.outbound.diagram.compiler import compile_network_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram
from tests.test_network_compiler import _non_routing_warnings, realistic_ir

_FALLBACK_XML = (
    '<mxfile host="app.diagrams.net"><diagram name="Network" id="d1">'
    "<mxGraphModel><root>"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="cloud" value="AWS Cloud" style="rounded=1;container=1;" vertex="1" parent="1">'
    '<mxGeometry x="40" y="40" width="800" height="500" as="geometry"/></mxCell>'
    '<mxCell id="ecs" value="ECS" shape_type="aws_ecs" style="rounded=0;" vertex="1" parent="cloud">'
    '<mxGeometry x="80" y="120" width="78" height="78" as="geometry"/></mxCell>'
    "</root></mxGraphModel></diagram></mxfile>"
)


async def _no_styles(*_a, **_k):
    return {}


class _IrEnabled:
    """Enable the IR path for the duration of a test, resetting the config singleton."""

    def __init__(self, enabled: bool = True, views: str = "network") -> None:
        self.enabled = enabled
        self.views = views
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in (
            ("DIAGRAM_IR_ENABLED", "true" if self.enabled else "false"),
            ("DIAGRAM_IR_VIEWS", self.views),
            ("GAP_ANALYSIS_ENABLED", "false"),
        ):
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


def _run(**kwargs):
    defaults = dict(
        user_prompt="HA three-tier web app on AWS",
        provider="gemini",
        platform="aws",
        logger=StepLogger(),
        diagram_renderer="local",
        diagram_views=["network"],
    )
    defaults.update(kwargs)
    return asyncio.run(xml_pipeline.run_xml_pipeline(**defaults))


class IrPathTests(unittest.TestCase):
    def test_ir_path_produces_a_diagram_end_to_end(self) -> None:
        ir = realistic_ir()
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", return_value=ir), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "ir")
        cells = {c.get("id") for c in ET.fromstring(result["xml"]).iter("mxCell")}
        self.assertIn("vpc", cells)
        self.assertIn("rds1", cells)

    def test_ir_output_survives_the_pipeline_without_repairs(self) -> None:
        """The repair stage should find no *geometry* issue to fix in compiler
        output. Edge-routing warnings (box/parallel overlap) are excluded —
        those are a repair-layer concern by design (fix_edge_anchors_from_geometry's
        obstacle-avoiding router), never the compiler's job; see
        tests/test_edge_overlap_checks.py for dedicated coverage."""
        ir = realistic_ir()
        pre_repair = diagnose_xml(compile_network_ir(ir))
        self.assertEqual(pre_repair.errors, [], f"IR output needed repair: {pre_repair.errors}")
        self.assertEqual(
            _non_routing_warnings(pre_repair.warnings), [],
            f"IR output needed repair: {pre_repair.warnings}",
        )

    def test_ir_failure_falls_back_to_direct_xml(self) -> None:
        """A broken IR path must never turn a working request into an error."""
        fallback = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", side_effect=RuntimeError("provider exploded")), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=fallback) as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "direct_xml")
        direct.assert_called_once()

    def test_disabled_flag_uses_direct_xml(self) -> None:
        fallback = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(enabled=False), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=fallback) as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run()

        self.assertEqual(result["generation_path"], "direct_xml")
        direct.assert_called_once()


class EligibilityTests(unittest.TestCase):
    def test_edit_mode_is_not_eligible(self) -> None:
        """The IR cannot express 'preserve these exact ids and coordinates'."""
        with _IrEnabled():
            self.assertFalse(
                xml_pipeline._should_use_ir_path("network", "x" * 500, False)
            )

    def test_three_d_is_not_eligible(self) -> None:
        with _IrEnabled():
            self.assertFalse(xml_pipeline._should_use_ir_path("network", None, True))

    def test_non_network_views_are_not_eligible(self) -> None:
        with _IrEnabled():
            self.assertFalse(xml_pipeline._should_use_ir_path("logical", None, False))
            self.assertTrue(xml_pipeline._should_use_ir_path("network", None, False))

    def test_disabled_by_default(self) -> None:
        with _IrEnabled(enabled=False):
            self.assertFalse(xml_pipeline._should_use_ir_path("network", None, False))


if __name__ == "__main__":
    unittest.main()
