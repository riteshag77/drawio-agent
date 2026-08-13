"""``generate_mermaid_diagram`` — LLM injection (D3) and the node-cap warning
(C2), both from docs/PROMPT_BATTLE_TEST.md.
"""

from __future__ import annotations

import unittest

from app.core.logging import StepLogger
from app.core.services.agent import _MERMAID_SOFT_NODE_CAP, generate_mermaid_diagram
from app.domain.entities.mermaid import MermaidDiagram


def _fake_llm(mermaid: str):
    def _call(*_args, **_kwargs):
        return MermaidDiagram(mermaid=mermaid, assistant_message="ok")
    return _call


class MermaidLlmInjectionTests(unittest.TestCase):
    def test_injected_llm_is_used_instead_of_the_real_port(self) -> None:
        """Regression: this path used to hardcode get_llm() directly, unlike
        every other generator in this module, which made it un-fakeable in
        isolation — a test could not exercise it without a real provider."""
        calls = []

        def fake(*args, **_kwargs):
            calls.append(args)
            return MermaidDiagram(mermaid="flowchart LR\nA-->B", assistant_message="ok")

        result = generate_mermaid_diagram("draw something", llm=fake)
        self.assertEqual(len(calls), 1, "the injected llm should be called exactly once")
        self.assertEqual(result.mermaid, "flowchart LR\nA-->B")

    def test_default_llm_is_used_when_none_injected(self) -> None:
        """The default (no llm= passed) must still resolve to a real port —
        this only proves the parameter is optional and keyword-only, not that
        it silently no-ops."""
        import inspect
        sig = inspect.signature(generate_mermaid_diagram)
        self.assertIn("llm", sig.parameters)
        self.assertTrue(sig.parameters["llm"].kind == inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(sig.parameters["llm"].default)


class MermaidNodeCapWarningTests(unittest.TestCase):
    def test_warns_when_over_the_soft_cap(self) -> None:
        big = "flowchart LR\n" + "\n".join(
            f"N{i}[Node {i}] --> N{i + 1}[Node {i + 1}]"
            for i in range(_MERMAID_SOFT_NODE_CAP + 3)
        )
        logger = StepLogger()
        generate_mermaid_diagram("draw a big pipeline", llm=_fake_llm(big), logger=logger)
        warnings = [e for e in logger.entries if e.level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("soft_cap", warnings[0].details)
        self.assertGreater(warnings[0].details["estimated_node_count"], _MERMAID_SOFT_NODE_CAP)

    def test_no_warning_under_the_cap(self) -> None:
        small = "flowchart LR\nA[Start] --> B[End]"
        logger = StepLogger()
        generate_mermaid_diagram("draw something small", llm=_fake_llm(small), logger=logger)
        warnings = [e for e in logger.entries if e.level == "warning"]
        self.assertEqual(warnings, [])

    def test_no_logger_means_no_crash(self) -> None:
        big = "flowchart LR\n" + "\n".join(
            f"N{i}[Node {i}] --> N{i + 1}[Node {i + 1}]"
            for i in range(_MERMAID_SOFT_NODE_CAP + 3)
        )
        # logger=None is the documented default; the warning path must not
        # assume a logger exists.
        result = generate_mermaid_diagram("draw a big pipeline", llm=_fake_llm(big))
        self.assertIn("flowchart", result.mermaid)
