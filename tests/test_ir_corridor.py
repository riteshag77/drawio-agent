"""CorridorIR validation — shared base plus Logical/Security/Data-flow vocabulary."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.domain.ir import (
    AnchoredCorridor,
    Corridor,
    DataFlowIR,
    ExternalActor,
    Flow,
    Leaf,
    LogicalIR,
    SecurityIR,
)


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc") -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


class SharedCorridorTests(unittest.TestCase):
    """Rules that must hold across all three views identically (base.py)."""

    def test_empty_corridor_is_impossible(self) -> None:
        with self.assertRaises(ValidationError):
            Corridor(id="domain_core", label="Core", leaves=[])

    def test_duplicate_ids_across_corridors_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[
                    Corridor(id="domain_experience", label="Experience", leaves=[_leaf("dup")]),
                    Corridor(id="domain_core", label="Core", leaves=[_leaf("dup")]),
                ],
            )

    def test_shelf_anchor_must_reference_a_row_corridor(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="domain_saas", label="SaaS", anchor_id="domain_data", leaves=[_leaf("b")])],
            )

    def test_whole_row_shelf_anchor_id_none_is_allowed(self) -> None:
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("a")])],
            anchored=[AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("b")])],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_flow_to_unknown_id_is_an_error(self) -> None:
        ir = LogicalIR(corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])])
        ir.flows = [Flow(source="a", target="ghost")]
        self.assertTrue(any("not a known cell id" in e for e in ir.semantic_errors()))

    def test_self_loop_is_an_error(self) -> None:
        ir = LogicalIR(corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])])
        ir.flows = [Flow(source="a", target="a")]
        self.assertTrue(any("itself" in e for e in ir.semantic_errors()))

    def test_extra_fields_on_leaf_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Leaf(id="x", shape_type="aws_ecs", label="X", x=10)

    def test_external_actor_shared_type_importable_from_every_module(self) -> None:
        """ExternalActor moved to base.py — every view's package import must still work."""
        from app.domain.ir.base import ExternalActor as BaseActor
        from app.domain.ir.network import ExternalActor as NetworkActor
        self.assertIs(BaseActor, NetworkActor)
        self.assertIs(BaseActor, ExternalActor)


class LogicalVocabularyTests(unittest.TestCase):
    def test_valid_domain_ids_accepted(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store")]),
            ],
            anchored=[AnchoredCorridor(id="domain_saas", label="SaaS", anchor_id="domain_data", leaves=[_leaf("wd")])],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_invented_domain_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(corridors=[Corridor(id="domain_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_non_domain_prefix_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(corridors=[Corridor(id="zone_public", label="Public", leaves=[_leaf("a")])])

    def test_shelf_id_must_be_domain_saas(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[Corridor(id="domain_data", label="Data", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="domain_wrong", label="Wrong", anchor_id="domain_data", leaves=[_leaf("b")])],
            )


class SecurityVocabularyTests(unittest.TestCase):
    def test_valid_zone_ids_accepted(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db")]),
            ],
            anchored=[AnchoredCorridor(id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("wd")])],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_invented_zone_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SecurityIR(corridors=[Corridor(id="zone_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_partner_zone_is_a_valid_row_corridor(self) -> None:
        ir = SecurityIR(corridors=[Corridor(id="zone_partner", label="Partner", leaves=[_leaf("gw")])])
        self.assertEqual(ir.semantic_errors(), [])

    def test_external_saas_shelf_must_anchor_under_restricted(self) -> None:
        with self.assertRaises(ValidationError):
            SecurityIR(
                corridors=[
                    Corridor(id="zone_public", label="Public", leaves=[_leaf("a")]),
                    Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("b")]),
                ],
                anchored=[AnchoredCorridor(id="zone_external_saas", label="SaaS", anchor_id="zone_public", leaves=[_leaf("c")])],
            )


class DataFlowVocabularyTests(unittest.TestCase):
    def test_valid_phase_ids_accepted(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db")]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store", leaves=[_leaf("dash")]),
                AnchoredCorridor(id="phase_saas", label="SaaS", anchor_id="phase_store", leaves=[_leaf("sf")]),
                AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("glue")]),
            ],
        )
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual(len(ir.anchored), 3)

    def test_invented_phase_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(corridors=[Corridor(id="phase_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_double_shelf_both_anchor_under_store(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_process", label="Process", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_process", leaves=[_leaf("b")])],
            )

    def test_control_shelf_must_have_no_anchor(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_control", label="Control", anchor_id="phase_store", leaves=[_leaf("b")])],
            )

    def test_unknown_anchored_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_bogus_shelf", label="Bogus", anchor_id=None, leaves=[_leaf("b")])],
            )


class ExternalActorReuseTests(unittest.TestCase):
    def test_actor_kinds_usable_across_every_corridor_view(self) -> None:
        for ir_cls, prefix in ((LogicalIR, "domain_experience"), (SecurityIR, "zone_public"), (DataFlowIR, "phase_ingest")):
            ir = ir_cls(
                corridors=[Corridor(id=prefix, label="X", leaves=[_leaf("a")])],
                external_actors=[
                    ExternalActor(id="u", kind="users", label="Users"),
                    ExternalActor(id="idp", kind="saas_ingress", label="Okta"),
                ],
            )
            self.assertEqual(len(ir.external_actors), 2)


if __name__ == "__main__":
    unittest.main()
