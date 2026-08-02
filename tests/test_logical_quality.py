"""Logical L→R order, density IR rules, and dual-audience overview."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    ensure_ir_overview_panel,
    synthesize_ir_overview_body,
)
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.domain.ir import Corridor, Flow, Leaf, LogicalIR
from tests.test_corridor_ir_layout import realistic_logical_ir


def _leaf(lid: str, shape: str, label: str) -> Leaf:
    return Leaf(id=lid, shape_type=shape, label=label)


def _compile(ir: LogicalIR) -> str:
    return compile_corridor_ir(ir, page_name="Logical")


class LogicalRowOrderTests(unittest.TestCase):
    def test_domains_read_left_to_right(self) -> None:
        xml = _compile(realistic_logical_ir())
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}

        def x(cid: str) -> float:
            g = cells[cid].find("mxGeometry")
            return float(g.get("x") or 0)

        self.assertLess(x("domain_experience"), x("domain_core"))
        self.assertLess(x("domain_core"), x("domain_data"))
        self.assertLess(x("domain_data"), x("domain_platform"))
        self.assertAlmostEqual(x("domain_saas"), x("domain_data"), delta=5.0)
        sy = float(cells["domain_saas"].find("mxGeometry").get("y") or 0)
        dy = float(cells["domain_data"].find("mxGeometry").get("y") or 0)
        dh = float(cells["domain_data"].find("mxGeometry").get("height") or 0)
        self.assertGreaterEqual(sy, dy + dh - 1.0)

    def test_rejects_both_core_and_commerce(self) -> None:
        with self.assertRaises(Exception):
            LogicalIR(
                corridors=[
                    Corridor(id="domain_experience", label="Experience", leaves=[_leaf("p", "logical_portal", "Portal")]),
                    Corridor(id="domain_core", label="Core", leaves=[_leaf("a", "logical_capability", "A")]),
                    Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("b", "logical_capability", "B")]),
                    Corridor(id="domain_data", label="Data", leaves=[_leaf("d", "logical_datastore", "Store")]),
                ],
                flows=[Flow(source="p", target="a"), Flow(source="a", target="d")],
            )

    def test_density_semantic_error(self) -> None:
        leaves = [_leaf(f"c{i}", "logical_capability", f"Cap{i}") for i in range(5)]
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("p", "logical_portal", "Portal")]),
                Corridor(id="domain_core", label="Core", leaves=leaves),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("d", "logical_datastore", "Store")]),
            ],
            flows=[Flow(source="p", target="c0"), Flow(source="c0", target="d")],
        )
        errs = ir.semantic_errors()
        self.assertTrue(any("domain_core" in e and "4" in e for e in errs), errs)


class LogicalOverviewTests(unittest.TestCase):
    def test_overview_has_business_and_technical(self) -> None:
        xml = _compile(realistic_logical_ir())
        body = synthesize_ir_overview_body(xml, "Logical", "Demo System")
        self.assertIn("Business journey", body)
        self.assertIn("Technical flow", body)
        self.assertIn("Business:", body)
        self.assertIn("Experience", body)
        out = ensure_ir_overview_panel(xml, "Logical", "Demo System")
        self.assertIn("overview_panel", out)


if __name__ == "__main__":
    unittest.main()
