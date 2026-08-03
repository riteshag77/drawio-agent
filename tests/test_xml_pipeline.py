"""Tests for run_xml_pipeline: deterministic XML repair (surgical edit path).

Fresh generation is IR-only. Malformed-XML retry lives on the 3D path only.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.adapters.outbound.diagram.xml_diagnostics import XmlDiagnostics
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram

MINIMAL_XML = (
    '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
    "<mxGraphModel><root>"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="iam_icon" value="IAM" style="html=1;" vertex="1" parent="1">'
    '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
    "</root></mxGraphModel></diagram></mxfile>"
)

# Substantial current_xml forces edit mode → surgical/direct XML (not IR).
_EDIT_BASE = "x" * 500


class DeterministicRepairOnlyTests(unittest.TestCase):
    """Format/structure repair must never call an LLM corrective pass."""

    def _run(self, *, diagnose_result, repair_side_effect):
        xml_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=diagnose_result), \
                patch.object(xml_pipeline, "repair_xml", side_effect=repair_side_effect):
            return asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename IAM to Identity",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                current_xml=_EDIT_BASE,
            ))

    def test_warnings_only_still_succeeds(self) -> None:
        warn_only = XmlDiagnostics(errors=[], warnings=["icon pitch 120px between 'a' and 'b' is outside range"])
        result = self._run(
            diagnose_result=warn_only,
            repair_side_effect=[(MINIMAL_XML, warn_only)],
        )
        self.assertEqual(result["status"], "success")

    def test_errors_do_not_trigger_llm_repair(self) -> None:
        """Residual structural errors are logged only — never sent to an LLM repair pass."""
        has_errors = XmlDiagnostics(errors=["dangling edge 'e1'"], warnings=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=XmlDiagram(
            xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[],
        )), patch.object(xml_pipeline, "diagnose_xml", return_value=has_errors), \
                patch.object(xml_pipeline, "repair_xml", return_value=(MINIMAL_XML, has_errors)), \
                patch("app.core.services.agent.repair_xml_diagram", create=True) as llm_repair:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="rename IAM to Identity",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                current_xml=_EDIT_BASE,
            ))
        llm_repair.assert_not_called()
        self.assertEqual(result["status"], "success")
        self.assertNotIn("repair_xml_diagram", dir(xml_pipeline))


class MalformedXmlRetryTests(unittest.TestCase):
    """Generation retry on truncated XML is allowed on the 3D path."""

    TRUNCATED_XML = (
        '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="broken" value="x" style="html=1;" vertex="1" parent="1">'
        '<mxGeometry x="0'
    )

    def _run(self, *, generate_side_effect):
        clean = XmlDiagnostics(errors=[], warnings=[])
        gen_mock = MagicMock(side_effect=generate_side_effect)
        with patch.object(xml_pipeline, "generate_xml_diagram", gen_mock), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=clean), \
                patch.object(xml_pipeline, "repair_xml", return_value=(MINIMAL_XML, clean)):
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="isometric 3d aws architecture",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                three_d=True,
            ))
        return gen_mock, result

    def test_malformed_first_attempt_retries_and_succeeds(self) -> None:
        truncated = XmlDiagram(xml=self.TRUNCATED_XML, assistant_message="ok", suggestions=[], step_markers=[])
        good = XmlDiagram(xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[])
        gen_mock, result = self._run(generate_side_effect=[truncated, good])

        self.assertEqual(gen_mock.call_count, 2)
        _, kwargs = gen_mock.call_args
        self.assertIn("retry_hint", kwargs)
        self.assertIsNotNone(kwargs["retry_hint"])
        self.assertEqual(result["status"], "success")
        self.assertIn("iam_icon", result["xml"])

    def test_malformed_on_retry_too_raises(self) -> None:
        truncated = XmlDiagram(xml=self.TRUNCATED_XML, assistant_message="ok", suggestions=[], step_markers=[])
        empty = XmlDiagram(xml="", assistant_message="ok", suggestions=[], step_markers=[])
        with self.assertRaises(ValueError):
            self._run(generate_side_effect=[truncated, empty])


class DegradedFeaturesTests(unittest.TestCase):
    def test_collects_quality_gate_and_quota(self) -> None:
        logger = StepLogger()
        logger.log(
            "views",
            "Logical view scored 40",
            level="warning",
            details={"degraded": True, "feature": "Logical view quality", "reason": "quality_gate"},
        )
        logger.log(
            "docs",
            "ADR notes branch failed: quota",
            level="warning",
            details={"quota_exceeded": True, "feature": "ADR notes"},
        )
        feats = xml_pipeline._collect_degraded_features(logger)
        by_feature = {f["feature"]: f["reason"] for f in feats}
        self.assertEqual(by_feature["Logical view quality"], "quality_gate")
        self.assertEqual(by_feature["ADR notes"], "quota")


if __name__ == "__main__":
    unittest.main()
