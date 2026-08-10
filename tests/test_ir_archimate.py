"""ArchimateIR validation — closed vocabulary, layer alignment, relationship
legality auto-correction, and the overview-panel derivation helpers."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.domain.ir import (
    ArchimateElement,
    ArchimateGrouping,
    ArchimateIR,
    ArchimateRelationship,
    OpenQuestion,
    is_legal_relationship,
    nearest_legal_substitute,
)


def _el(i: str, archimate_type: str, layer: str, name: str = "X", **overrides) -> ArchimateElement:
    return ArchimateElement(
        id=i, archimate_type=archimate_type, layer=layer, name=name,
        source_confidence=overrides.pop("source_confidence", "explicit"), **overrides,
    )


def _rel(i: str, rel_type: str, source: str, target: str) -> ArchimateRelationship:
    return ArchimateRelationship(id=i, type=rel_type, source=source, target=target)


def minimal_ir(**overrides) -> ArchimateIR:
    elements = overrides.pop("elements", None) or [
        _el("actor1", "business-actor", "business", "Customer"),
        _el("proc1", "business-process", "business", "Quote & Bind"),
    ]
    relationships = overrides.pop("relationships", None)
    if relationships is None:
        relationships = [_rel("r1", "assignment", "actor1", "proc1")]
    return ArchimateIR(
        viewpoint=overrides.pop("viewpoint", "layered"),
        elements=elements,
        relationships=relationships,
        **overrides,
    )


class ElementTypeVocabularyTests(unittest.TestCase):
    def test_unknown_archimate_type_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArchimateElement(
                id="x", archimate_type="business-widget", layer="business",
                name="X", source_confidence="explicit",
            )

    def test_known_types_from_every_layer_accepted(self) -> None:
        for t, layer in (
            ("business-actor", "business"),
            ("application-component", "application"),
            ("technology-node", "technology"),
        ):
            el = _el("x", t, layer)
            self.assertEqual(el.archimate_type, t)

    def test_extra_fields_rejected_loudly(self) -> None:
        with self.assertRaises(ValidationError):
            ArchimateElement(
                id="x", archimate_type="business-actor", layer="business", name="X",
                source_confidence="explicit", color="#fff",
            )


class LayerAlignmentTests(unittest.TestCase):
    """The `layer` field is notation, not judgement — the table always wins."""

    def _raw_ir(self, element: dict) -> ArchimateIR:
        return ArchimateIR.model_validate({
            "viewpoint": "layered",
            "elements": [element],
        })

    def test_wrong_layer_is_silently_overwritten_from_the_table(self) -> None:
        ir = self._raw_ir({
            "id": "n1", "archimate_type": "technology-node", "layer": "business",
            "name": "Node", "source_confidence": "explicit",
        })
        self.assertEqual(ir.elements[0].layer, "technology")

    def test_omitted_layer_is_filled_from_the_table(self) -> None:
        ir = self._raw_ir({
            "id": "n1", "archimate_type": "application-service",
            "name": "Svc", "source_confidence": "explicit",
        })
        self.assertEqual(ir.elements[0].layer, "application")

    def test_unknown_type_left_untouched_for_literal_to_reject(self) -> None:
        """An unrecognized type must fail as an unknown-type error, not a
        confusing layer-correction error."""
        with self.assertRaises(ValidationError):
            self._raw_ir({
                "id": "n1", "archimate_type": "not-a-real-type", "layer": "business",
                "name": "X", "source_confidence": "explicit",
            })


class RelationshipLegalityFunctionTests(unittest.TestCase):
    """Direct tests of the standalone rule function, independent of the IR."""

    def test_composition_within_same_layer_and_aspect_is_legal(self) -> None:
        self.assertTrue(is_legal_relationship("business-actor", "composition", "business-role"))

    def test_composition_across_aspects_is_illegal(self) -> None:
        self.assertFalse(is_legal_relationship("business-process", "composition", "business-actor"))

    def test_serving_crosses_layers_freely(self) -> None:
        self.assertTrue(is_legal_relationship("technology-node", "serving", "application-service"))

    def test_access_only_targets_passive_structure(self) -> None:
        self.assertTrue(is_legal_relationship("application-function", "access", "data-object"))
        self.assertFalse(is_legal_relationship("data-object", "access", "business-actor"))

    def test_association_is_always_legal(self) -> None:
        self.assertTrue(is_legal_relationship("business-actor", "association", "technology-node"))

    def test_influence_is_never_legal_in_v1_vocabulary(self) -> None:
        self.assertFalse(is_legal_relationship("business-actor", "influence", "business-process"))

    def test_unknown_element_type_is_reported_legal(self) -> None:
        """An unknown type is already a hard Pydantic failure elsewhere; the
        legality function must not pile a second, misleading error on top."""
        self.assertTrue(is_legal_relationship("not-a-type", "composition", "business-actor"))

    def test_nearest_substitute_for_illegal_assignment(self) -> None:
        # business-process (behavior) -> business-actor (active-structure):
        # assignment requires an active-structure source, so this is illegal;
        # serving is the nearest legal substitute.
        self.assertIsNone(is_legal_relationship("business-process", "assignment", "business-actor") or None)
        self.assertEqual(
            nearest_legal_substitute("business-process", "assignment", "business-actor"), "serving",
        )

    def test_no_substitute_returns_none(self) -> None:
        # composition/aggregation both require same-aspect+same-layer; a
        # cross-aspect composition has no honest structural substitute.
        self.assertIsNone(nearest_legal_substitute("business-process", "composition", "business-actor"))

    def test_association_never_offered_as_a_substitute(self) -> None:
        substitute = nearest_legal_substitute("business-process", "composition", "business-actor")
        self.assertNotEqual(substitute, "association")


class RelationshipAutoCorrectionTests(unittest.TestCase):
    def test_legal_relationship_passes_through_unchanged(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("actor1", "business-actor", "business"),
                _el("proc1", "business-process", "business"),
            ],
            relationships=[_rel("r1", "assignment", "actor1", "proc1")],
        )
        self.assertFalse(ir.relationships[0].auto_corrected)
        self.assertIsNone(ir.relationships[0].correction_note)
        self.assertEqual(ir.relationships[0].type, "assignment")

    def test_illegal_relationship_with_substitute_is_auto_corrected(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("proc1", "business-process", "business"),
                _el("actor1", "business-actor", "business"),
            ],
            relationships=[_rel("r1", "assignment", "proc1", "actor1")],
        )
        rel = ir.relationships[0]
        self.assertEqual(rel.type, "serving")
        self.assertTrue(rel.auto_corrected)
        self.assertIsNotNone(rel.correction_note)
        self.assertEqual(ir.open_questions, [])

    def test_illegal_relationship_with_no_substitute_is_dropped_and_questioned(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("proc1", "business-process", "business"),
                _el("actor1", "business-actor", "business"),
            ],
            relationships=[_rel("r1", "composition", "proc1", "actor1")],
        )
        self.assertEqual(ir.relationships, [])
        self.assertEqual(len(ir.open_questions), 1)
        self.assertIn("proc1", ir.open_questions[0].blocks)
        self.assertIn("actor1", ir.open_questions[0].blocks)

    def test_relationship_with_unknown_endpoint_is_left_for_semantic_errors(self) -> None:
        """A dangling endpoint is not a legality question — don't guess at it."""
        ir = minimal_ir(
            elements=[_el("actor1", "business-actor", "business")],
            relationships=[_rel("r1", "composition", "actor1", "ghost")],
        )
        self.assertEqual(len(ir.relationships), 1)
        self.assertFalse(ir.relationships[0].auto_corrected)
        self.assertTrue(any("ghost" in e for e in ir.semantic_errors()))


