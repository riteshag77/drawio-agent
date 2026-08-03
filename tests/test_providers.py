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
        for key in ("openrouter", "opencode", "kimi", "nvidia"):
            self.assertIn(key, MODEL_IDS)
            self.assertIsInstance(MODEL_IDS[key], str)
            self.assertTrue(len(MODEL_IDS[key]) > 0)

    def test_nvidia_exposes_multiple_selectable_models(self) -> None:
        spec = get_provider("nvidia")
        self.assertEqual(spec.default_model, "z-ai/glm-5.2")
        self.assertIn("z-ai/glm-5.2", spec.models)
        self.assertIn("deepseek-ai/deepseek-v4-pro", spec.models)
        self.assertIn("deepseek-ai/deepseek-v4-flash", spec.models)
        self.assertIn("minimaxai/minimax-m2.7", spec.models)
        self.assertIn("minimaxai/minimax-m3", spec.models)
        self.assertEqual(len(spec.models), 5)

    def test_model_override_selects_an_available_model(self) -> None:
        from app.adapters.outbound.llm.providers import using_model_override

        with using_model_override("minimaxai/minimax-m3"):
            spec = get_provider("nvidia")
        self.assertEqual(spec.default_model, "minimaxai/minimax-m3")

    def test_model_override_rejects_a_model_not_in_the_list(self) -> None:
        from app.adapters.outbound.llm.providers import using_model_override

        with using_model_override("not-a-real-model"):
            with self.assertRaises(ValueError):
                get_provider("nvidia")

    def test_model_override_is_ignored_for_providers_without_a_models_list(self) -> None:
        """A single-model provider (e.g. claude) has no restriction to enforce —
        any override is simply applied, matching today's default_model_env pattern."""
        from app.adapters.outbound.llm.providers import using_model_override

        with using_model_override("claude-opus-4"):
            spec = get_provider("claude")
        self.assertEqual(spec.default_model, "claude-opus-4")

    def test_model_override_resets_after_context_exit(self) -> None:
        from app.adapters.outbound.llm.providers import using_model_override

        with using_model_override("minimaxai/minimax-m3"):
            pass
        spec = get_provider("nvidia")
        self.assertEqual(spec.default_model, "z-ai/glm-5.2")

    def test_kimi_omits_temperature(self) -> None:
        from dataclasses import replace

        from app.adapters.outbound.llm.providers import build_langchain_client

        spec = get_provider("kimi")
        self.assertTrue(spec.omit_temperature)
        mock_cls = MagicMock(return_value=MagicMock())
        patched = replace(spec, client_class=mock_cls)
        with patch.dict("os.environ", {"MOONSHOT_API_KEY": "test-key"}, clear=False):
            build_langchain_client(patched, timeout=30)
        kwargs = mock_cls.call_args.kwargs
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs.get("model"), spec.default_model)
        # kimi's client_key is "openai" — max_tokens, not max_output_tokens.
        self.assertIn("max_tokens", kwargs)
        self.assertGreater(kwargs["max_tokens"], 0)

    def test_max_output_tokens_kwarg_name_per_client_library(self) -> None:
        """google clients get max_output_tokens; anthropic/openai clients get max_tokens."""
        from dataclasses import replace

        from app.adapters.outbound.llm.providers import build_langchain_client

        cases = [
            ("gemini", "max_output_tokens"),
            ("claude", "max_tokens"),
            ("openai", "max_tokens"),
        ]
        for provider_key, expected_kwarg in cases:
            with self.subTest(provider=provider_key):
                spec = get_provider(provider_key)
                mock_cls = MagicMock(return_value=MagicMock())
                patched = replace(spec, client_class=mock_cls)
                with patch.dict("os.environ", {spec.api_key_env: "test-key"}, clear=False):
                    build_langchain_client(patched, timeout=30)
                kwargs = mock_cls.call_args.kwargs
                self.assertIn(expected_kwarg, kwargs)
                self.assertGreater(kwargs[expected_kwarg], 0)

    def test_max_output_tokens_override_from_providers_json(self) -> None:
        from dataclasses import replace

        from app.adapters.outbound.llm.providers import build_langchain_client

        spec = get_provider("claude")
        mock_cls = MagicMock(return_value=MagicMock())
        patched = replace(spec, client_class=mock_cls, max_output_tokens=4096)
        with patch.dict("os.environ", {spec.api_key_env: "test-key"}, clear=False):
            build_langchain_client(patched, timeout=30)
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 4096)

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
    """call_llm() retries exactly once on a parsing failure, then propagates if still failing.

    With with_structured_output(..., include_raw=True) (needed to surface
    finish_reason/response_metadata — see llm_client.call_llm), invoke() returns
    a {"raw": AIMessage, "parsed": T | None, "parsing_error": Exception | None}
    dict rather than raising directly on a parsing failure, so the mocked
    side effects here return that shape instead of raising/returning bare values.
    """

    @staticmethod
    def _raw_message():
        msg = MagicMock()
        msg.response_metadata = {"finish_reason": "STOP"}
        return msg

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
            {"raw": self._raw_message(), "parsed": None, "parsing_error": ValueError("1 validation error")},
            {"raw": self._raw_message(), "parsed": success, "parsing_error": None},
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
            {"raw": self._raw_message(), "parsed": None, "parsing_error": ValueError("first failure")},
            {"raw": self._raw_message(), "parsed": None, "parsing_error": ValueError("second failure")},
        ])
        with patch("app.adapters.outbound.llm.llm_client.build_langchain_client", return_value=llm), \
             patch("app.adapters.outbound.llm.llm_client.require_api_key"), \
             patch("app.adapters.outbound.llm.llm_client.get_provider", return_value=get_provider("claude")):
            with self.assertRaises(OutputParserException):
                call_llm("system", "human", "claude", XmlDiagram, None)
        self.assertEqual(structured.invoke.call_count, 2)

    def test_finish_reason_max_tokens_is_logged_as_warning(self) -> None:
        """A non-STOP finish_reason (e.g. MAX_TOKENS) must be surfaced in the log,
        not silently swallowed — this is what makes a future truncation diagnosable
        instead of guessed."""
        from app.adapters.outbound.llm.llm_client import call_llm
        from app.domain.entities import XmlDiagram
        from app.core.logging import StepLogger

        success = XmlDiagram(xml="<mxfile></mxfile>", assistant_message="ok", suggestions=[])
        truncated_msg = MagicMock()
        truncated_msg.response_metadata = {"finish_reason": "MAX_TOKENS"}
        llm, structured = self._mock_llm([
            {"raw": truncated_msg, "parsed": success, "parsing_error": None},
        ])
        logger = StepLogger()
        with patch("app.adapters.outbound.llm.llm_client.build_langchain_client", return_value=llm), \
             patch("app.adapters.outbound.llm.llm_client.require_api_key"), \
             patch("app.adapters.outbound.llm.llm_client.get_provider", return_value=get_provider("claude")):
            call_llm("system", "human", "claude", XmlDiagram, logger)
        warnings = [e for e in logger.entries if e.level == "warning" and "finish_reason" in e.message]
        self.assertEqual(len(warnings), 1)
        self.assertIn("MAX_TOKENS", warnings[0].message)


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


