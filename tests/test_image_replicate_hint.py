"""Image-replicate hint must stay generic for Network but caveat corridor views
(Logical/Security/Data-flow) to keep their own inclusion/exclusion rules."""

from __future__ import annotations

import unittest

from app.core.services.agent import _build_ir_human_content


def _content(view: str, image_replicate: bool) -> str:
    return _build_ir_human_content(
        view=view,
        platform="aws",
        chat_messages=[{"role": "user", "content": "recreate this diagram"}],
        applied_suggestions=None,
        context_text=None,
        image_replicate=image_replicate,
    )


class ImageReplicateHintTests(unittest.TestCase):
    def test_network_gets_blanket_fidelity_hint_only(self) -> None:
        content = _content("network", True)
        self.assertIn("Image / PDF replicate mode", content)
        self.assertNotIn("deliberately exclude", content)

    def test_logical_gets_corridor_addendum(self) -> None:
        content = _content("logical", True)
        self.assertIn("Image / PDF replicate mode", content)
        self.assertIn("deliberately exclude", content)
        self.assertIn("Logical", content)

    def test_security_and_data_flow_also_get_corridor_addendum(self) -> None:
        for view in ("security", "data_flow"):
            content = _content(view, True)
            self.assertIn("deliberately exclude", content, msg=view)

    def test_no_hint_when_image_replicate_false(self) -> None:
        content = _content("logical", False)
        self.assertNotIn("Image / PDF replicate mode", content)
        self.assertNotIn("deliberately exclude", content)


if __name__ == "__main__":
    unittest.main()