class UniqueIdTests(unittest.TestCase):
    def test_duplicate_element_and_grouping_ids_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ArchimateIR(
                viewpoint="layered",
                elements=[_el("dup", "business-actor", "business")],
                groupings=[ArchimateGrouping(id="dup", label="Dup")],
            )


class SemanticErrorTests(unittest.TestCase):
    def test_dangling_relationship_endpoint_is_an_error(self) -> None:
        ir = minimal_ir(
            elements=[_el("actor1", "business-actor", "business")],
            relationships=[_rel("r1", "association", "actor1", "ghost")],
        )
        self.assertTrue(any("not a known element id" in e for e in ir.semantic_errors()))

    def test_self_loop_relationship_is_an_error(self) -> None:
        ir = minimal_ir(
            elements=[_el("actor1", "business-actor", "business")],
            relationships=[_rel("r1", "association", "actor1", "actor1")],
        )
        self.assertTrue(any("itself" in e for e in ir.semantic_errors()))

    def test_empty_relationships_with_two_plus_elements_is_an_error(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("actor1", "business-actor", "business"),
                _el("proc1", "business-process", "business"),
            ],
            relationships=[],
        )
        self.assertTrue(any("relationships is empty" in e for e in ir.semantic_errors()))

    def test_element_group_reference_to_undeclared_grouping_is_an_error(self) -> None:
        ir = minimal_ir(
            elements=[_el("actor1", "business-actor", "business", group="ghost_group")],
            relationships=[],
        )
        self.assertTrue(any("ghost_group" in e for e in ir.semantic_errors()))

    def test_technology_viewpoint_rejects_non_technology_elements(self) -> None:
        ir = minimal_ir(
            viewpoint="technology",
            elements=[
                _el("node1", "technology-node", "technology"),
                _el("actor1", "business-actor", "business"),
            ],
            relationships=[_rel("r1", "association", "node1", "actor1")],
        )
        self.assertTrue(any("technology viewpoint" in e for e in ir.semantic_errors()))

    def test_application_cooperation_rejects_all_business_elements(self) -> None:
        ir = minimal_ir(
            viewpoint="application_cooperation",
            elements=[
                _el("actor1", "business-actor", "business"),
                _el("actor2", "business-role", "business"),
            ],
            relationships=[_rel("r1", "association", "actor1", "actor2")],
        )
        self.assertTrue(any("application-cooperation viewpoint" in e for e in ir.semantic_errors()))


