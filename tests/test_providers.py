"""Provider registry smoke tests."""

from __future__ import annotations

import unittest

from app.llm.agent import MODEL_IDS


class ProviderTests(unittest.TestCase):
    def test_gateway_providers_are_registered(self) -> None:
        for key in ("openrouter", "opencode", "bazaarlink"):
            self.assertIn(key, MODEL_IDS)
            self.assertIsInstance(MODEL_IDS[key], str)
            self.assertTrue(len(MODEL_IDS[key]) > 0)

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
