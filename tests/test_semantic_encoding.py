"""Phase 2 (2a/2b): typed IR semantics — Flow.classification/protection,
Leaf.criticality — round-trip with defaults, drive a distinct style per
value, and are stamped as mxCell attributes only when non-default.

This is the regression guard the plan calls for at this step: "legacy IR
fixtures round-trip with defaults; each enum value maps to a distinct style
string."
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import palette
from app.adapters.outbound.diagram.compiler import compile_corridor_ir, theme
from app.domain.ir import AnchoredCorridor, Corridor, Flow, Leaf, SecurityIR


def _leaf(i: str, label: str, **kw) -> Leaf:
    return Leaf(id=i, shape_type="aws_ec2", label=label, **kw)


class DefaultsRoundTripTests(unittest.TestCase):
    """A legacy IR/flow/leaf built without the new fields must still validate
    with the documented defaults — the whole point of adding fields with
    defaults under ``extra="forbid"``."""

    def test_flow_defaults(self) -> None:
        flow = Flow(source="a", target="b")
        self.assertEqual(flow.classification, "none")
        self.assertEqual(flow.protection, "none")

    def test_leaf_defaults(self) -> None:
        leaf = Leaf(id="x", shape_type="aws_ec2", label="X")
        self.assertEqual(leaf.criticality, "standard")


class DistinctStyleTests(unittest.TestCase):
    """Each enum value must render a genuinely different style string —
    otherwise the field exists but nothing distinguishes it visually."""

    def test_leaf_style_distinct_by_criticality(self) -> None:
        standard = theme.leaf_style("standard")
        critical = theme.leaf_style("critical")
        self.assertNotEqual(standard, critical)
        self.assertIn(palette.CRITICAL_LEAF_STROKE, critical)
        self.assertNotIn(palette.CRITICAL_LEAF_STROKE, standard)

    def test_flow_style_distinct_per_classification(self) -> None:
        styles = {
            level: theme.flow_style(primary=True, labelled=False, classification=level)
            for level in ("none", "internal", "confidential", "pii", "pci")
        }
        self.assertEqual(len(set(styles.values())), len(styles), "every classification must render distinctly")
        for level, style in styles.items():
            self.assertIn(palette.CLASSIFICATION_COLORS[level], style)

    def test_flow_style_primary_channel_independent_of_classification(self) -> None:
        """primary/dashed must still work regardless of classification — the
        two channels must never collapse into one."""
        primary_pii = theme.flow_style(primary=True, labelled=False, classification="pii")
        secondary_pii = theme.flow_style(primary=False, labelled=False, classification="pii")
        self.assertIn("dashed=1", secondary_pii)
        self.assertNotIn("dashed=1", primary_pii)
        # Same colour either way — only weight/dash differ.
        self.assertIn(palette.CLASSIFICATION_COLORS["pii"], primary_pii)
        self.assertIn(palette.CLASSIFICATION_COLORS["pii"], secondary_pii)

    def test_actor_style_distinct_per_kind(self) -> None:
        styles = {kind: theme.actor_style(kind) for kind in ("users", "saas_ingress", "saas_egress", "on_prem")}
        self.assertEqual(len(set(styles.values())), len(styles), "every actor kind must render distinctly")


class MxCellStampingTests(unittest.TestCase):
    """Compiled XML must carry the new fields as plain attributes, only when
    non-default — verified end-to-end through compile_corridor_ir, not just
    at the CellWriter unit level."""

    def _compile(self) -> ET.Element:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns", "Route 53")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[
                    _leaf("db", "Primary DB", criticality="critical"),
                ]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="External SaaS", anchor_id="zone_restricted",
                leaves=[_leaf("vault", "Secrets Vault")],
            )],
            flows=[
                Flow(source="dns", target="db", label="Query", classification="pii", protection="tls"),
                Flow(source="db", target="vault", label="Reads secret"),
            ],
        )
        xml = compile_corridor_ir(ir, page_name="Security")
        return ET.fromstring(xml)

    def test_critical_leaf_stamped(self) -> None:
        root = self._compile()
        db_cell = next(c for c in root.iter("mxCell") if c.get("id") == "db")
        self.assertEqual(db_cell.get("criticality"), "critical")
        dns_cell = next(c for c in root.iter("mxCell") if c.get("id") == "dns")
        self.assertIsNone(dns_cell.get("criticality"), "standard is the default; must not be stamped")

    def test_classified_protected_flow_stamped(self) -> None:
        root = self._compile()
        edges = [c for c in root.iter("mxCell") if c.get("edge") == "1"]
        pii_edge = next(e for e in edges if e.get("classification") == "pii")
        self.assertEqual(pii_edge.get("protection"), "tls")
        other_edge = next(e for e in edges if e.get("classification") is None)
        self.assertIsNone(other_edge.get("protection"), "none is the default; must not be stamped")


if __name__ == "__main__":
    unittest.main()