class LlmPortInjectionTests(unittest.TestCase):
    """Verify that a fake LlmPort can be injected into generate_xml_diagram (3D path)."""

    def test_fake_llm_is_used_when_injected(self) -> None:
        from app.core.services.agent import generate_xml_diagram
        from app.domain.entities import XmlDiagram

        fake_xml = "<mxfile><diagram><mxGraphModel><root><mxCell id='0'/></root></mxGraphModel></diagram></mxfile>"
        fake_result = XmlDiagram(xml=fake_xml, assistant_message="ok", suggestions=[])
        calls: list[tuple] = []

        def fake_llm(system_prompt, human_content, provider, output_type, logger, attachments):
            calls.append((provider, output_type))
            return fake_result

        result = generate_xml_diagram(
            "draw a VPC",
            provider="claude",
            platform="aws",
            three_d=True,
            llm=fake_llm,
        )
        self.assertEqual(result.xml, fake_xml)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "claude")


class CurrentXmlPromptAssemblyTests(unittest.TestCase):
    """Edit mode: surgical ops or NonSurgicalEditError (IR regenerate upstream)."""

    def test_non_surgical_edit_raises(self) -> None:
        from app.core.services.agent import NonSurgicalEditError, generate_xml_diagram
        from app.domain.entities import DiagramEditPlan

        def fake_llm(system_prompt, human_content, provider, output_type, logger, attachments):
            if output_type is DiagramEditPlan:
                return DiagramEditPlan(applicable=False)
            raise AssertionError("should not call XML generation for non-surgical edit")

        prior_xml = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="overview_panel" value="Old"/>'
            "</root></mxGraphModel></diagram></mxfile>"
            + ("<!-- pad -->" * 40)
        )
        with self.assertRaises(NonSurgicalEditError):
            generate_xml_diagram(
                "redesign this as a multi-region active-active architecture",
                provider="claude",
                platform="aws",
                current_xml=prior_xml,
                llm=fake_llm,
            )

    def test_fresh_non_3d_raises(self) -> None:
        from app.core.services.agent import generate_xml_diagram

        with self.assertRaises(ValueError) as ctx:
            generate_xml_diagram("draw a VPC", provider="claude", platform="aws", llm=lambda *a: None)
        self.assertIn("three_d=True", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
