"""Provider registry smoke tests."""

from __future__ import annotations

import json
import unittest

from app.llm.agent import MODEL_IDS, _extract_opencode_response


class ProviderTests(unittest.TestCase):
    def test_openrouter_and_opencode_are_registered(self) -> None:
        self.assertIn("openrouter", MODEL_IDS)
        self.assertIn("opencode", MODEL_IDS)
        self.assertIsInstance(MODEL_IDS["openrouter"], str)
        self.assertTrue(len(MODEL_IDS["openrouter"]) > 0)

    def test_opencode_json_wrapper_response_is_unwrapped(self) -> None:
        payload = {"response": json.dumps({"nodes": [], "edges": [], "suggestions": []})}
        self.assertEqual(
            _extract_opencode_response(json.dumps(payload)),
            '{"nodes": [], "edges": [], "suggestions": []}',
        )


if __name__ == "__main__":
    unittest.main()
