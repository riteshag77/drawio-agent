"""IR path wired through run_xml_pipeline (mocked LLM).

Fresh cloud generation is IR-only: success produces ``generation_path=ir``;
IR failure fails the request (no direct-XML fallback). Edit mode and 3D still
use direct XML.
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

    def test_ir_failure_fails_the_request(self) -> None:
        """Fresh generation is IR-only — no silent direct-XML fallback."""
        with _IrEnabled(), \
                patch("app.core.services.agent.generate_network_ir", side_effect=RuntimeError("provider exploded")), \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            with self.assertRaises(RuntimeError):
                _run()
        direct.assert_not_called()

    def test_disabled_flag_rejects_fresh_generation(self) -> None:
        with _IrEnabled(enabled=False), \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            with self.assertRaises(ValueError) as ctx:
                _run()
        self.assertIn("DIAGRAM_IR_ENABLED", str(ctx.exception))
        direct.assert_not_called()

    def test_edit_mode_still_uses_direct_xml(self) -> None:
        edit = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(), \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=edit) as direct, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(current_xml="x" * 500, user_prompt="rename ECS to Fargate")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "direct_xml")
        direct.assert_called_once()
        ir_gen.assert_not_called()

    def test_nonsurgical_edit_regenerates_via_ir(self) -> None:
        from app.core.services.agent import NonSurgicalEditError

        compiled = XmlDiagram(xml=_FALLBACK_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with _IrEnabled(), \
                patch.object(
                    xml_pipeline, "generate_xml_diagram",
                    side_effect=NonSurgicalEditError("too big"),
                ) as direct, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=compiled) as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(current_xml="x" * 500, user_prompt="redesign as multi-region active-active")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "ir")
        direct.assert_called_once()
        ir_gen.assert_called_once()


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

    def test_view_not_in_ir_views_is_not_eligible(self) -> None:
        with _IrEnabled(views="network"):
            self.assertFalse(xml_pipeline._should_use_ir_path("logical", None, False))
            self.assertTrue(xml_pipeline._should_use_ir_path("network", None, False))

    def test_disabled_by_default(self) -> None:
        with _IrEnabled(enabled=False):
            self.assertFalse(xml_pipeline._should_use_ir_path("network", None, False))


if __name__ == "__main__":
    unittest.main()
