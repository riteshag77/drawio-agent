"""Structured output method resolution and message text extraction."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.llm.agent import _message_text, _resolve_structured_output_method


class StructuredOutputMethodTests(unittest.TestCase):
    def test_opencode_always_uses_text_json(self) -> None:
        with patch.dict(os.environ, {"STRUCTURED_OUTPUT_METHOD": "function_calling"}, clear=False):
            self.assertEqual(_resolve_structured_output_method("opencode"), "none")

    def test_openrouter_defaults_to_text_json(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_STRUCTURED_OUTPUT"}
        with patch.dict(os.environ, env, clear=True):
            os.environ["STRUCTURED_OUTPUT_METHOD"] = "function_calling"
            self.assertEqual(_resolve_structured_output_method("openrouter"), "none")

    def test_openrouter_honors_override(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STRUCTURED_OUTPUT_METHOD": "function_calling",
                "OPENROUTER_STRUCTURED_OUTPUT": "function_calling",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_structured_output_method("openrouter"), "function_calling")

    def test_openai_keeps_function_calling_default(self) -> None:
        with patch.dict(os.environ, {"STRUCTURED_OUTPUT_METHOD": "function_calling"}, clear=False):
            self.assertEqual(_resolve_structured_output_method("openai"), "function_calling")


class MessageTextTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(_message_text("hello"), "hello")

    def test_object_with_content(self) -> None:
        class FakeMsg:
            content = '{"ok": true}'

        self.assertEqual(_message_text(FakeMsg()), '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
