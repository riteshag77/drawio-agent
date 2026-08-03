"""As-is image replication short-circuit in run_xml_pipeline.

``image_mode="as_is"`` (the API default for ChatRequest, but NOT the
``run_xml_pipeline`` function default — see the parameter's docstring-free
default of ``"cloud"``, kept for backward compatibility with every existing
direct caller/test that predates this flag) must bypass the cloud attachment
classifier and both cloud generation paths entirely.
"""

from __future__ import annotations

import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.adapters.outbound.diagram.compiler import compile_generic_ir, compile_network_ir
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericNode
from tests.test_ir_pipeline import _no_styles
from tests.test_network_compiler import realistic_ir

IMAGE_ATTACHMENT = [{"filename": "flowchart.png", "mime": "image/png", "data": "ZmFrZQ=="}]
PDF_ATTACHMENT = [{"filename": "flowchart.pdf", "mime": "application/pdf", "data": "ZmFrZQ=="}]


def _run(**kwargs):
    defaults = dict(
        user_prompt="Recreate this diagram",
        provider="gemini",
        platform="aws",
        logger=StepLogger(),
        diagram_renderer="local",
    )
    defaults.update(kwargs)
    return asyncio.run(xml_pipeline.run_xml_pipeline(**defaults))


def _generic_diagram() -> XmlDiagram:
    ir = GenericDiagramIR(
        title="Approval Flow",
        nodes=[GenericNode(id="a", shape="rectangle", label="Start"),
               GenericNode(id="b", shape="rectangle", label="End")],
        edges=[GenericEdge(source="a", target="b")],
    )
    return XmlDiagram(
        xml=compile_generic_ir(ir), assistant_message="Reproduced as-is.",
        suggestions=[], step_markers=[], ir_title="Approval Flow",
    )


class AsIsShortCircuitTests(unittest.TestCase):
    def test_as_is_mode_bypasses_classification_and_cloud_paths(self) -> None:
        with patch.object(xml_pipeline, "generate_generic_diagram_from_ir", return_value=_generic_diagram()) as generic_gen, \
                patch.object(xml_pipeline, "_classify_attachment_view") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct:
            result = _run(attachment_documents=IMAGE_ATTACHMENT, image_mode="as_is")

        generic_gen.assert_called_once()
        classify.assert_not_called()
        ir_gen.assert_not_called()
        direct.assert_not_called()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "generic_ir")
        cell_ids = {c.get("id") for c in ET.fromstring(result["xml"]).iter("mxCell")}
        self.assertIn("a", cell_ids)
        self.assertIn("b", cell_ids)

    def test_pdf_attachment_also_reaches_the_as_is_path(self) -> None:
        """PDFs are sent as vision blocks specifically to preserve an embedded
        diagram — a PDF export of a flowchart is as eligible as a PNG of one."""
        with patch.object(xml_pipeline, "generate_generic_diagram_from_ir", return_value=_generic_diagram()) as generic_gen, \
                patch.object(xml_pipeline, "_classify_attachment_view") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen:
            result = _run(attachment_documents=PDF_ATTACHMENT, image_mode="as_is")

        generic_gen.assert_called_once()
        classify.assert_not_called()
        ir_gen.assert_not_called()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generation_path"], "generic_ir")

    def test_cloud_mode_keeps_existing_classification_behavior(self) -> None:
        """Explicit image_mode='cloud' (or omitting it) must not touch the new path."""
        from tests.test_ir_pipeline import _IrEnabled

        network_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[], ir_title="Network",
        )
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "generate_generic_diagram_from_ir") as generic_gen, \
                patch.object(xml_pipeline, "_classify_attachment_view", return_value="logical") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=network_diagram) as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(attachment_documents=IMAGE_ATTACHMENT, image_mode="cloud")

        generic_gen.assert_not_called()
        classify.assert_called_once()
        ir_gen.assert_called_once()
        self.assertEqual(result["status"], "success")

    def test_three_d_request_ignores_as_is_mode(self) -> None:
        direct_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        with patch.object(xml_pipeline, "generate_generic_diagram_from_ir") as generic_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=direct_diagram) as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(attachment_documents=IMAGE_ATTACHMENT, image_mode="as_is", three_d=True)

        generic_gen.assert_not_called()
        direct.assert_called_once()
        self.assertEqual(result["status"], "success")

    def test_edit_mode_ignores_as_is_mode(self) -> None:
        direct_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[],
        )
        with patch.object(xml_pipeline, "generate_generic_diagram_from_ir") as generic_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram", return_value=direct_diagram) as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            result = _run(
                attachment_documents=IMAGE_ATTACHMENT, image_mode="as_is", current_xml="x" * 500,
            )

        generic_gen.assert_not_called()
        direct.assert_called_once()
        self.assertEqual(result["status"], "success")


if __name__ == "__main__":
    unittest.main()
