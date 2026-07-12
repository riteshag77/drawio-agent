"""Structured output registry and message text extraction."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.services.agent import _message_text
from app.adapters.outbound.llm.providers import get_provider
from app.adapters.outbound.llm.response_parse import invoke_gateway_json, parse_http_json
from app.domain.entities import ArchitectureDiagram


class StructuredOutputRegistryTests(unittest.TestCase):
    def test_opencode_uses_text_json(self) -> None:
        self.assertEqual(get_provider("opencode").structured_output, "none")

    def test_openrouter_uses_text_json(self) -> None:
        self.assertEqual(get_provider("openrouter").structured_output, "none")

    def test_bazaarlink_uses_text_json(self) -> None:
        self.assertEqual(get_provider("bazaarlink").structured_output, "none")
        self.assertEqual(get_provider("bazaarlink").base_url_default, "https://bazaarlink.ai/api/v1")

    def test_openai_uses_function_calling(self) -> None:
        self.assertEqual(get_provider("openai").structured_output, "function_calling")

    def test_gemini_uses_json_mode(self) -> None:
        self.assertEqual(get_provider("gemini").structured_output, "json_mode")


class GatewayInvokeTests(unittest.TestCase):
    @patch("app.adapters.outbound.llm.response_parse.httpx.post")
    def test_invoke_gateway_json_parses_openai_style_response(self, mock_post: MagicMock) -> None:
        body = '{"choices":[{"message":{"content":"{\\"title\\":\\"t\\",\\"nodes\\":[],\\"edges\\":[],\\"flow_steps\\":[],\\"suggestions\\":[]}"}}]}'
        mock_post.return_value.raise_for_status = MagicMock()
        mock_post.return_value.status_code = 200
        mock_post.return_value.text = body

        result = invoke_gateway_json(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="demo-model",
            messages=[SystemMessage(content="sys"), HumanMessage(content="user")],
            output_type=ArchitectureDiagram,
            timeout=30,
        )

        self.assertEqual(result.title, "t")
        request_kwargs = mock_post.call_args.kwargs
        self.assertEqual(request_kwargs["json"]["model"], "demo-model")
        self.assertEqual(request_kwargs["json"]["messages"][0]["role"], "system")

    def test_parse_http_json_rejects_plain_text_body(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.text = "Not Found"

        with self.assertRaisesRegex(ValueError, "non-JSON body"):
            parse_http_json(response)


class MessageTextTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(_message_text("hello"), "hello")

    def test_object_with_content(self) -> None:
        class FakeMsg:
            content = '{"ok": true}'

        self.assertEqual(_message_text(FakeMsg()), '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