class SemanticWarningTests(unittest.TestCase):
    def test_thin_panel_on_a_nontrivial_diagram_warns(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("a", "business-actor", "business"),
                _el("b", "business-role", "business"),
                _el("c", "business-process", "business"),
                _el("d", "business-service", "business"),
            ],
            relationships=[_rel("r1", "association", "a", "b")],
        )
        warnings = ir.semantic_warnings()
        self.assertTrue(any("panel_summary" in w for w in warnings))
        self.assertTrue(any("walkthrough" in w for w in warnings))

    def test_unexplained_inference_warns(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("a", "business-actor", "business", source_confidence="inferred"),
            ],
            relationships=[],
        )
        self.assertTrue(any("no `notes`" in w for w in ir.semantic_warnings()))

    def test_missing_coaching_note_warns_when_open_questions_exist(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("proc1", "business-process", "business"),
                _el("actor1", "business-actor", "business"),
            ],
            relationships=[_rel("r1", "composition", "proc1", "actor1")],  # dropped -> open question
        )
        self.assertTrue(ir.open_questions)
        self.assertTrue(any("coaching_note" in w for w in ir.semantic_warnings()))

    def test_fully_authored_panel_has_no_warnings(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("actor1", "business-actor", "business"),
                _el("proc1", "business-process", "business"),
            ],
            relationships=[_rel("r1", "assignment", "actor1", "proc1")],
            panel_summary="Shows how a customer's request is handled end to end.",
            viewpoint_rationale="The brief asks how the capability is delivered end to end.",
            layers_excluded_reason="The brief only concerns the business capability itself.",
        )
        self.assertEqual(ir.semantic_warnings(), [])


class DerivedHelperTests(unittest.TestCase):
    def test_layers_present_is_in_canonical_order(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("t1", "technology-node", "technology"),
                _el("a1", "business-actor", "business"),
                _el("s1", "application-service", "application"),
            ],
            relationships=[],
        )
        self.assertEqual(ir.layers_present(), ["business", "application", "technology"])

    def test_confidence_counts_tally_correctly(self) -> None:
        ir = minimal_ir(
            elements=[
                _el("a", "business-actor", "business", source_confidence="explicit"),
                _el("b", "business-role", "business", source_confidence="inferred", notes="why"),
                _el("c", "business-process", "business", source_confidence="assumed", notes="why"),
            ],
            relationships=[],
        )
        self.assertEqual(ir.confidence_counts(), {"explicit": 1, "inferred": 1, "assumed": 1})

    def test_open_question_can_be_constructed_standalone(self) -> None:
        q = OpenQuestion(id="q1", question="Which system owns the customer record?")
        self.assertEqual(q.blocks, [])


if __name__ == "__main__":
    unittest.main()
