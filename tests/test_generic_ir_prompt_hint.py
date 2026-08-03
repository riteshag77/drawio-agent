"""Human-content builder for the generic (as-is) image-replication IR path."""

from __future__ import annotations

import unittest

from app.core.services.agent import _build_generic_ir_human_content


class GenericIrHumanContentTests(unittest.TestCase):
    def test_includes_the_request(self) -> None:
        content = _build_generic_ir_human_content(
            chat_messages=[{"role": "user", "content": "reproduce this flowchart"}],
            applied_suggestions=None,
        )
        self.assertIn("reproduce this flowchart", content)

    def test_omits_cloud_and_platform_vocabulary(self) -> None:
        """Unlike the cloud IR generators, this path has no platform/image_replicate
        hint — generic.txt alone carries the fidelity instruction."""
        content = _build_generic_ir_human_content(
            chat_messages=[{"role": "user", "content": "reproduce this diagram"}],
            applied_suggestions=None,
        )
        self.assertNotIn("Platform:", content)
        self.assertNotIn("Image / PDF replicate mode", content)

    def test_includes_prior_conversation_turns(self) -> None:
        content = _build_generic_ir_human_content(
            chat_messages=[
                {"role": "user", "content": "first turn"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second turn"},
            ],
            applied_suggestions=None,
        )
        self.assertIn("Conversation so far:", content)
        self.assertIn("first turn", content)
        self.assertIn("second turn", content)


if __name__ == "__main__":
    unittest.main()
