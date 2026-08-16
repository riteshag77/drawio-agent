"""The architecture interview: which questions get asked, and how many rounds.

The interview used to be five hardcoded strings, identical for every brief —
it asked a static-site brief about trust boundaries and a PCI brief nothing
about cardholder data. It now plans questions from the brief, and may ask one
follow-up when an answer opens something the first round could not have known
to ask.
"""

from __future__ import annotations

import unittest
import unittest.mock

from app.adapters.inbound.web.routes import _MAX_INTERVIEW_ROUNDS, _interview_rounds
from app.adapters.outbound.diagram.architecture_docs import (
    INTERVIEW_QUESTIONS,
    MAX_INTERVIEW_QUESTIONS,
    InterviewPlan,
    InterviewQuestion,
    load_interview_prompt_pack,
)
from app.core.services import xml_pipeline
from app.core.logging import StepLogger


def _plan(**kwargs) -> list[dict[str, str]]:
    return xml_pipeline.plan_interview(
        human_prompt=kwargs.pop("brief", "a payments platform"),
        platform_key="aws",
        views=["network"],
        provider_key="claude",
        logger=kwargs.pop("logger", StepLogger()),
        **kwargs,
    )


class _StubLlm:
    """Stands in for the provider call, the way the other pipeline tests do."""

    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, system, human, provider, schema, logger, attachments):
        self.calls.append((system, human, schema))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class QuestionPlanningTests(unittest.TestCase):
    def _with_llm(self, result):
        return unittest.mock.patch.object(xml_pipeline, "get_llm", lambda: _StubLlm(result))

    def test_questions_come_from_the_plan(self) -> None:
        plan = InterviewPlan(questions=[
            InterviewQuestion(id="pci_scope", prompt="Is PCI in scope? I'll assume no.",
                              assumption="PCI out of scope"),
        ])
        with self._with_llm(plan):
            questions = _plan()
        self.assertEqual([q["id"] for q in questions], ["pci_scope"])
        self.assertIn("PCI", questions[0]["prompt"])

    def test_an_empty_plan_is_a_valid_answer_not_a_fallback(self) -> None:
        """A brief that already says enough should be drawn, not interrogated."""
        with self._with_llm(InterviewPlan(questions=[])):
            self.assertEqual(_plan(), [])

    def test_questions_are_capped(self) -> None:
        """Past five, people answer 'n/a' to everything, which is worse than
        not having asked."""
        plan = InterviewPlan(questions=[
            InterviewQuestion(id=f"q{i}", prompt=f"Question {i}?") for i in range(12)
        ])
        with self._with_llm(plan):
            self.assertEqual(len(_plan()), MAX_INTERVIEW_QUESTIONS)

    def test_blank_prompts_are_dropped(self) -> None:
        plan = InterviewPlan(questions=[
            InterviewQuestion(id="real", prompt="Which regions?"),
            InterviewQuestion(id="blank", prompt="   "),
        ])
        with self._with_llm(plan):
            self.assertEqual([q["id"] for q in _plan()], ["real"])

    def test_a_failure_falls_back_to_the_fixed_question_set(self) -> None:
        """An interview is an enhancement; it must never be the reason a
        diagram doesn't get drawn."""
        logger = StepLogger()
        with self._with_llm(RuntimeError("provider down")):
            questions = _plan(logger=logger)
        self.assertEqual(len(questions), len(INTERVIEW_QUESTIONS))
        warnings = [e for e in logger.to_list() if e.get("level") == "warning"]
        self.assertTrue(any("Interview planning failed" in e["message"] for e in warnings))

    def test_a_failed_follow_up_round_asks_nothing_rather_than_repeating(self) -> None:
        """The fixed list was already asked in round one; asking it again is
        worse than proceeding with what we have."""
        with self._with_llm(RuntimeError("provider down")):
            self.assertEqual(_plan(prior_answers={"answers": "single region"}), [])

    def test_the_follow_up_round_is_told_it_is_one(self) -> None:
        stub = _StubLlm(InterviewPlan(questions=[]))
        with unittest.mock.patch.object(xml_pipeline, "get_llm", lambda: stub):
            _plan(prior_answers={"answers": "PCI is in scope"})
        human = stub.calls[0][1]
        self.assertIn("follow-up round", human)
        self.assertIn("PCI is in scope", human)


class RoundCountingTests(unittest.TestCase):
    """The count rides on the answers because the chat endpoint is stateless."""

    def test_no_answers_is_round_zero(self) -> None:
        self.assertEqual(_interview_rounds(None), 0)
        self.assertEqual(_interview_rounds({}), 0)

    def test_the_marker_is_read_back(self) -> None:
        self.assertEqual(_interview_rounds({"answers": "x", "_rounds": "2"}), 2)

    def test_answers_without_a_marker_count_as_one_round(self) -> None:
        """A client that predates the marker has still answered once — treating
        it as zero would ask the first round again forever."""
        self.assertEqual(_interview_rounds({"answers": "single region"}), 1)

    def test_two_rounds_is_the_ceiling(self) -> None:
        self.assertEqual(_MAX_INTERVIEW_ROUNDS, 2)
        self.assertGreaterEqual(_interview_rounds({"_rounds": "2"}), _MAX_INTERVIEW_ROUNDS)


class PackTests(unittest.TestCase):
    def test_the_pack_states_the_test_a_question_must_pass(self) -> None:
        pack = load_interview_prompt_pack()
        self.assertIn("would the **drawing change**", pack)

    def test_the_pack_requires_a_stated_default(self) -> None:
        """Defaults are what make the interview cheap to skip — without them a
        user cannot answer 'all fine' and still get a considered diagram."""
        pack = load_interview_prompt_pack()
        self.assertIn("assumption", pack.lower())
        self.assertIn("i'll assume", pack.lower())

    def test_the_pack_forbids_a_third_round(self) -> None:
        self.assertIn("never a third", load_interview_prompt_pack().lower())


if __name__ == "__main__":
    unittest.main()
