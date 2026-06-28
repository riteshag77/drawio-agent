"""Provider registry smoke tests."""

from __future__ import annotations

import unittest

from app.llm.agent import MODEL_IDS, _normalize_opencode_model


class ProviderTests(unittest.TestCase):
    def test_openrouter_and_opencode_are_registered(self) -> None:
        self.assertIn("openrouter", MODEL_IDS)
        self.assertIn("opencode", MODEL_IDS)
        self.assertIsInstance(MODEL_IDS["openrouter"], str)
        self.assertTrue(len(MODEL_IDS["openrouter"]) > 0)
        self.assertEqual(MODEL_IDS["opencode"], "big-pickle")

    def test_normalize_opencode_model_strips_prefix(self) -> None:
        self.assertEqual(_normalize_opencode_model("opencode/big-pickle"), "big-pickle")
        self.assertEqual(_normalize_opencode_model("big-pickle"), "big-pickle")
        self.assertEqual(_normalize_opencode_model(""), "big-pickle")
        self.assertEqual(_normalize_opencode_model("  opencode/glm-5.2  "), "glm-5.2")


if __name__ == "__main__":
    unittest.main()
