"""Cursor SDK diagnostics and error formatting tests."""

from __future__ import annotations

import os
import unittest

from app.core.config import BASE_DIR
from app.llm.agent import _format_cursor_error
from app.llm.prompts.system import get_cursor_system_prompt, get_system_prompt


class CursorErrorFormattingTests(unittest.TestCase):
    def test_format_cursor_error_includes_metadata(self) -> None:
        class FakeError:
            message = "internal error"
            code = "internal"
            status = 500
            request_id = "req-123"
            details = [{"sdkErrorCode": "SDK_ERROR_CODE_INTERNAL"}]
            is_retryable = False
            cause = None

        formatted = _format_cursor_error(FakeError())
        self.assertIn("internal error", formatted)
        self.assertIn("request_id=req-123", formatted)
        self.assertIn("details=", formatted)


class CursorPromptTests(unittest.TestCase):
    def test_cursor_prompt_is_smaller_than_full_schema_prompt(self) -> None:
        full = get_system_prompt(provider="openai", include_schema=True, patterns_block="")
        slim = get_cursor_system_prompt()
        self.assertLess(len(slim), len(full))
        self.assertIn("aws_vpc", slim)
        self.assertNotIn("SECTION 3", slim)


@unittest.skipUnless(os.getenv("CURSOR_API_KEY"), "CURSOR_API_KEY not set")
class CursorSdkIntegrationTests(unittest.TestCase):
    def test_models_list(self) -> None:
        from app.core.cursor_bridge import apply_windows_bridge_patch
        from cursor_sdk import Cursor

        apply_windows_bridge_patch()
        models = Cursor.models.list()
        self.assertTrue(len(models) > 0)

    def test_minimal_local_prompt(self) -> None:
        from app.core.cursor_bridge import apply_windows_bridge_patch
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

        apply_windows_bridge_patch()
        model = os.getenv("CURSOR_MODEL", "composer-2.5")
        options = AgentOptions(
            api_key=os.environ["CURSOR_API_KEY"],
            model=model,
            name="drawio-agent-test",
            local=LocalAgentOptions(cwd=str(BASE_DIR), setting_sources=[]),
        )
        result = Agent.prompt('Return {"ok": true} as JSON only.', options)
        self.assertEqual(result.status, "finished")
        self.assertIn("ok", str(result.result or ""))


if __name__ == "__main__":
    unittest.main()
