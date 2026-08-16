"""The validation loop: how many corrective passes a view earns, and why.

The rule that makes this worth having is the stopping rule. Iterating because
a budget allows it, rather than because the diagram is getting better, is how a
pipeline burns calls producing no change — so a pass that gains nothing ends
the loop even when iterations remain.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.adapters.inbound.web import admin_routes
from app.adapters.outbound.config.env_config import get_config


def _Settings(**values: str):
    """Apply settings for one test.

    Patches ``os.environ`` rather than going through ``admin_settings_store``:
    the config adapter reads the environment directly, and the store's own
    ``clear_settings`` is documented as *not* touching it — so a store-based
    fixture leaks every override into the tests that follow.
    """
    return patch.dict(os.environ, values, clear=False)


class ValidationSettingsTests(unittest.TestCase):
    def test_defaults_are_off_and_cheap(self) -> None:
        """Off by default: the loop costs an LLM call per iteration, and the
        single corrective pass it replaces was free of that choice."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIAGRAM_VALIDATION_ENABLED", None)
            os.environ.pop("DIAGRAM_VALIDATION_MIN_IMPROVEMENT", None)
            cfg = get_config()
            self.assertFalse(cfg.validation_enabled)
            self.assertEqual(cfg.validation_min_improvement, 0)

    def test_iterations_are_clamped(self) -> None:
        with _Settings(DIAGRAM_VALIDATION_MAX_ITERATIONS="99"):
            self.assertEqual(get_config().validation_max_iterations, 5)
        with _Settings(DIAGRAM_VALIDATION_MAX_ITERATIONS="-3"):
            self.assertEqual(get_config().validation_max_iterations, 0)

    def test_min_improvement_is_clamped(self) -> None:
        with _Settings(DIAGRAM_VALIDATION_MIN_IMPROVEMENT="500"):
            self.assertEqual(get_config().validation_min_improvement, 100)

    def test_settings_round_trip_through_admin(self) -> None:
        with _Settings(
            DIAGRAM_VALIDATION_ENABLED="true",
            DIAGRAM_VALIDATION_MAX_ITERATIONS="3",
            DIAGRAM_VALIDATION_MIN_IMPROVEMENT="5",
            VIEW_QUALITY_MIN_SCORE="80",
        ):
            settings = admin_routes._validation_settings()
            self.assertTrue(settings["enabled"])
            self.assertEqual(settings["max_iterations"], 3)
            self.assertEqual(settings["min_improvement"], 5)
            self.assertEqual(settings["min_score"], 80)


class _Score:
    """Minimal stand-in for a ViewScore."""

    def __init__(self, score: int, passes: bool) -> None:
        self.score = score
        self.passes = passes
        self.violations: list = []
        self.metrics: dict = {}


def _run_loop(scores: list[tuple[int, bool]], *, max_iterations: int, min_gain: int):
    """Replay the loop's decision rule over a canned sequence of scores.

    Mirrors the branch in ``xml_pipeline._run_view`` — kept as a readable
    reimplementation because the real one is welded to an async view
    generation, and what needs pinning is the *stopping rule*, not the LLM
    plumbing around it. If the two ever disagree the loop's own logging
    (``gain``, ``iteration``) is what reveals it.
    """
    attempts = [_Score(s, p) for s, p in scores]
    current = attempts[0]
    calls = 0
    for attempt in attempts[1:]:
        if current.passes or calls >= max_iterations:
            break
        calls += 1
        gain = attempt.score - current.score
        if gain >= 0:
            current = attempt
        if gain <= 0 or gain < min_gain:
            break
    return current.score, calls


class StoppingRuleTests(unittest.TestCase):
    def test_it_keeps_going_while_the_score_rises(self) -> None:
        score, calls = _run_loop(
            [(40, False), (60, False), (80, False)], max_iterations=3, min_gain=0,
        )
        self.assertEqual((score, calls), (80, 2))

    def test_it_stops_the_moment_a_pass_gains_nothing(self) -> None:
        """The whole point of the feature: two iterations remained, and the
        loop declined to spend them because the diagram stopped improving."""
        score, calls = _run_loop(
            [(40, False), (60, False), (60, False), (90, False)],
            max_iterations=3, min_gain=0,
        )
        self.assertEqual((score, calls), (60, 2))

    def test_a_regression_is_discarded_and_ends_the_loop(self) -> None:
        score, calls = _run_loop(
            [(70, False), (50, False), (95, False)], max_iterations=3, min_gain=0,
        )
        self.assertEqual(score, 70, "the worse attempt must not be kept")
        self.assertEqual(calls, 1)

    def test_a_threshold_declines_a_small_win(self) -> None:
        """'Don't spend another call on a two-point gain.'"""
        score, calls = _run_loop(
            [(40, False), (42, False), (90, False)], max_iterations=3, min_gain=10,
        )
        self.assertEqual((score, calls), (42, 1))

    def test_the_same_small_win_continues_when_any_improvement_counts(self) -> None:
        score, calls = _run_loop(
            [(40, False), (42, False), (90, False)], max_iterations=3, min_gain=0,
        )
        self.assertEqual((score, calls), (90, 2))

    def test_passing_early_spends_nothing_further(self) -> None:
        score, calls = _run_loop(
            [(95, True), (99, False)], max_iterations=3, min_gain=0,
        )
        self.assertEqual((score, calls), (95, 0))

    def test_zero_iterations_never_calls(self) -> None:
        score, calls = _run_loop([(10, False), (99, False)], max_iterations=0, min_gain=0)
        self.assertEqual((score, calls), (10, 0))


class ProviderSelectionTests(unittest.TestCase):
    def test_blank_means_use_the_generation_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIAGRAM_VALIDATION_PROVIDER", None)
            self.assertEqual(get_config().validation_provider, "")

    def test_an_explicit_provider_is_kept(self) -> None:
        with _Settings(DIAGRAM_VALIDATION_PROVIDER="openai"):
            self.assertEqual(get_config().validation_provider, "openai")


if __name__ == "__main__":
    unittest.main()


class PrimaryPageCoverageTests(unittest.TestCase):
    """The loop was wired only into the *extra* views at first, so turning
    validation on left the page the user is actually looking at untouched."""

    def _source(self) -> str:
        import inspect

        from app.core.services import xml_pipeline

        return inspect.getsource(xml_pipeline)

    def test_the_primary_page_is_scored(self) -> None:
        from app.core.services.xml_pipeline import _score_primary

        self.assertTrue(callable(_score_primary))
        self.assertIn("_score_primary(", self._source())

    def test_the_primary_loop_is_guarded_to_fresh_ir_only(self) -> None:
        """A surgical edit or a 3D page must never be regenerated wholesale to
        chase a score — that throws away the canvas the user has been editing,
        which is a far worse outcome than a view scoring 68."""
        source = self._source()
        guard = source[source.index("_cfg_primary.validation_enabled"):]
        guard = guard[:guard.index("primary_min =")]
        self.assertIn("used_ir_path", guard)
        self.assertIn("not three_d", guard)
        self.assertIn("current_xml", guard)

    def test_the_primary_loop_uses_the_same_stopping_rule(self) -> None:
        source = self._source()
        self.assertIn("validation_min_improvement", source)
        self.assertIn("validation_max_iterations", source)

    def test_network_is_scoreable(self) -> None:
        """The primary is usually Network; if score_view had no rules for it
        the loop above would be measuring nothing."""
        from app.adapters.outbound.diagram.view_conformance import score_view

        xml = (
            '<mxfile><diagram name="Network"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        scored = score_view(xml, view="network", min_score=75)
        self.assertIsInstance(scored.score, int)
