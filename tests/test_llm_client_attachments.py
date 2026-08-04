"""Tests for multimodal attachment plumbing in app.adapters.outbound.llm.llm_client."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.adapters.outbound.llm.llm_client import _build_human_message, call_llm


class _DummyOutput(BaseModel):
    """Stand-in structured-output type for tests exercising retry plumbing."""

    ok: bool = True


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
        mock_get_config.return_value = MagicMock(
            llm_timeout_seconds=30, llm_max_retries=1, llm_retry_backoff_base=0.0, llm_retry_backoff_max=0.0,
        )
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
        mock_get_config.return_value = MagicMock(
            llm_timeout_seconds=30, llm_max_retries=1, llm_retry_backoff_base=0.0, llm_retry_backoff_max=0.0,
        )
        mock_build_client.return_value = _mock_provider_chain(RuntimeError("rate limited"))

        with self.assertRaises(RuntimeError) as ctx:
            call_llm("system", "human", "claude", dict, None, None)
        self.assertEqual(str(ctx.exception), "rate limited")


class _TransientError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"{status_code} transient")
        self.status_code = status_code


class CallLlmTransientRetryTests(unittest.TestCase):
    @patch("app.adapters.outbound.llm.llm_client.time.sleep")
    @patch("app.adapters.outbound.llm.llm_client.build_langchain_client")
    @patch("app.adapters.outbound.llm.llm_client.require_api_key")
    @patch("app.adapters.outbound.llm.llm_client.get_provider")
    @patch("app.adapters.outbound.llm.llm_client.normalize_provider_key")
    @patch("app.adapters.outbound.llm.llm_client.get_config")
    def test_succeeds_after_configured_retries(
        self, mock_get_config, mock_normalize, mock_get_provider, mock_require_key, mock_build_client, mock_sleep,
    ) -> None:
        mock_normalize.return_value = "gemini"
        mock_get_provider.return_value = MagicMock(default_model="gemini-2.5-flash", structured_output="json_mode")
        mock_get_config.return_value = MagicMock(
            llm_timeout_seconds=30, llm_max_retries=2, llm_retry_backoff_base=1.0, llm_retry_backoff_max=10.0,
        )
        expected = {"ok": True}
        raw_msg = MagicMock()
        raw_msg.response_metadata = {"finish_reason": "STOP"}
        mock_build_client.return_value = _mock_provider_chain(
            [_TransientError(429), _TransientError(429), {"raw": raw_msg, "parsed": expected, "parsing_error": None}]
        )

        result = call_llm("system", "human", "gemini", _DummyOutput, None, None)

        self.assertEqual(result, expected)
        # Two retries needed (attempts 2 and 3 succeed on the 3rd call) -> sleep called twice, backoff doubling.
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [1.0, 2.0])

    @patch("app.adapters.outbound.llm.llm_client.time.sleep")
    @patch("app.adapters.outbound.llm.llm_client.build_langchain_client")
    @patch("app.adapters.outbound.llm.llm_client.require_api_key")
    @patch("app.adapters.outbound.llm.llm_client.get_provider")
    @patch("app.adapters.outbound.llm.llm_client.normalize_provider_key")
    @patch("app.adapters.outbound.llm.llm_client.get_config")
    def test_raises_after_exhausting_configured_retries(
        self, mock_get_config, mock_normalize, mock_get_provider, mock_require_key, mock_build_client, mock_sleep,
    ) -> None:
        mock_normalize.return_value = "gemini"
        mock_get_provider.return_value = MagicMock(default_model="gemini-2.5-flash", structured_output="json_mode")
        mock_get_config.return_value = MagicMock(
            llm_timeout_seconds=30, llm_max_retries=1, llm_retry_backoff_base=1.0, llm_retry_backoff_max=10.0,
        )
        mock_build_client.return_value = _mock_provider_chain(_TransientError(429))

        with self.assertRaises(ValueError) as ctx:
            call_llm("system", "human", "gemini", _DummyOutput, None, None)

        self.assertIn("gemini", str(ctx.exception))
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("app.adapters.outbound.llm.llm_client.time.sleep")
    @patch("app.adapters.outbound.llm.llm_client.build_langchain_client")
    @patch("app.adapters.outbound.llm.llm_client.require_api_key")
    @patch("app.adapters.outbound.llm.llm_client.get_provider")
    @patch("app.adapters.outbound.llm.llm_client.normalize_provider_key")
    @patch("app.adapters.outbound.llm.llm_client.get_config")
    def test_zero_retries_fails_immediately(
        self, mock_get_config, mock_normalize, mock_get_provider, mock_require_key, mock_build_client, mock_sleep,
    ) -> None:
        mock_normalize.return_value = "gemini"
        mock_get_provider.return_value = MagicMock(default_model="gemini-2.5-flash", structured_output="json_mode")
        mock_get_config.return_value = MagicMock(
            llm_timeout_seconds=30, llm_max_retries=0, llm_retry_backoff_base=1.0, llm_retry_backoff_max=10.0,
        )
        mock_build_client.return_value = _mock_provider_chain(_TransientError(429))

        with self.assertRaises(ValueError):
            call_llm("system", "human", "gemini", _DummyOutput, None, None)

        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
