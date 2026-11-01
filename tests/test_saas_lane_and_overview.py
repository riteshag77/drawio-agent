"""Network SaaS call-path placement + overview clear + focused corridors + edges."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    resolve_saas_network_side,
    saas_default_network_side,
)
from app.adapters.outbound.diagram.edge_routing import (
    fix_edge_anchors_from_geometry,
)
from app.adapters.outbound.diagram.xml_diagnostics import (
    clear_overview_panel_children,
    ensure_overview_panel_position,
)


def _network(cells: str) -> str:
    return (
        '<mxfile><diagram name="Network" id="n1">'
        '<mxGraphModel pageWidth="2800" pageHeight="1600"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _logical(cells: str) -> str:
    return (
        '<mxfile><diagram name="Logical" id="l1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _security(cells: str) -> str:
    return (
        '<mxfile><diagram name="Security" id="s1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _data_flow(cells: str) -> str:
    return (
        '<mxfile><diagram name="Data flow" id="d1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _x(cell: ET.Element) -> float:
    return float(cell.find("mxGeometry").get("x") or 0)


def _y(cell: ET.Element) -> float:
    return float(cell.find("mxGeometry").get("y") or 0)


class CallPathSideTests(unittest.TestCase):
    def test_defaults_okta_ingress_workday_egress(self) -> None:
        self.assertEqual(saas_default_network_side("saas_okta"), "ingress")
        self.assertEqual(saas_default_network_side("saas_workday"), "egress")
        self.assertEqual(saas_default_network_side("saas_successfactors"), "egress")

    def test_edge_actor_to_saas_forces_ingress(self) -> None:
        side = resolve_saas_network_side(
            "saas_salesforce",
            "Salesforce",
            cell_id="sf",
            edges=[("users", "sf"), ("sf", "edge")],
            actor_ids={"users"},
            cloud_descendant_ids={"cloud", "edge", "app"},
        )
        self.assertEqual(side, "ingress")

    def test_edge_app_to_saas_forces_egress(self) -> None:
        side = resolve_saas_network_side(
            "saas_okta",
            "Okta",
            cell_id="ok",
            edges=[("app", "ok")],
            actor_ids={"users"},
            cloud_descendant_ids={"cloud", "app"},
        )
        self.assertEqual(side, "egress")


class LogicalRoutingAndIconTests(unittest.TestCase):
    """Spacing / routing / icon-aspect rules that keep Logical readable."""


    def test_saas_icons_keep_compiler_geometry(self) -> None:
        """assign_saas_shape_types may change style/shape_type, never mxGeometry."""
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types

        page = _logical(
            '<mxCell id="wd" value="Workday (SAAS)" shape_type="saas_workday" style="rounded=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = assign_saas_shape_types(page)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "wd")
        geom = cell.find("mxGeometry")
        self.assertEqual(geom.get("width"), "78")
        self.assertEqual(geom.get("height"), "78")
        self.assertNotIn("imageAspect=0", cell.get("style") or "")
        self.assertTrue((cell.get("style") or "").strip())

    def test_edge_anchors_stay_on_the_shape(self) -> None:
        """Parallel offsets must never push an anchor outside its own icon."""
        page = _logical(
            '<mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="220" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="c" value="C" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="600" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="d" value="D" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="600" y="220" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Uses" style="html=1;" edge="1" parent="1" '
            'source="a" target="c"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" value="Uses" style="html=1;" edge="1" parent="1" '
            'source="b" target="d"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        for cell in ET.fromstring(out).iter("mxCell"):
            if cell.get("edge") != "1":
                continue
            kv = dict(
                p.split("=", 1) for p in (cell.get("style") or "").split(";") if "=" in p
            )
            for key in ("exitDy", "entryDy"):
                self.assertLessEqual(abs(float(kv.get(key, 0))), 34.0)

    def test_blocked_edge_routes_around_intervening_icons(self) -> None:
        """A connector must not run straight through a third box."""
        page = _logical(
            '<mxCell id="src" value="Src" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="mid" value="Mid" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="dst" value="Dst" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="560" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Invokes" style="html=1;" edge="1" parent="1" '
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [
            (float(p.get("x") or 0), float(p.get("y") or 0))
            for p in edge.iter("mxPoint")
            if p.get("as") is None
        ]
        self.assertTrue(pts, "blocked edge should get routing waypoints")
        # Every waypoint clears the intervening box (300..460 x, 100..160 y).
        for px, py in pts:
            self.assertFalse(
                290 < px < 470 and 90 < py < 170,
                f"waypoint {(px, py)} sits on the intervening icon",
            )


class NetworkBadgeRemountTests(unittest.TestCase):
    def test_step_badge_not_on_saas_icon(self) -> None:
        from types import SimpleNamespace

        from app.adapters.outbound.diagram.xml_diagnostics import apply_step_markers

        page = _network(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="40" width="800" height="400" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="48" width="300" height="100" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="78" height="78" as="geometry"/></mxCell>'
        )
        markers = [SimpleNamespace(step_number=1, cell_id="ok")]
        out = apply_step_markers(page, markers)
        badges = [
            c for c in ET.fromstring(out).iter("mxCell")
            if (c.get("id") or "").startswith("step_badge_")
        ]
        self.assertEqual(len(badges), 1)
        # Badge should sit near Edge abs position (500+40, 40+48), not Okta at (200,100)
        bx = float(badges[0].find("mxGeometry").get("x") or 0)
        self.assertGreater(bx, 400.0)


class OverviewClearTests(unittest.TestCase):
    def test_sfs_child_of_overview_is_hoisted(self) -> None:
        page = _logical(
            '<mxCell id="overview_panel" value="Overview" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="400" height="300" as="geometry"/></mxCell>'
            '<mxCell id="sfs" value="SuccessFactors" shape_type="saas_successfactors" style="shape=image;" '
            'vertex="1" parent="overview_panel">'
            '<mxGeometry x="20" y="20" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="app" value="Orders" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="80" y="80" width="120" height="60" as="geometry"/></mxCell>'
        )
        out = clear_overview_panel_children(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["sfs"].get("parent"), "1")
        out2 = ensure_overview_panel_position(out)
        by_id2 = {c.get("id"): c for c in ET.fromstring(out2).iter("mxCell") if c.get("id")}
        panel_x = _x(by_id2["overview_panel"])
        sfs_right = _x(by_id2["sfs"]) + 78
        app_right = _x(by_id2["app"]) + 120
        self.assertGreaterEqual(panel_x, max(sfs_right, app_right))

    def test_sanitize_strips_saas_image_from_overview(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import sanitize_overview_panel_style
        from app.adapters.outbound.diagram.xml_tools import apply_mcp_styles

        page = _logical(
            '<mxCell id="overview_panel" value="SuccessFactors step" '
            'shape_type="saas_successfactors" '
            'style="shape=image;image=data:image/svg+xml,abc;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="400" height="300" as="geometry"/></mxCell>'
        )
        # MCP must not paint the panel
        painted = apply_mcp_styles(page, {"saas_successfactors": "shape=image;image=data:image/svg+xml,xyz;"})
        panel = next(c for c in ET.fromstring(painted).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertEqual(panel.get("shape_type"), "saas_successfactors")  # untouched by MCP
        out = sanitize_overview_panel_style(painted)
        panel2 = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertIsNone(panel2.get("shape_type"))
        self.assertNotIn("shape=image", panel2.get("style") or "")
        self.assertNotIn("data:image", panel2.get("style") or "")
        self.assertIn("fillColor=#FFF8E1", panel2.get("style") or "")


class LogicalJourneyEdgeTests(unittest.TestCase):
    def test_drops_data_store_to_okta(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import (
            drop_nonsensical_logical_edges,
        )

        page = _logical(
            '<mxCell id="store" value="Customer data store" vertex="1" parent="1">'
            '<mxGeometry x="400" y="200" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" vertex="1" parent="1">'
            '<mxGeometry x="200" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="store" target="ok" value="Invokes" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = drop_nonsensical_logical_edges(page, view="logical")
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("e1", ids)

    def test_rewrites_invokes_on_okta_to_authenticates(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import drop_nonsensical_logical_edges

        page = _logical(
            '<mxCell id="user" value="Users" vertex="1" parent="1">'
            '<mxGeometry x="40" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" vertex="1" parent="1">'
            '<mxGeometry x="200" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="user" target="ok" value="Invokes" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = drop_nonsensical_logical_edges(page, view="logical")
        e1 = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        self.assertEqual(e1.get("value"), "Authenticates via")

    def test_apply_view_fixers_chain_drops_store_okta(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import apply_view_fixers

        page = _logical(
            '<mxCell id="store" value="Customer data store" vertex="1" parent="1">'
            '<mxGeometry x="400" y="200" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" vertex="1" parent="1">'
            '<mxGeometry x="200" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="store" target="ok" value="Invokes" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = apply_view_fixers(page, view="logical")
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("e1", ids)


class ParallelEdgeOffsetTests(unittest.TestCase):
    def test_parallel_edges_get_staggered_dy(self) -> None:
        page = _logical(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="40" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="c" value="C" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="a" target="c" parent="1" style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" source="b" target="c" parent="1" style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        styles = {
            c.get("id"): (c.get("style") or "")
            for c in ET.fromstring(out).iter("mxCell")
            if c.get("edge") == "1"
        }
        self.assertTrue(any("exitDy=" in s for s in styles.values()))


class PartTenOverviewContainerGapTests(unittest.TestCase):
    """Overview panel must stay a root-level docs cell, not a channel leaf."""

    def test_overview_panel_survives_view_fixers(self) -> None:
        from app.adapters.outbound.diagram.view_fixers import apply_view_fixers

        page = _logical(
            '<mxCell id="overview_panel" value="Overview" style="fillColor=#FFF8E1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="420" height="200" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" shape_type="logical_channel" vertex="1" parent="1">'
            '<mxGeometry x="360" y="400" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_view_fixers(page, view="logical")
        cells = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("overview_panel", cells)
        self.assertEqual(cells["overview_panel"].get("parent"), "1")


if __name__ == "__main__":
    unittest.main()
