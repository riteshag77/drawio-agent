"""Provider registry smoke tests."""

from __future__ import annotations

import unittest

from app.llm.agent import MODEL_IDS


class ProviderTests(unittest.TestCase):
    def test_openrouter_and_opencode_are_registered(self) -> None:
        self.assertIn("openrouter", MODEL_IDS)
        self.assertIn("opencode", MODEL_IDS)
        self.assertIsInstance(MODEL_IDS["openrouter"], str)
        self.assertTrue(len(MODEL_IDS["openrouter"]) > 0)
        self.assertIsInstance(MODEL_IDS["opencode"], str)
        self.assertTrue(len(MODEL_IDS["opencode"]) > 0)

    def test_normalize_gateway_base_url_strips_trailing_slash(self) -> None:
        from app.llm.providers import normalize_gateway_base_url
        self.assertEqual(
            normalize_gateway_base_url("https://example.com/v1/", "https://example.com/v1"),
            "https://example.com/v1",
        )
        self.assertEqual(
            normalize_gateway_base_url("https://example.com/v1", "https://example.com/v1"),
            "https://example.com/v1",
        )


if __name__ == "__main__":
    unittest.main()
