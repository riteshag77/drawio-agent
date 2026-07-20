"""Tests for multimodal attachment plumbing in app.adapters.outbound.llm.llm_client."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

from app.adapters.outbound.llm.llm_client import _build_human_message, call_llm


class BuildHumanMessageTests(unittest.TestCase):
    def test_no_attachments_returns_plain_string_content(self) -> None:
        msg = _build_human_message("hello", None)
        self.assertIsInstance(msg, HumanMessage)
        self.assertEqual(msg.content, "hello")

    def test_empty_attachments_list_returns_plain_string_content(self) -> None:
        msg = _build_human_message("hello", [])
        self.assertEqual(msg.content, "hello")

    def test_attachments_build_content_blocks(self) -> None:
        attachments = [
            {"filename": "diagram.png", "mime": "image/png", "data": "aGVsbG8="},
            {"filename": "architecture.pdf", "mime": "application/pdf", "data": "d29ybGQ="},
        ]
        msg = _build_human_message("describe this", attachments)
        self.assertIsInstance(msg.content, list)
        self.assertEqual(msg.content[0], {"type": "text", "text": "describe this"})

        image_block = msg.content[1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["source_type"], "base64")
        self.assertEqual(image_block["mime_type"], "image/png")
        self.assertEqual(image_block["data"], "aGVsbG8=")

        file_block = msg.content[2]
        self.assertEqual(file_block["type"], "file")
        self.assertEqual(file_block["mime_type"], "application/pdf")
        self.assertEqual(file_block["filename"], "architecture.pdf")


def _mock_provider_chain(invoke_side_effect):
    structured = MagicMock()
    structured.invoke.side_effect = invoke_side_effect
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


class CallLlmProviderMismatchTests(unittest.TestCase):
    @patch("app.adapters.outbound.llm.llm_client.build_langchain_client")
    @patch("app.adapters.outbound.llm.llm_client.require_api_key")
    @patch("app.adapters.outbound.llm.llm_client.get_provider")
    @patch("app.adapters.outbound.llm.llm_client.normalize_provider_key")
    @patch("app.adapters.outbound.llm.llm_client.get_config")
    def test_provider_failure_with_attachments_raises_clear_message(
        self, mock_get_config, mock_normalize, mock_get_provider, mock_require_key, mock_build_client,
    ) -> None:
        mock_normalize.return_value = "openrouter"
        mock_get_provider.return_value = MagicMock(default_model="some-model", structured_output=None)
        mock_get_config.return_value = MagicMock(llm_timeout_seconds=30)
        mock_build_client.return_value = _mock_provider_chain(RuntimeError("400 unsupported content type"))

        with self.assertRaises(ValueError) as ctx:
            call_llm(
                "system", "human", "openrouter", dict, None,
                [{"filename": "diagram.png", "mime": "image/png", "data": "aGVsbG8="}],
            )
        message = str(ctx.exception)
        self.assertIn("openrouter", message)
        self.assertIn("diagram.png", message)

    @patch("app.adapters.outbound.llm.llm_client.build_langchain_client")
    @patch("app.adapters.outbound.llm.llm_client.require_api_key")
    @patch("app.adapters.outbound.llm.llm_client.get_provider")
    @patch("app.adapters.outbound.llm.llm_client.normalize_provider_key")
    @patch("app.adapters.outbound.llm.llm_client.get_config")
    def test_provider_failure_without_attachments_propagates_original_error(
        self, mock_get_config, mock_normalize, mock_get_provider, mock_require_key, mock_build_client,
    ) -> None:
        mock_normalize.return_value = "claude"
        mock_get_provider.return_value = MagicMock(default_model="some-model", structured_output=None)
        mock_get_config.return_value = MagicMock(llm_timeout_seconds=30)
        mock_build_client.return_value = _mock_provider_chain(RuntimeError("rate limited"))

        with self.assertRaises(RuntimeError) as ctx:
            call_llm("system", "human", "claude", dict, None, None)
        self.assertEqual(str(ctx.exception), "rate limited")


if __name__ == "__main__":
    unittest.main()
