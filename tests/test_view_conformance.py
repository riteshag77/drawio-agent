"""Tests for focused-view conformance scoring and deterministic fixers."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    achievable_density_target,
    build_architecture_inventory,
    build_single_view_system,
)
from app.adapters.outbound.diagram.view_conformance import score_view
from app.adapters.outbound.diagram.view_fixers import (
    apply_edge_label_defaults,
    apply_view_fixers,
    drop_forbidden_nodes,
    ensure_gap_nodes,
    relabel_generic_data_stores,
)


def _wrap(body: str, name: str = "Logical") -> str:
    return (
        f'<mxfile host="app.diagrams.net"><diagram id="d1" name="{name}">'
        '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{body}</root></mxGraphModel></diagram></mxfile>"
    )


def _leaf(cid: str, label: str, x: int, shape_type: str = "") -> str:
    st = f' shape_type="{shape_type}"' if shape_type else ""
    return (
        f'<mxCell id="{cid}" value="{label}"{st} style="fillColor=#F5F5F5;" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="40" width="78" height="78" as="geometry"/></mxCell>'
    )


class PackBoundaryTests(unittest.TestCase):
    def test_view_system_inherits_core_not_network(self) -> None:
        logical = build_single_view_system("logical")
        self.assertIn("180–220", logical)
        self.assertIn("overview_panel", logical)
        self.assertIn("&lt;", logical)
        self.assertNotIn("Canonical Container Hierarchy", logical)
        self.assertNotIn("Cross-Cutting Groups", logical)
        self.assertNotIn("Availability Zone", logical)


class DensityTargetTests(unittest.TestCase):
    def test_scales_with_inventory(self) -> None:
        self.assertEqual(achievable_density_target(0), 5)
        self.assertEqual(achievable_density_target(2), 5)
        self.assertGreaterEqual(achievable_density_target(8), 8)
        self.assertLessEqual(achievable_density_target(100), 14)


class ConformanceScorerTests(unittest.TestCase):
    def test_forbidden_waf_on_logical(self) -> None:
        xml = _wrap(
            _leaf("waf", "WAF", 40, "aws_waf")
            + _leaf("app", "Orders API", 240, "aws_ecs")
            + '<mxCell id="e1" edge="1" parent="1" source="waf" target="app" value="Calls API">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        scored = score_view(xml, view="logical", min_score=75)
        codes = {v.code for v in scored.violations}
        self.assertIn("forbidden_node", codes)

    def test_unlabeled_edge(self) -> None:
        xml = _wrap(
            _leaf("a", "Orders API", 40, "aws_ecs")
            + _leaf("b", "Customer data store", 240, "aws_rds")
            + '<mxCell id="e1" edge="1" parent="1" source="a" target="b" value="">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        scored = score_view(xml, view="logical", min_score=75)
        self.assertTrue(any(v.code == "unlabeled_edge" for v in scored.violations))

    def test_network_clone_detected(self) -> None:
        primary = _wrap(
            _leaf("waf", "WAF", 40, "aws_waf")
            + _leaf("alb", "ALB", 220, "aws_alb")
            + _leaf("ecs", "ECS", 400, "aws_ecs")
            + '<mxCell id="e1" edge="1" parent="1" source="waf" target="alb" value="HTTPS">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            + '<mxCell id="e2" edge="1" parent="1" source="alb" target="ecs" value="HTTPS">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>',
            name="Network",
        )
        clone = primary.replace('name="Network"', 'name="Logical"')
        scored = score_view(clone, view="logical", primary_xml=primary, min_score=75)
        self.assertTrue(any(v.code == "network_clone" for v in scored.violations))

    def test_escaped_html_leak(self) -> None:
        xml = _wrap(
            _leaf("a", "App", 40, "aws_ecs")
            + _leaf("b", "Store", 240, "aws_rds")
            + '<mxCell id="overview_panel" value="&amp;lt;div&amp;gt;Title&amp;lt;/div&amp;gt;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="400" height="120" as="geometry"/></mxCell>'
        )
        scored = score_view(xml, view="logical", min_score=75)
        self.assertTrue(any(v.code == "escaped_html_leak" for v in scored.violations))


class ViewFixerTests(unittest.TestCase):
    def test_drop_forbidden_keeps_graph(self) -> None:
        xml = _wrap(
            _leaf("waf", "WAF", 40, "aws_waf")
            + _leaf("app", "Orders API", 240, "aws_ecs")
            + _leaf("db", "Aurora", 440, "aws_rds")
            + '<mxCell id="e1" edge="1" parent="1" source="waf" target="app" value="Calls API">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            + '<mxCell id="e2" edge="1" parent="1" source="app" target="db" value="Relies on">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = drop_forbidden_nodes(xml, view="logical")
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("waf", ids)
        self.assertIn("app", ids)
        self.assertIn("db", ids)

    def test_relabel_preserves_shape_type(self) -> None:
        xml = _wrap(_leaf("db", "Aurora", 40, "aws_rds"))
        out = relabel_generic_data_stores(xml, view="logical")
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "db")
        self.assertEqual(cell.get("shape_type"), "aws_rds")
        self.assertIn("data store", (cell.get("value") or "").lower())

    def test_edge_label_defaults(self) -> None:
        xml = _wrap(
            _leaf("app", "App", 40, "aws_ecs")
            + _leaf("db", "Customer data store", 240, "aws_rds")
            + '<mxCell id="e1" edge="1" parent="1" source="app" target="db" value="">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = apply_edge_label_defaults(xml, view="logical")
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        self.assertTrue((edge.get("value") or "").strip())

    def test_gap_nodes(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import append_gaps_to_overview_panel
        from app.adapters.outbound.diagram.xml_diagnostics import _trim_overview_panel_value

        xml = _wrap(
            _leaf("app", "App", 40, "aws_ecs")
            + '<mxCell id="overview_panel" value="'
            "&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;Security&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;1. Perimeter first.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;2. Private app.&lt;/div&gt;"
            '" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="400" height="200" as="geometry"/></mxCell>'
            + '<mxCell id="gap_old" value="Old gap box" gap="1" vertex="1" parent="1">'
            '<mxGeometry x="40" y="400" width="200" height="60" as="geometry"/></mxCell>'
        )
        out = append_gaps_to_overview_panel(
            xml, view="security", gap_labels=["Identity Provider / IdP", "KMS / secrets management"],
        )
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("gap_old", ids)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        val = panel.get("value") or ""
        self.assertIn("Recommended gaps", val)
        self.assertIn("Identity Provider", val)
        self.assertIn("#B85450", val)
        self.assertIn("font-size:22px", val)  # existing title recipe untouched by append
        # Trim must preserve red gaps and keep normal steps
        trimmed = _trim_overview_panel_value(val)
        self.assertIn("Recommended gaps", trimmed)
        self.assertIn("#B85450", trimmed)
        self.assertIn("Perimeter first", trimmed)
        self.assertIn("font-size:22px;font-weight:bold", trimmed)

    def test_space_detail_cards_no_overlap(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import space_detail_cards

        # Four cards stacked on the same coordinates (LLM failure mode)
        cards = ""
        for i, cid in enumerate(("note_a", "note_b", "note_c", "note_d")):
            cards += (
                f'<mxCell id="{cid}" value="&lt;div&gt;Card {i}&lt;/div&gt;&lt;div&gt;• one&lt;/div&gt;'
                f'&lt;div&gt;• two&lt;/div&gt;&lt;div&gt;• three&lt;/div&gt;" '
                f'style="rounded=1;fillColor=#FFFFFF;" vertex="1" parent="1">'
                f'<mxGeometry x="100" y="100" width="280" height="110" as="geometry"/></mxCell>'
            )
        xml = _wrap(
            _leaf("app", "App", 40, "aws_ecs")
            + _leaf("db", "DB", 240, "aws_rds")
            + '<mxCell id="overview_panel" value="Overview" vertex="1" parent="1">'
            '<mxGeometry x="1200" y="40" width="400" height="200" as="geometry"/></mxCell>'
            + cards
        )
        out = space_detail_cards(xml, view="logical")
        rects = []
        for c in ET.fromstring(out).iter("mxCell"):
            cid = c.get("id") or ""
            if not cid.startswith("note_"):
                continue
            g = c.find("mxGeometry")
            self.assertIsNotNone(g)
            x, y = float(g.get("x") or 0), float(g.get("y") or 0)
            w, h = float(g.get("width") or 0), float(g.get("height") or 0)
            self.assertEqual(c.get("parent"), "1")
            # Below architecture icons (y≈40+78)
            self.assertGreaterEqual(y, 100)
            rects.append((x, y, w, h, cid))
        self.assertEqual(len(rects), 4)
        for i, (ax, ay, aw, ah, aid) in enumerate(rects):
            for bx, by, bw, bh, bid in rects[i + 1:]:
                overlap = not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)
                self.assertFalse(overlap, f"{aid} overlaps {bid}")

    def test_apply_view_fixers_chain(self) -> None:
        xml = _wrap(
            _leaf("waf", "WAF", 40, "aws_waf")
            + _leaf("app", "App", 240, "aws_ecs")
            + _leaf("db", "Aurora", 440, "aws_rds")
            + '<mxCell id="e1" edge="1" parent="1" source="app" target="db" value="">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = apply_view_fixers(xml, view="logical")
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("waf", ids)
        db = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "db")
        self.assertIn("data store", (db.get("value") or "").lower())


class InventoryBriefTests(unittest.TestCase):
    def test_inventory_includes_target_model(self) -> None:
        xml = _wrap(
            _leaf("alb", "ALB", 40, "aws_alb")
            + _leaf("ecs", "ECS", 220, "aws_ecs")
            + _leaf("rds", "Aurora", 400, "aws_rds"),
            name="Network",
        )
        inv = build_architecture_inventory(xml, request_text="secure web app with PII")
        self.assertIn("Achievable density target", inv)
        self.assertIn("Trust-zone assignment", inv)
        self.assertIn("Gap candidates", inv)


if __name__ == "__main__":
    unittest.main()
