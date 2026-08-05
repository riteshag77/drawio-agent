"""Regression: a follow-up-suggestions LLM call that fails to deliver real
content must surface as a visible degraded-feature signal, not disappear
silently — whether it fails via a raised exception, OR "succeeds" but
returns an empty list (ignoring the "never leave suggestions empty"
instruction). The user-visible symptom (nothing in the suggestions panel)
is identical either way, so both failure modes get one retry, and both are
flagged degraded if they're still empty after that retry.

Before this fix, ``_generate_followup_suggestions`` only retried/flagged a
raised exception — a technically-successful call returning ``[]`` logged a
plain warning with no ``details`` dict, invisible to
``xml_pipeline._collect_degraded_features`` (which only picks up entries
where ``details.get("degraded")`` is truthy), so ``suggestions: []`` with
``status: "success"`` gave zero signal anywhere in logs or UI.
"""

from __future__ import annotations

import unittest

from app.core.logging import StepLogger
from app.core.services.agent import _generate_followup_suggestions
from app.domain.entities.xml_diagram import DiagramSuggestion


def _real_suggestions() -> object:
    return type("Parsed", (), {
        "suggestions": [DiagramSuggestion(text="Add a read replica", category="network")],
    })()


def _empty_suggestions() -> object:
    return type("Parsed", (), {"suggestions": []})()


class FollowupSuggestionsDegradedTests(unittest.TestCase):
    def test_persistent_failure_logs_degraded_details(self) -> None:
        logger = StepLogger()

        def always_fails(*_args, **_kwargs):
            raise RuntimeError("provider timeout")

        result = _generate_followup_suggestions(
            ir=object(), view="network", user_request="add a database",
            provider_key="claude", llm=always_fails, logger=logger, applied_suggestions=None,
        )

        self.assertEqual(result, [])
        degraded = [e for e in logger.entries if (e.details or {}).get("degraded")]
        self.assertEqual(len(degraded), 1, "exactly one degraded entry expected")
        self.assertEqual(degraded[0].details["feature"], "suggestions")
        self.assertIn("provider timeout", degraded[0].details["reason"])

    def test_persistent_empty_success_also_logs_degraded_details(self) -> None:
        """The gap this fix closes: no exception anywhere, every attempt
        "succeeds," but every attempt returns zero suggestions — still
        must be visible, not a silent empty panel."""
        logger = StepLogger()
        calls: list[int] = []

        def always_empty(*_args, **_kwargs):
            calls.append(1)
            return _empty_suggestions()

        result = _generate_followup_suggestions(
            ir=object(), view="network", user_request="add a database",
            provider_key="claude", llm=always_empty, logger=logger, applied_suggestions=None,
        )

        self.assertEqual(len(calls), 2, "an empty-but-successful attempt must still retry once")
        self.assertEqual(result, [])
        degraded = [e for e in logger.entries if (e.details or {}).get("degraded")]
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0].details["feature"], "suggestions")

    def test_retries_after_exception_and_succeeds_with_real_content(self) -> None:
        logger = StepLogger()
        calls: list[int] = []

        def fails_once_then_real(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient blip")
            return _real_suggestions()

        result = _generate_followup_suggestions(
            ir=object(), view="network", user_request="add a database",
            provider_key="claude", llm=fails_once_then_real, logger=logger, applied_suggestions=None,
        )

        self.assertEqual(len(calls), 2, "must retry once after the first failure")
        self.assertEqual(len(result), 1)
        degraded = [e for e in logger.entries if (e.details or {}).get("degraded")]
        self.assertEqual(degraded, [], "a retry that returns real content must not report degraded")

    def test_empty_first_attempt_retries_and_succeeds_with_real_content(self) -> None:
        logger = StepLogger()
        calls: list[int] = []

        def empty_once_then_real(*_args, **_kwargs):
            calls.append(1)
            return _empty_suggestions() if len(calls) == 1 else _real_suggestions()

        result = _generate_followup_suggestions(
            ir=object(), view="network", user_request="add a database",
            provider_key="claude", llm=empty_once_then_real, logger=logger, applied_suggestions=None,
        )

        self.assertEqual(len(calls), 2, "an empty first attempt must trigger a retry")
        self.assertEqual(len(result), 1)
        degraded = [e for e in logger.entries if (e.details or {}).get("degraded")]
        self.assertEqual(degraded, [], "a retry that returns real content must not report degraded")

    def test_success_with_real_content_on_first_try_does_not_retry(self) -> None:
        logger = StepLogger()
        calls: list[int] = []

        def real_content(*_args, **_kwargs):
            calls.append(1)
            return _real_suggestions()

        result = _generate_followup_suggestions(
            ir=object(), view="network", user_request="add a database",
            provider_key="claude", llm=real_content, logger=logger, applied_suggestions=None,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
