"""Scoring a non-cloud diagram.

Every rule checked here is one `config/xml_gen/ir/simple.txt` already states.
That constraint is the design: a conformance score is only worth iterating
against if a higher number means the diagram is closer to what was asked for,
and a score built from taste would send the model chasing preferences nobody
agreed to.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.simple_conformance import score_simple_ir
from app.domain.ir.simple import SimpleDiagramIR


def _flow(**overrides) -> SimpleDiagramIR:
    payload = {
        "kind": "flow", "title": "Claim Flow",
        "nodes": [
            {"id": "a", "shape": "rounded_rectangle", "label": "Start"},
            {"id": "d", "shape": "diamond", "label": "Covered?"},
            {"id": "y", "shape": "rectangle", "label": "Pay claim"},
            {"id": "n", "shape": "rectangle", "label": "Decline"},
        ],
        "edges": [
            {"source": "a", "target": "d"},
            {"source": "d", "target": "y", "label": "Yes"},
            {"source": "d", "target": "n", "label": "No"},
        ],
    }
    payload.update(overrides)
    return SimpleDiagramIR(**payload)


def _codes(ir: SimpleDiagramIR) -> list[str]:
    return [v.code for v in score_simple_ir(ir).violations]


class CleanDiagramTests(unittest.TestCase):
    def test_a_well_formed_flow_scores_full_marks(self) -> None:
        scored = score_simple_ir(_flow())
        self.assertEqual(scored.score, 100)
        self.assertTrue(scored.passes)

    def test_scoring_is_deterministic(self) -> None:
        """Iterating against a score that moves on its own would be noise."""
        self.assertEqual(score_simple_ir(_flow()).score, score_simple_ir(_flow()).score)


class FlowViolationTests(unittest.TestCase):
    def test_an_unlabelled_branch_is_caught(self) -> None:
        """The pack: 'an unlabelled branch is unreadable'."""
        ir = _flow(edges=[
            {"source": "a", "target": "d"},
            {"source": "d", "target": "y"},
            {"source": "d", "target": "n", "label": "No"},
        ])
        self.assertIn("unlabelled_branch", _codes(ir))

    def test_a_decision_with_one_way_out_is_not_a_decision(self) -> None:
        ir = _flow(edges=[
            {"source": "a", "target": "d"},
            {"source": "d", "target": "y", "label": "Yes"},
            {"source": "y", "target": "n"},
        ])
        self.assertIn("decision_without_branches", _codes(ir))

    def test_a_stranded_node_is_caught(self) -> None:
        ir = _flow(nodes=[
            *_flow().nodes,
            {"id": "orphan", "shape": "rectangle", "label": "Audit"},
        ])
        self.assertIn("orphan_node", _codes(ir))

    def test_a_single_node_diagram_is_not_called_stranded(self) -> None:
        """Only call a node stranded when there were edges it could have
        joined — a one-node diagram has nothing to connect to."""
        ir = SimpleDiagramIR(
            kind="flow", title="One",
            nodes=[{"id": "a", "shape": "rectangle", "label": "Only"}], edges=[],
        )
        self.assertNotIn("orphan_node", _codes(ir))

    def test_a_sentence_is_not_a_label(self) -> None:
        ir = _flow(nodes=[
            {"id": "a", "shape": "rectangle",
             "label": "Pay the claim out to the customer promptly"},
            {"id": "d", "shape": "rectangle", "label": "Next"},
        ], edges=[{"source": "a", "target": "d"}])
        self.assertIn("long_label", _codes(ir))


class SequenceViolationTests(unittest.TestCase):
    def _sequence(self, **overrides) -> SimpleDiagramIR:
        payload = {
            "kind": "sequence", "title": "Login",
            "participants": [{"id": "u", "label": "User"}, {"id": "api", "label": "API"}],
            "messages": [{"source": "u", "target": "api", "label": "POST /login"}],
        }
        payload.update(overrides)
        return SimpleDiagramIR(**payload)

    def test_a_participant_nobody_talks_to_is_caught(self) -> None:
        ir = self._sequence(participants=[
            {"id": "u", "label": "User"}, {"id": "api", "label": "API"},
            {"id": "ghost", "label": "Audit Log"},
        ])
        self.assertIn("orphan_participant", _codes(ir))

    def test_an_unlabelled_message_is_caught(self) -> None:
        """The pack: 'label every message with what is actually sent'."""
        ir = self._sequence(messages=[{"source": "u", "target": "api", "label": ""}])
        self.assertIn("unlabelled_message", _codes(ir))

    def test_an_alt_with_one_arm_is_caught(self) -> None:
        """An alt with a single branch tells a reader there is a choice where
        there is none — it is an opt, mislabelled."""
        ir = self._sequence(messages=[
            {"source": "u", "target": "api", "label": "go",
             "fragment": "outcome", "fragment_kind": "alt", "branch": "only"},
        ])
        self.assertIn("single_arm_alt", _codes(ir))

    def test_a_two_armed_alt_is_clean(self) -> None:
        ir = self._sequence(messages=[
            {"source": "u", "target": "api", "label": "a",
             "fragment": "outcome", "fragment_kind": "alt", "branch": "high"},
            {"source": "api", "target": "u", "label": "b", "kind": "reply",
             "fragment": "outcome", "fragment_kind": "alt", "branch": "low"},
        ])
        self.assertNotIn("single_arm_alt", _codes(ir))


class ChartViolationTests(unittest.TestCase):
    def test_a_one_category_chart_compares_nothing(self) -> None:
        ir = SimpleDiagramIR(
            kind="bar", title="Sales", categories=["Q1"],
            series=[{"label": "2026", "values": [10.0]}],
        )
        self.assertIn("single_category", _codes(ir))

    def test_a_gantt_is_not_judged_on_categories(self) -> None:
        """Gantt measures tasks over time — its categories are optional column
        headings, not the thing being compared."""
        ir = SimpleDiagramIR(
            kind="gantt", title="Plan", categories=[],
            tasks=[{"label": "Build", "start": 0, "duration": 2}],
        )
        self.assertNotIn("single_category", _codes(ir))


if __name__ == "__main__":
    unittest.main()
