"""Tests for run_xml_pipeline's corrective-repair-pass trigger condition."""

from __future__ import annotations

import asyncio
import os
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


class RepairPassTriggerTests(unittest.TestCase):
    """Regression: a cosmetic warning alone (no structural errors) must not
    trigger the expensive corrective LLM pass. A real incident traced through
    logs/drawio-agent.log showed a warnings-only diagnostic ("icon pitch 120px
    ... outside the 150-190px range", zero errors) firing a second ~60-90s LLM
    call anyway, which pushed the request past request_timeout_seconds. Since
    the timeout can't actually cancel the in-flight call (see
    _with_request_timeout's docstring), that second call finished successfully
    seconds later with its result silently discarded — then the user's retry
    ran the whole two-call sequence again from scratch. See the comment above
    the ``if diagnostics.errors:`` check in xml_pipeline.py."""

    def _run(self, *, diagnose_result, repair_side_effect, repair_diagram_mock):
        xml_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="ok", suggestions=[], step_markers=[])
        with patch.object(xml_pipeline, "generate_xml_diagram", return_value=xml_diagram), \
                patch.object(xml_pipeline, "diagnose_xml", return_value=diagnose_result), \
                patch.object(xml_pipeline, "repair_xml", side_effect=repair_side_effect), \
                patch.object(xml_pipeline, "repair_xml_diagram", repair_diagram_mock):
            return asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="draw something",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
            ))

    def test_warnings_only_does_not_trigger_corrective_pass(self) -> None:
        warn_only = XmlDiagnostics(errors=[], warnings=["icon pitch 120px between 'a' and 'b' is outside range"])
        repair_diagram_mock = MagicMock()
        result = self._run(
            diagnose_result=warn_only,
            repair_side_effect=[(MINIMAL_XML, warn_only)],
            repair_diagram_mock=repair_diagram_mock,
        )
        repair_diagram_mock.assert_not_called()
        self.assertEqual(result["status"], "success")

    def test_errors_still_trigger_corrective_pass(self) -> None:
        has_errors = XmlDiagnostics(errors=["dangling edge 'e1'"], warnings=[])
        clean = XmlDiagnostics(errors=[], warnings=[])
        repaired_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="fixed", suggestions=[], step_markers=[])
        repair_diagram_mock = MagicMock(return_value=repaired_diagram)
        result = self._run(
            diagnose_result=has_errors,
            repair_side_effect=[(MINIMAL_XML, has_errors), (MINIMAL_XML, clean)],
            repair_diagram_mock=repair_diagram_mock,
        )
        repair_diagram_mock.assert_called_once()
        self.assertEqual(result["status"], "success")

    def test_repair_stops_at_default_max_attempts_when_errors_persist(self) -> None:
        """Default XML_REPAIR_MAX_ATTEMPTS=1 — a diagram whose errors the LLM never
        actually clears must not loop indefinitely burning LLM calls."""
        has_errors = XmlDiagnostics(errors=["dangling edge 'e1'"], warnings=[])
        repaired_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="fixed", suggestions=[], step_markers=[])
        repair_diagram_mock = MagicMock(return_value=repaired_diagram)
        # repair_xml never clears the error, no matter how many times it's called.
        result = self._run(
            diagnose_result=has_errors,
            repair_side_effect=[(MINIMAL_XML, has_errors)] * 5,
            repair_diagram_mock=repair_diagram_mock,
        )
        self.assertEqual(repair_diagram_mock.call_count, 1)
        self.assertEqual(result["status"], "success")

    def test_repair_honors_configured_max_attempts(self) -> None:
        has_errors = XmlDiagnostics(errors=["dangling edge 'e1'"], warnings=[])
        repaired_diagram = XmlDiagram(xml=MINIMAL_XML, assistant_message="fixed", suggestions=[], step_markers=[])
        repair_diagram_mock = MagicMock(return_value=repaired_diagram)
        with patch.dict(os.environ, {"XML_REPAIR_MAX_ATTEMPTS": "3"}):
            result = self._run(
                diagnose_result=has_errors,
                repair_side_effect=[(MINIMAL_XML, has_errors)] * 5,
                repair_diagram_mock=repair_diagram_mock,
            )
        self.assertEqual(repair_diagram_mock.call_count, 3)
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
