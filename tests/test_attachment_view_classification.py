"""An uploaded image may itself depict a specific lens (Network/Security/Data-flow)
rather than the generic Logical default — classify it so that lens becomes the
faithfully-replicated primary page, with Logical still fanning out from it."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram
from tests.test_corridor_compiler import realistic_logical_ir
from tests.test_ir_pipeline import _IrEnabled, _no_styles
from tests.test_network_compiler import realistic_ir

IMAGE_ATTACHMENT = [{"filename": "diagram.png", "mime": "image/png", "data": "ZmFrZQ=="}]
TEXT_ATTACHMENT = [{"filename": "notes.txt", "mime": "text/plain", "data": "aGVsbG8="}]


def _run(**kwargs):
    defaults = dict(
        user_prompt="Recreate this diagram",
        provider="gemini",
        platform="aws",
        logger=StepLogger(),
        diagram_renderer="local",
    )
    defaults.update(kwargs)
    import asyncio
    return asyncio.run(xml_pipeline.run_xml_pipeline(**defaults))


class ClassifyAttachmentViewUnitTests(unittest.TestCase):
    def test_no_attachments_returns_none_without_calling_llm(self) -> None:
        with patch.object(xml_pipeline, "get_llm") as get_llm:
            result = xml_pipeline._classify_attachment_view(
                attachment_documents=None, human_prompt="hi",
                provider_key="gemini", logger=None,
            )
        self.assertIsNone(result)
        get_llm.assert_not_called()

    def test_returns_classified_view(self) -> None:
        fake_llm = MagicMock(return_value=xml_pipeline._AttachmentViewClassification(view="network"))
        with patch.object(xml_pipeline, "get_llm", return_value=fake_llm):
            result = xml_pipeline._classify_attachment_view(
                attachment_documents=IMAGE_ATTACHMENT, human_prompt="hi",
                provider_key="gemini", logger=None,
            )
        self.assertEqual(result, "network")

    def test_llm_failure_returns_none(self) -> None:
        fake_llm = MagicMock(side_effect=RuntimeError("boom"))
        with patch.object(xml_pipeline, "get_llm", return_value=fake_llm):
            result = xml_pipeline._classify_attachment_view(
                attachment_documents=IMAGE_ATTACHMENT, human_prompt="hi",
                provider_key="gemini", logger=StepLogger(),
            )
        self.assertIsNone(result)


class ClassificationWiringTests(unittest.TestCase):
    def _mock_generation(self, ir_gen_mock):
        network_diagram = XmlDiagram(
            xml=compile_network_ir(realistic_ir()),
            assistant_message="ok", suggestions=[], step_markers=[], ir_title="Network",
        )
        logical_diagram = XmlDiagram(
            xml=compile_corridor_ir(realistic_logical_ir(), page_name="Logical"),
            assistant_message="ok", suggestions=[], step_markers=[], ir_title="Logical",
        )

        def _from_ir(view, *_a, **_k):
            if view == "network":
                return network_diagram
            if view == "logical":
                return logical_diagram
            raise AssertionError(f"unexpected view {view!r}")

        ir_gen_mock.side_effect = _from_ir

    def test_specific_view_detected_becomes_primary_logical_still_fans_out(self) -> None:
        with _IrEnabled(views="logical,network"), \
                patch.object(xml_pipeline, "_classify_attachment_view", return_value="network") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            self._mock_generation(ir_gen)
            result = _run(attachment_documents=IMAGE_ATTACHMENT)

        classify.assert_called_once()
        direct.assert_not_called()
        self.assertEqual(result["status"], "success")
        called_views = [c.args[0] for c in ir_gen.call_args_list]
        self.assertIn("network", called_views)
        self.assertIn("logical", called_views)

    def test_generic_classification_keeps_logical_default_primary(self) -> None:
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "_classify_attachment_view", return_value="logical") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            self._mock_generation(ir_gen)
            result = _run(attachment_documents=IMAGE_ATTACHMENT)

        classify.assert_called_once()
        self.assertEqual(result["status"], "success")
        called_views = [c.args[0] for c in ir_gen.call_args_list]
        self.assertEqual(called_views, ["logical"])

    def test_classification_failure_falls_back_to_default_primary(self) -> None:
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "_classify_attachment_view", return_value=None) as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            self._mock_generation(ir_gen)
            result = _run(attachment_documents=IMAGE_ATTACHMENT)

        classify.assert_called_once()
        self.assertEqual(result["status"], "success")
        called_views = [c.args[0] for c in ir_gen.call_args_list]
        self.assertEqual(called_views, ["logical"])

    def test_no_image_attachment_skips_classification(self) -> None:
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "_classify_attachment_view") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram_from_ir") as ir_gen, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            self._mock_generation(ir_gen)
            _run(attachment_documents=TEXT_ATTACHMENT)

        classify.assert_not_called()

    def test_edit_mode_skips_classification(self) -> None:
        """An attached image on an edit turn (current_xml present) shouldn't jump tabs."""
        with _IrEnabled(views="logical"), \
                patch.object(xml_pipeline, "_classify_attachment_view") as classify, \
                patch.object(xml_pipeline, "generate_xml_diagram") as direct, \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_no_styles):
            direct.return_value = XmlDiagram(
                xml=compile_network_ir(realistic_ir()),
                assistant_message="ok", suggestions=[], step_markers=[],
            )
            _run(attachment_documents=IMAGE_ATTACHMENT, current_xml="x" * 500)

        classify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
