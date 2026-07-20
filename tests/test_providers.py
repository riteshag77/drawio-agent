"""Provider registry smoke tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.exceptions import OutputParserException

from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.llm.providers import MODEL_IDS, ProviderSpec, get_provider, reload_providers


class ProviderTests(unittest.TestCase):
    def test_gateway_providers_are_registered(self) -> None:
        for key in ("openrouter", "opencode"):
            self.assertIn(key, MODEL_IDS)
            self.assertIsInstance(MODEL_IDS[key], str)
            self.assertTrue(len(MODEL_IDS[key]) > 0)

    def test_cursor_is_registered_as_cursor_agents_provider(self) -> None:
        spec = get_provider("cursor")
        self.assertEqual(spec.kind, "cursor_agents")
        self.assertEqual(spec.base_url_env, "CURSOR_API_BASE_URL")
        self.assertEqual(spec.api_key_env, "CURSOR_API_KEY")
        self.assertIsNone(spec.client_class)

    def test_normalize_gateway_base_url_strips_trailing_slash(self) -> None:
        from app.adapters.outbound.llm.providers import normalize_gateway_base_url
        self.assertEqual(
            normalize_gateway_base_url("https://example.com/v1/", "https://example.com/v1"),
            "https://example.com/v1",
        )
        self.assertEqual(
            normalize_gateway_base_url("https://example.com/v1", "https://example.com/v1"),
            "https://example.com/v1",
        )


class ProviderRegistryLoaderTests(unittest.TestCase):
    """Exercise the config/providers.json loader directly, restoring real state after each test."""

    def setUp(self) -> None:
        self._orig_path = providers_module.PROVIDERS_CONFIG_PATH

    def tearDown(self) -> None:
        providers_module.PROVIDERS_CONFIG_PATH = self._orig_path
        reload_providers()

    def _write_and_reload(self, content: str) -> None:
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_path = tmp_dir / "providers.json"
        tmp_path.write_text(content, encoding="utf-8")
        providers_module.PROVIDERS_CONFIG_PATH = tmp_path
        reload_providers()

    def test_missing_file_raises(self) -> None:
        providers_module.PROVIDERS_CONFIG_PATH = Path(tempfile.mkdtemp()) / "does_not_exist.json"
        with self.assertRaises(FileNotFoundError):
            reload_providers()

    def test_malformed_json_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self._write_and_reload("{not valid json")

    def test_unknown_client_discriminator_raises(self) -> None:
        record = [{
            "key": "made_up", "client": "made_up", "default_model": "x", "api_key_env": "X_API_KEY",
        }]
        with self.assertRaises(ValueError):
            self._write_and_reload(json.dumps(record))

    def test_duplicate_key_raises(self) -> None:
        record = [
            {"key": "openai", "client": "openai", "default_model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
            {"key": "openai", "client": "openai", "default_model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY"},
        ]
        with self.assertRaises(ValueError):
            self._write_and_reload(json.dumps(record))

    def test_missing_default_model_raises(self) -> None:
        record = [{"key": "openai", "client": "openai", "api_key_env": "OPENAI_API_KEY"}]
        with self.assertRaises(ValueError):
            self._write_and_reload(json.dumps(record))

    def test_resolve_gateway_base_url_raises_when_unset(self) -> None:
        spec = ProviderSpec(
            key="unconfigured",
            client_class=None,
            default_model="auto",
            api_key_env="UNCONFIGURED_API_KEY",
            base_url_env="UNCONFIGURED_BASE_URL_NOT_SET",
            base_url_default="",
        )
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("UNCONFIGURED_BASE_URL_NOT_SET", None)
            with self.assertRaises(ValueError):
                providers_module.resolve_gateway_base_url(spec)


class CallLlmRetryTests(unittest.TestCase):
    """call_llm() retries exactly once on OutputParserException, then propagates if still failing."""

    def _mock_llm(self, invoke_side_effects):
        structured = MagicMock()
        structured.invoke.side_effect = invoke_side_effects
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        return llm, structured

    def test_retries_once_then_succeeds(self) -> None:
        from app.adapters.outbound.llm.llm_client import call_llm
        from app.domain.entities import XmlDiagram

        success = XmlDiagram(xml="<mxfile></mxfile>", assistant_message="ok", suggestions=[])
        llm, structured = self._mock_llm([
            OutputParserException("Failed to parse XmlDiagram from completion {}. Got: 1 validation error"),
            success,
        ])
        with patch("app.adapters.outbound.llm.llm_client.build_langchain_client", return_value=llm), \
             patch("app.adapters.outbound.llm.llm_client.require_api_key"), \
             patch("app.adapters.outbound.llm.llm_client.get_provider", return_value=get_provider("claude")):
            result = call_llm("system", "human", "claude", XmlDiagram, None)
        self.assertIs(result, success)
        self.assertEqual(structured.invoke.call_count, 2)

    def test_raises_when_both_attempts_fail(self) -> None:
        from app.adapters.outbound.llm.llm_client import call_llm
        from app.domain.entities import XmlDiagram

        llm, structured = self._mock_llm([
            OutputParserException("first failure"),
            OutputParserException("second failure"),
        ])
        with patch("app.adapters.outbound.llm.llm_client.build_langchain_client", return_value=llm), \
             patch("app.adapters.outbound.llm.llm_client.require_api_key"), \
             patch("app.adapters.outbound.llm.llm_client.get_provider", return_value=get_provider("claude")):
            with self.assertRaises(OutputParserException):
                call_llm("system", "human", "claude", XmlDiagram, None)
        self.assertEqual(structured.invoke.call_count, 2)


class ChatRequestAttachmentSchemaTests(unittest.TestCase):
    def test_accepts_context_text_and_attachment_documents(self) -> None:
        from app.adapters.inbound.web.schemas import AttachmentDocument, ChatMessage, ChatRequest

        req = ChatRequest(
            messages=[ChatMessage(role="user", content="hi")],
            context_text="extracted document text",
            attachment_documents=[AttachmentDocument(filename="a.png", mime="image/png", data="aGVsbG8=")],
        )
        self.assertEqual(req.context_text, "extracted document text")
        self.assertEqual(len(req.attachment_documents), 1)
        self.assertEqual(req.attachment_documents[0].mime, "image/png")

    def test_context_text_and_attachment_documents_default_to_none(self) -> None:
        from app.adapters.inbound.web.schemas import ChatMessage, ChatRequest

        req = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
        self.assertIsNone(req.context_text)
        self.assertIsNone(req.attachment_documents)

    def test_attachment_documents_capped_at_three(self) -> None:
        from pydantic import ValidationError

        from app.adapters.inbound.web.schemas import AttachmentDocument, ChatMessage, ChatRequest

        with self.assertRaises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="hi")],
                attachment_documents=[
                    AttachmentDocument(filename=f"{i}.png", mime="image/png", data="x") for i in range(4)
                ],
            )


if __name__ == "__main__":
    unittest.main()
