"""Network SaaS call-path placement + overview clear + focused corridors + edges."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    resolve_saas_network_side,
    saas_default_network_side,
)
from app.adapters.outbound.diagram.placement_fixers import (
    enforce_view_journey_columns,
    fix_edge_anchors_from_geometry,
    hoist_security_saas_from_private_zones,
    resolve_misplaced_saas_leaves,
)
from app.adapters.outbound.diagram.xml_diagnostics import (
    clear_overview_panel_children,
    ensure_overview_panel_position,
    repair_xml,
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


class NetworkCallPathPlacementTests(unittest.TestCase):
    def test_okta_left_workday_right_of_cloud(self) -> None:
        """Users → Okta → Cloud; Cloud app → Workday profile."""
        page = _network(
            '<mxCell id="users" value="Users" shape_type="aws_users" style="ellipse;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="40" width="800" height="500" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="60" width="300" height="100" as="geometry"/></mxCell>'
            '<mxCell id="app" value="Orders" shape_type="aws_ecs" style="shape=mxgraph.aws4.resourceIcon;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="100" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="wd" value="Workday" shape_type="saas_workday" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="140" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="users" target="ok" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" source="ok" target="edge" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e3" edge="1" source="app" target="wd" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_saas_leaves(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["ok"].get("parent"), "1")
        self.assertEqual(by_id["wd"].get("parent"), "1")
        cloud_x = _x(by_id["cloud"])
        cloud_right = cloud_x + float(by_id["cloud"].find("mxGeometry").get("width") or 0)
        self.assertLess(_x(by_id["users"]), _x(by_id["ok"]))
        self.assertLess(_x(by_id["ok"]) + 78, cloud_x)
        self.assertGreaterEqual(_x(by_id["wd"]), cloud_right)
        self.assertGreaterEqual(_y(by_id["ok"]), 80.0)

    def test_user_direct_to_cloud_only_egress_saas(self) -> None:
        """Users → Edge first; only Workday on the right."""
        page = _network(
            '<mxCell id="users" value="Users" shape_type="aws_users" style="ellipse;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="40" width="800" height="500" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="60" width="300" height="100" as="geometry"/></mxCell>'
            '<mxCell id="app" value="HR App" shape_type="aws_ecs" style="shape=mxgraph.aws4.resourceIcon;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="100" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="wd" value="Workday" shape_type="saas_workday" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="50" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="users" target="edge" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" source="app" target="wd" parent="1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_saas_leaves(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        cloud_right = _x(by_id["cloud"]) + float(by_id["cloud"].find("mxGeometry").get("width") or 0)
        self.assertGreaterEqual(_x(by_id["wd"]), cloud_right)

    def test_defaults_without_edges_okta_left_sfs_right(self) -> None:
        page = _network(
            '<mxCell id="users" value="Users" shape_type="aws_users" style="ellipse;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="40" width="900" height="500" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="60" width="300" height="100" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="sfs" value="SuccessFactors" shape_type="saas_successfactors" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="140" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_saas_leaves(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        cloud_x = _x(by_id["cloud"])
        cloud_right = cloud_x + float(by_id["cloud"].find("mxGeometry").get("width") or 0)
        self.assertLess(_x(by_id["ok"]) + 78, cloud_x)
        self.assertGreaterEqual(_x(by_id["sfs"]), cloud_right)

    def test_repair_preserves_call_path_sides(self) -> None:
        page = _network(
            '<mxCell id="users" value="Users" shape_type="aws_users" style="ellipse;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="40" width="800" height="500" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="48" width="300" height="100" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="wd" value="Workday" shape_type="saas_workday" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="1400" y="100" width="78" height="78" as="geometry"/></mxCell>'
        )
        fixed, _ = repair_xml(page)
        by_id = {c.get("id"): c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id")}
        cloud_x = _x(by_id["cloud"])
        cloud_right = cloud_x + float(by_id["cloud"].find("mxGeometry").get("width") or 0)
        self.assertLess(_x(by_id["ok"]), cloud_x)
        self.assertGreaterEqual(_x(by_id["wd"]), cloud_right - 5)


class LogicalCorridorTests(unittest.TestCase):
    def test_saas_stays_in_domain_saas_not_network_lane(self) -> None:
        page = _logical(
            '<mxCell id="domain_commerce" value="Commerce" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="80" width="300" height="200" as="geometry"/></mxCell>'
            '<mxCell id="checkout" value="Checkout" vertex="1" parent="domain_commerce">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_saas" value="External SaaS" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="80" y="80" width="280" height="200" as="geometry"/></mxCell>'
            '<mxCell id="sf" value="Salesforce (SAAS)" shape_type="saas_salesforce" style="shape=image;" '
            'vertex="1" parent="domain_saas">'
            '<mxGeometry x="40" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="domain_saas">'
            '<mxGeometry x="140" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        # Non-identity SaaS stay in domain_saas vertical shelf; Okta may move to spine
        self.assertEqual(by_id["sf"].get("parent"), "domain_saas")
        # External SaaS shelf sits under / aligned with a domain column (not a
        # mandatory right-hand neighbour of commerce).
        saas_y = _y(by_id["domain_saas"])
        commerce_y = _y(by_id["domain_commerce"])
        self.assertGreaterEqual(saas_y, commerce_y)
        # Vertical stack inside shelf when 2+ non-hoisted — with only SF left, still nested
        self.assertEqual(by_id["sf"].get("parent"), "domain_saas")
        # Okta identity prefers journey spine (parent=1) when layout runs
        self.assertIn(by_id["ok"].get("parent"), {"1", "domain_saas"})
        if by_id["ok"].get("parent") == "domain_saas":
            self.assertAlmostEqual(_x(by_id["ok"]), _x(by_id["sf"]), delta=1.0)
            self.assertLess(_y(by_id["ok"]), _y(by_id["sf"]))

    def test_saas_shelf_anchors_under_data_domain(self) -> None:
        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="80" width="280" height="180" as="geometry"/></mxCell>'
            '<mxCell id="cap" value="Orders" style="rounded=1;" vertex="1" parent="domain_core">'
            '<mxGeometry x="40" y="48" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="520" y="80" width="280" height="180" as="geometry"/></mxCell>'
            '<mxCell id="ds" value="Customer data store" style="rounded=1;" vertex="1" parent="domain_data">'
            '<mxGeometry x="40" y="48" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="domain_saas" value="External SaaS" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="80" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="wd" value="Workday (SAAS)" shape_type="saas_workday" style="shape=image;" '
            'vertex="1" parent="domain_saas">'
            '<mxGeometry x="40" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="cap" target="ds" value="Writes">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        data = by_id["domain_data"].find("mxGeometry")
        saas = by_id["domain_saas"].find("mxGeometry")
        self.assertAlmostEqual(float(saas.get("x")), float(data.get("x")), delta=2.0)
        self.assertGreater(
            float(saas.get("y")),
            float(data.get("y")) + float(data.get("height")) - 1.0,
        )
    def test_logical_edges_get_waypoints_clear_of_icons(self) -> None:
        page = _logical(
            '<mxCell id="a" value="Orders" vertex="1" parent="1">'
            '<mxGeometry x="40" y="120" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="mid" value="Catalog" vertex="1" parent="1">'
            '<mxGeometry x="200" y="120" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="Store" vertex="1" parent="1">'
            '<mxGeometry x="400" y="120" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" source="a" target="b" parent="1" style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" source="a" target="mid" parent="1" style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        e1 = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        self.assertIn("orthogonalEdgeStyle", e1.get("style") or "")
        # Parallel edges from same source get face spread / Dy offset
        self.assertTrue("exitDy=" in (e1.get("style") or "") or "exitY=" in (e1.get("style") or ""))

    def test_logical_spine_employee_okta_portal_compact(self) -> None:
        page = _logical(
            '<mxCell id="emp" value="Employees" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="400" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="800" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="600" y="300" width="100" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_commerce" value="Commerce" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="80" width="280" height="200" as="geometry"/></mxCell>'
        )
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types
        tagged = assign_logical_shape_types(page)
        out = enforce_view_journey_columns(tagged)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("aws4.users", (by_id["emp"].get("style") or "").lower())
        self.assertIn("aws4.client", (by_id["portal"].get("style") or "").lower())
        # Compact: Okta near Employees (not hundreds of px away)
        self.assertLess(abs(_x(by_id["ok"]) - _x(by_id["emp"])), 200.0)
        # Journey edges present
        edges = [
            (c.get("source"), c.get("target"), c.get("value"))
            for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"
        ]
        pairs = {(s, t) for s, t, _v in edges}
        self.assertIn(("emp", "ok"), pairs)
        self.assertIn(("ok", "portal"), pairs)

    def _domain_with_apps(self, count: int) -> dict[str, ET.Element]:
        apps = "".join(
            f'<mxCell id="a{i}" value="App {i}" style="rounded=1;" vertex="1" '
            f'parent="domain_core">'
            f'<mxGeometry x="{40 + i * 50}" y="60" width="100" height="40" '
            f'as="geometry"/></mxCell>'
            for i in range(count)
        )
        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="60" width="200" height="120" as="geometry"/></mxCell>'
            + apps
        )
        out = enforce_view_journey_columns(page)
        return {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}

    def test_logical_domain_apps_never_exceed_four_per_row(self) -> None:
        """Hard cap: a 5th app in a full row wraps to the next line."""
        by_id = self._domain_with_apps(10)
        rows: dict[float, int] = {}
        for i in range(10):
            y = round(float(by_id[f"a{i}"].find("mxGeometry").get("y") or 0))
            rows[y] = rows.get(y, 0) + 1
        self.assertTrue(rows)
        self.assertLessEqual(max(rows.values()), 4)
        # Domain grew to fit the wrapped rows.
        dh = float(by_id["domain_core"].find("mxGeometry").get("height") or 0)
        self.assertGreater(dh, 150)

    def test_logical_domain_apps_use_a_balanced_grid(self) -> None:
        """Four apps read better as 2x2 than as one 900px-wide row."""
        by_id = self._domain_with_apps(4)
        ys = {round(float(by_id[f"a{i}"].find("mxGeometry").get("y") or 0)) for i in range(4)}
        xs = {round(float(by_id[f"a{i}"].find("mxGeometry").get("x") or 0)) for i in range(4)}
        self.assertEqual(len(ys), 2)
        self.assertEqual(len(xs), 2)
        dw = float(by_id["domain_core"].find("mxGeometry").get("width") or 0)
        self.assertLess(dw, 700.0, "balanced grid keeps domains on-screen")

    def test_logical_application_gets_okta_redirect(self) -> None:
        page = _logical(
            '<mxCell id="emp" value="Employees" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="64" height="64" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="64" height="64" as="geometry"/></mxCell>'
            '<mxCell id="app" value="Application" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="100" width="120" height="40" as="geometry"/></mxCell>'
        )
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types
        tagged = assign_logical_shape_types(page)
        out = enforce_view_journey_columns(tagged)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["app"].get("shape_type"), "logical_channel")
        pairs = {
            (c.get("source"), c.get("target"))
            for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"
        }
        self.assertIn(("ok", "app"), pairs)

    def test_step_badges_resnap_after_spine(self) -> None:
        from types import SimpleNamespace

        from app.adapters.outbound.diagram.xml_diagnostics import (
            apply_step_markers,
            reposition_step_badges,
            repair_xml,
        )

        page = _logical(
            '<mxCell id="emp" value="Employees" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="400" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="800" y="80" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="600" y="300" width="100" height="60" as="geometry"/></mxCell>'
        )
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types
        tagged = assign_logical_shape_types(page)
        badged = apply_step_markers(tagged, [SimpleNamespace(step_number=1, cell_id="emp")])
        # Spine moves Employees — badges must follow via repair / reposition.
        fixed, _ = repair_xml(badged)
        badges = [
            c for c in ET.fromstring(fixed).iter("mxCell")
            if (c.get("id") or "").startswith("step_badge_")
        ]
        self.assertEqual(len(badges), 1)
        self.assertEqual(badges[0].get("anchor"), "emp")
        by_id = {c.get("id"): c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id")}
        emp_x = float(by_id["emp"].find("mxGeometry").get("x") or 0)
        emp_y = float(by_id["emp"].find("mxGeometry").get("y") or 0)
        bx = float(badges[0].find("mxGeometry").get("x") or 0)
        by = float(badges[0].find("mxGeometry").get("y") or 0)
        self.assertLess(abs(bx - (emp_x - 11)), 2.0)
        self.assertLess(abs(by - (emp_y - 11)), 2.0)
        # Direct reposition is idempotent
        again = reposition_step_badges(fixed)
        self.assertIn("step_badge_1", again)


class SecuritySaasHoistTests(unittest.TestCase):
    def test_workday_under_private_hoisted_to_external_saas_zone(self) -> None:
        page = _security(
            '<mxCell id="zone_private" value="Private App Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="400" y="80" width="360" height="280" as="geometry"/></mxCell>'
            '<mxCell id="ecs" value="ECS Portal" style="rounded=1;" vertex="1" parent="zone_private">'
            '<mxGeometry x="40" y="48" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="wd" value="Workday (SAAS)" shape_type="saas_workday" style="shape=image;" '
            'vertex="1" parent="zone_private">'
            '<mxGeometry x="180" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = hoist_security_saas_from_private_zones(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("zone_external_saas", by_id)
        self.assertEqual(by_id["wd"].get("parent"), "zone_external_saas")
        self.assertEqual(by_id["ecs"].get("parent"), "zone_private")

    def test_enforce_journey_columns_hoists_security_saas(self) -> None:
        page = _security(
            '<mxCell id="zone_private" value="Private App Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="400" height="300" as="geometry"/></mxCell>'
            '<mxCell id="sf" value="SuccessFactors (SAAS)" shape_type="saas_successfactors" '
            'style="shape=image;" vertex="1" parent="zone_private">'
            '<mxGeometry x="40" y="60" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["sf"].get("parent"), "zone_external_saas")


class LogicalRoutingAndIconTests(unittest.TestCase):
    """Spacing / routing / icon-aspect rules that keep Logical readable."""

    def _spine_page(self) -> str:
        return _logical(
            '<mxCell id="emp" value="Employees" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="420" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="okta" value="Okta (SAAS)" shape_type="saas_okta" style="rounded=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="760" y="90" width="140" height="50" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="560" y="300" width="100" height="60" as="geometry"/></mxCell>'
        )

    def test_spine_survives_pitch_and_overlap_fixers(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types

        fixed, _ = repair_xml(assign_logical_shape_types(self._spine_page()))
        by_id = {c.get("id"): c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id")}
        boxes = []
        for cid in ("emp", "okta", "portal"):
            g = by_id[cid].find("mxGeometry")
            boxes.append((float(g.get("x") or 0), float(g.get("width") or 0)))
        boxes.sort()
        for (x1, w1), (x2, _w2) in zip(boxes, boxes[1:]):
            self.assertGreaterEqual(x2, x1 + w1, "spine icons must not overlap")
            self.assertLess(x2 - (x1 + w1), 200.0, "spine gap must stay compact")

    def test_saas_icons_are_square_and_keep_aspect(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types

        page = _logical(
            '<mxCell id="wd" value="Workday (SAAS)" shape_type="saas_workday" style="rounded=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="240" height="40" as="geometry"/></mxCell>'
        )
        out = assign_saas_shape_types(page)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "wd")
        geom = cell.find("mxGeometry")
        self.assertEqual(geom.get("width"), geom.get("height"))
        self.assertNotIn("imageAspect=0", cell.get("style") or "")

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

    def test_domain_columns_leave_a_label_corridor(self) -> None:
        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="60" width="240" height="140" as="geometry"/></mxCell>'
            '<mxCell id="a1" value="Order Capability" style="rounded=1;" vertex="1" parent="domain_core">'
            '<mxGeometry x="20" y="50" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="60" width="240" height="140" as="geometry"/></mxCell>'
            '<mxCell id="d1" value="Store" style="rounded=1;" vertex="1" parent="domain_data">'
            '<mxGeometry x="20" y="50" width="160" height="60" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        core = by_id["domain_core"].find("mxGeometry")
        data = by_id["domain_data"].find("mxGeometry")
        gap = float(data.get("x")) - (float(core.get("x")) + float(core.get("width")))
        self.assertGreaterEqual(gap, 80.0, "domains need a corridor for labelled edges")


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
            apply_view_fixers,
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


class RealOutputRegressionTests(unittest.TestCase):
    """Reproduces concrete defects found reviewing a real generated capture (Part 5)."""

    def test_empty_corridor_shell_is_removed(self) -> None:
        """5a: an unused domain/zone container must not survive as an empty box."""
        page = _security(
            '<mxCell id="zone_public" value="Public Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="120" height="80" as="geometry"/></mxCell>'
            '<mxCell id="zone_dmz" value="Perimeter Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="40" y="60" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        ids = {c.get("id") for c in ET.fromstring(out).iter("mxCell")}
        self.assertNotIn("zone_public", ids)
        self.assertIn("zone_dmz", ids)

    def test_secrets_manager_reparented_to_private_zone(self) -> None:
        """5c: a known control misplaced by the LLM is corrected to its canonical zone."""
        from app.adapters.outbound.diagram.placement_fixers import (
            resolve_misplaced_security_controls,
        )

        page = _security(
            '<mxCell id="zone_identity" value="Identity Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="300" height="200" as="geometry"/></mxCell>'
            '<mxCell id="okta" value="Okta" shape_type="saas_okta" vertex="1" '
            'parent="zone_identity">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="secrets" value="Secrets Manager" vertex="1" parent="zone_identity">'
            '<mxGeometry x="150" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_private" value="Private App Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="800" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="app" value="App" vertex="1" parent="zone_private">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_security_controls(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["secrets"].get("parent"), "zone_private")
        # Okta (a distinct known control) is untouched — already in its canonical zone.
        self.assertEqual(by_id["okta"].get("parent"), "zone_identity")

    def test_overview_panel_never_reparented_by_security_control_fixer(self) -> None:
        """5c-regression: found via a live-generation review pass (Part 6). The
        overview panel's narrative text legitimately mentions control names like
        "WAF" and "CloudWatch" when summarizing the diagram in plain language —
        resolve_misplaced_security_controls must not keyword-match that prose and
        drag the panel into zone_dmz (which then balloons that zone's width via
        fit_containers_to_children and forces edges into long cross-canvas
        detours that cross each other)."""
        from app.adapters.outbound.diagram.placement_fixers import (
            resolve_misplaced_security_controls,
        )

        page = _security(
            '<mxCell id="zone_dmz" value="Perimeter Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" '
            'value="Employees access the portal via CloudFront and WAF for edge '
            'protection; CloudWatch monitors logs." '
            'style="fillColor=#FFF8E1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="140" width="500" height="300" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_security_controls(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["overview_panel"].get("parent"), "1")

    def test_canonical_zone_order_survives_a_backward_edge(self) -> None:
        """5b: an edge from a later-trust zone to an earlier one must not scramble order."""
        page = _security(
            '<mxCell id="zone_dmz" value="Perimeter Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="160" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_identity" value="Identity Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="160" as="geometry"/></mxCell>'
            '<mxCell id="okta" value="Okta" shape_type="saas_okta" vertex="1" '
            'parent="zone_identity">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_private" value="Private App Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="160" as="geometry"/></mxCell>'
            '<mxCell id="app" value="App" vertex="1" parent="zone_private">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            # Edge private -> identity (app fetches credentials) — under the old
            # edge-first ranking this alone would push private ahead of identity.
            '<mxCell id="e1" edge="1" parent="1" source="app" target="okta">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        dmz_x = _x(by_id["zone_dmz"])
        identity_x = _x(by_id["zone_identity"])
        private_x = _x(by_id["zone_private"])
        self.assertLess(dmz_x, identity_x, "perimeter must stay left of identity")
        self.assertLess(identity_x, private_x, "identity must stay left of private despite the backward edge")

    def test_logical_journey_edge_synthesis_skips_already_reachable_target(self) -> None:
        """5e: Okta -> Portal -> App already connects them; no duplicate Okta -> App edge."""
        page = _logical(
            '<mxCell id="emp" value="Employees" shape_type="logical_actor" vertex="1" '
            'parent="1">'
            '<mxGeometry x="40" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" vertex="1" '
            'parent="1">'
            '<mxGeometry x="200" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" shape_type="logical_channel" vertex="1" '
            'parent="1">'
            '<mxGeometry x="360" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="app" value="Portal App" shape_type="logical_channel" vertex="1" '
            'parent="1">'
            '<mxGeometry x="520" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="emp" target="ok" value="Authenticates via">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" parent="1" source="ok" target="portal" value="Redirects to">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e3" edge="1" parent="1" source="portal" target="app" value="Invokes">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        pairs = {
            (c.get("source"), c.get("target"))
            for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"
        }
        self.assertNotIn(("ok", "app"), pairs)
        self.assertIn(("ok", "portal"), pairs)
        self.assertIn(("portal", "app"), pairs)


class CorridorGapConsistencyTests(unittest.TestCase):
    """7a: a corridor width shrink elsewhere must not balloon the gap to the next one."""

    def test_gap_snapped_back_after_simulated_shrink(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import (
            enforce_corridor_gap_consistency,
        )

        # domain_core's right edge (200+300=500) + the documented 110px gap would put
        # domain_data at x=610 — simulate a post-hoc shrink leaving a stale x=900 (a
        # ~290px inflated gap), mirroring fit_containers_to_children's independent
        # width recompute with nothing re-flowing the next corridor afterward.
        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="140" width="300" height="200" as="geometry"/></mxCell>'
            '<mxCell id="a1" value="Order Capability" vertex="1" parent="domain_core">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="d1" value="Order Store" vertex="1" parent="domain_data">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
        )
        out = enforce_corridor_gap_consistency(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        core = by_id["domain_core"].find("mxGeometry")
        data = by_id["domain_data"].find("mxGeometry")
        gap = float(data.get("x")) - (float(core.get("x")) + float(core.get("width")))
        self.assertAlmostEqual(gap, 110.0, delta=1.0)

    def test_noop_when_gap_already_correct(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import (
            enforce_corridor_gap_consistency,
        )

        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="300" height="200" as="geometry"/></mxCell>'
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="570" y="140" width="200" height="200" as="geometry"/></mxCell>'
        )
        out = enforce_corridor_gap_consistency(page)
        self.assertEqual(out, page)


class ZoneColorCodingTests(unittest.TestCase):
    """7b: auto-created corridor containers must use their own zone's documented color."""

    def test_synthesized_domains_get_distinct_colors(self) -> None:
        # Call _synthesize_corridors directly (rather than the full
        # enforce_view_journey_columns pipeline) — an actor/channel leaf would be
        # swept onto the Logical spine afterward by _layout_logical_journey_spine,
        # emptying (and Part 5a then removing) whichever domain synthesis put it
        # in, which is correct pipeline behavior but not what this test targets.
        from app.adapters.outbound.diagram.placement_fixers import _synthesize_corridors

        def _is_layout_leaf(cell: ET.Element) -> bool:
            return cell.get("vertex") == "1" and cell.get("edge") != "1"

        page = _logical(
            '<mxCell id="cap" value="Order Capability" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="store" value="Customer data store" vertex="1" parent="1">'
            '<mxGeometry x="400" y="40" width="160" height="60" as="geometry"/></mxCell>'
        )
        tree = ET.fromstring(page)
        root = tree.find(".//root")
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        by_id = {c.get("id"): c for c in cells}
        created = _synthesize_corridors("logical", cells, root, by_id, _is_layout_leaf)
        created_by_id = {c.get("id"): c for c in created}
        # "Order Capability" -> Core domain (#DAE8FC/#6C8EBF); "data store" -> Data domain (#D5E8D4/#82B366)
        self.assertIn("domain_core", created_by_id)
        self.assertIn("domain_data", created_by_id)
        core_style = created_by_id["domain_core"].get("style") or ""
        data_style = created_by_id["domain_data"].get("style") or ""
        self.assertIn("fillColor=#DAE8FC", core_style)
        self.assertIn("strokeColor=#6C8EBF", core_style)
        self.assertIn("fillColor=#D5E8D4", data_style)
        self.assertIn("strokeColor=#82B366", data_style)
        self.assertNotEqual(core_style, data_style)

    def test_reparented_zone_gets_canonical_color(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import (
            resolve_misplaced_security_controls,
        )

        page = _security(
            '<mxCell id="zone_identity" value="Identity Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="300" height="200" as="geometry"/></mxCell>'
            '<mxCell id="secrets" value="Secrets Manager" vertex="1" parent="zone_identity">'
            '<mxGeometry x="150" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_security_controls(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("zone_private", by_id)
        style = by_id["zone_private"].get("style") or ""
        self.assertIn("fillColor=#DAE8FC", style)
        self.assertIn("strokeColor=#6C8EBF", style)


class DataFlowConsumeAnchorTests(unittest.TestCase):
    """8: Consume phase anchors below Store instead of ranking as a 4th L→R column."""

    def test_consume_anchored_below_store_same_column(self) -> None:
        page = _data_flow(
            '<mxCell id="phase_ingest" value="Ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="130" height="186" as="geometry"/></mxCell>'
            '<mxCell id="api_gw" value="API Gateway" vertex="1" parent="phase_ingest">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_process" value="Process" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="130" height="186" as="geometry"/></mxCell>'
            '<mxCell id="app" value="App" vertex="1" parent="phase_process">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" value="Store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="700" y="140" width="130" height="186" as="geometry"/></mxCell>'
            '<mxCell id="db" value="Database" vertex="1" parent="phase_store">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_consume" value="Consume" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="1000" y="140" width="130" height="186" as="geometry"/></mxCell>'
            '<mxCell id="dash" value="Dashboard" vertex="1" parent="phase_consume">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        store_geo = by_id["phase_store"].find("mxGeometry")
        consume_geo = by_id["phase_consume"].find("mxGeometry")
        self.assertAlmostEqual(float(store_geo.get("x")), float(consume_geo.get("x")), delta=1.0)
        self.assertGreater(
            float(consume_geo.get("y")),
            float(store_geo.get("y")) + float(store_geo.get("height")) - 1.0,
        )


class SecondReviewRoundTests(unittest.TestCase):
    """Reproduces concrete defects found reviewing a second real generated capture (Part 9)."""

    def test_overview_panel_text_never_targeted_as_portal(self) -> None:
        """9a: overview panel narrative mentioning "portal" must not become the spine target."""
        page = _logical(
            '<mxCell id="emp" value="Employees" shape_type="logical_actor" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" '
            'value="Employees access the portal via the public URL." '
            'style="fillColor=#FFF8E1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="100" width="500" height="300" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        pairs = {
            (c.get("source"), c.get("target"))
            for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"
        }
        self.assertFalse(any(t == "overview_panel" for _s, t in pairs))

    def test_llm_emitted_zone_color_gets_normalized(self) -> None:
        """9b: an LLM-emitted zone with fillColor=none must be recolored to canonical."""
        from app.adapters.outbound.diagram.placement_fixers import normalize_corridor_colors

        page = _security(
            '<mxCell id="zone_dmz" value="Perimeter" '
            'style="rounded=1;container=1;fillColor=none;strokeColor=#D6B656;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = normalize_corridor_colors(page)
        zone = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "zone_dmz")
        style = zone.get("style") or ""
        self.assertIn("fillColor=#FFF2CC", style)
        self.assertIn("strokeColor=#D6B656", style)

    def test_shelf_y_drift_snapped_back(self) -> None:
        """9c: a shelf drifted far below its anchor must snap back to SHELF_GAP."""
        from app.adapters.outbound.diagram.placement_fixers import (
            enforce_corridor_gap_consistency,
        )

        page = _logical(
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="d1" value="Store" vertex="1" parent="domain_data">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_saas" value="External SaaS" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="480" width="180" height="178" as="geometry" /></mxCell>'
            '<mxCell id="sf" value="Salesforce (SAAS)" shape_type="saas_salesforce" vertex="1" '
            'parent="domain_saas">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_corridor_gap_consistency(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        data_geo = by_id["domain_data"].find("mxGeometry")
        saas_geo = by_id["domain_saas"].find("mxGeometry")
        expected_y = float(data_geo.get("y")) + float(data_geo.get("height")) + 110.0
        self.assertAlmostEqual(float(saas_geo.get("y")), expected_y, delta=1.0)

    def test_security_external_saas_anchored_below_restricted(self) -> None:
        """9d: zone_external_saas must anchor below zone_restricted, not rank as a peer column."""
        page = _security(
            '<mxCell id="zone_restricted" value="Restricted Data" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="db" value="Aurora" vertex="1" parent="zone_restricted">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_dmz" value="Perimeter" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_external_saas" value="External SaaS Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="900" y="140" width="180" height="178" as="geometry"/></mxCell>'
            '<mxCell id="sf" value="Salesforce" shape_type="saas_salesforce" vertex="1" '
            'parent="zone_external_saas">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        restricted_geo = by_id["zone_restricted"].find("mxGeometry")
        saas_geo = by_id["zone_external_saas"].find("mxGeometry")
        self.assertAlmostEqual(
            float(restricted_geo.get("x")), float(saas_geo.get("x")), delta=1.0,
        )
        self.assertGreater(
            float(saas_geo.get("y")),
            float(restricted_geo.get("y")) + float(restricted_geo.get("height")) - 1.0,
        )

    def test_external_actor_repositioned_near_first_zone(self) -> None:
        """9e: an actor far from the pushed-out first zone must be pulled in close."""
        from app.adapters.outbound.diagram.placement_fixers import (
            enforce_corridor_gap_consistency,
        )

        page = _security(
            '<mxCell id="employees" value="Employees" shape_type="aws_users" vertex="1" parent="1">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_dmz" value="Perimeter" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="510" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="zone_private" value="Private" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="850" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="app" value="App" vertex="1" parent="zone_private">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_corridor_gap_consistency(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        # Fix 2c: the actor is now wrapped in a lightweight "actors_left" band
        # container (still parent="1", peer of the main row) instead of being
        # a bare, independently-positioned parent="1" leaf — so it's the
        # band's absolute position, not the actor's own now-container-relative
        # geometry, that must sit close to the pushed-out first zone.
        self.assertEqual(by_id["employees"].get("parent"), "actors_left")
        band_geo = by_id["actors_left"].find("mxGeometry")
        zone_geo = by_id["zone_dmz"].find("mxGeometry")
        gap = float(zone_geo.get("x")) - (float(band_geo.get("x")) + float(band_geo.get("width")))
        self.assertLess(gap, 150.0)
        self.assertGreater(gap, 0.0)

    def test_missing_adjacent_domain_edge_is_synthesized(self) -> None:
        """9f: two domains with content but no connecting edge must get one."""
        page = _logical(
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="cap" value="Order Capability" vertex="1" parent="domain_core">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="store" value="Customer data store" vertex="1" parent="domain_data">'
            '<mxGeometry x="28" y="56" width="160" height="60" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        pairs = {
            (c.get("source"), c.get("target"))
            for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"
        }
        self.assertTrue(
            ("cap", "store") in pairs or ("store", "cap") in pairs,
            f"expected a synthesized edge between domain_core and domain_data leaves, got {pairs}",
        )

    def test_phase_control_gets_a_connecting_edge_when_isolated(self) -> None:
        """9f: phase_control with content but zero edges must get one synthesized."""
        page = _data_flow(
            '<mxCell id="phase_process" value="Process" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="app" value="App" vertex="1" parent="phase_process">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_control" value="Control Plane" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="340" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="registry" value="Schema Registry" vertex="1" parent="phase_control">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        edges = [c for c in ET.fromstring(out).iter("mxCell") if c.get("edge") == "1"]
        touches_registry = any(
            e.get("source") == "registry" or e.get("target") == "registry" for e in edges
        )
        self.assertTrue(touches_registry)

    def test_saas_leaf_pulled_into_dedicated_vertical_shelf(self) -> None:
        """9g: an in-phase SaaS leaf must move to a dedicated SaaS shelf, anchored
        under phase_store — split by direction (Fix 1); Salesforce has no edges
        here so falls back to its vendor-role default ('experience' -> producer,
        see saas_default_data_flow_direction)."""
        page = _data_flow(
            '<mxCell id="phase_store" value="Store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="db" value="Database" vertex="1" parent="phase_store">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="salesforce" value="Salesforce" shape_type="saas_salesforce" vertex="1" '
            'parent="phase_store">'
            '<mxGeometry x="120" y="56" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("phase_saas_producer", by_id)
        self.assertEqual(by_id["salesforce"].get("parent"), "phase_saas_producer")
        store_geo = by_id["phase_store"].find("mxGeometry")
        saas_geo = by_id["phase_saas_producer"].find("mxGeometry")
        self.assertAlmostEqual(float(store_geo.get("x")), float(saas_geo.get("x")), delta=1.0)
        self.assertGreater(
            float(saas_geo.get("y")),
            float(store_geo.get("y")) + float(store_geo.get("height")) - 1.0,
        )


class PartTenOverviewContainerGapTests(unittest.TestCase):
    """Reproduces the two container-detection defects found in the freshest
    real capture (Part 10): overview_panel winning the primary spine-edge
    scan, and overview_panel getting reparented into a shelf it overlaps."""

    def test_primary_channel_scan_never_picks_overview_panel_over_real_portal(self) -> None:
        """10a: with a real portal leaf present BEFORE the fallback would even
        trigger, the primary `channels` list must never include overview_panel,
        and the synthesized edge must target the real portal, not the panel."""
        from app.adapters.outbound.diagram.placement_fixers import _is_logical_channel_cell

        page = _logical(
            '<mxCell id="overview_panel" '
            'value="Employees access the portal via the public URL." '
            'style="fillColor=#FFF8E1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="500" height="300" as="geometry"/></mxCell>'
            '<mxCell id="emp" value="Employees" shape_type="logical_actor" vertex="1" parent="1">'
            '<mxGeometry x="40" y="400" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta (SAAS)" shape_type="saas_okta" vertex="1" parent="1">'
            '<mxGeometry x="200" y="400" width="68" height="68" as="geometry"/></mxCell>'
            '<mxCell id="portal" value="HR Portal" shape_type="logical_channel" vertex="1" parent="1">'
            '<mxGeometry x="360" y="400" width="68" height="68" as="geometry"/></mxCell>'
        )
        root = ET.fromstring(page)
        cells = [c for c in root.iter("mxCell") if c.get("id")]
        overview = next(c for c in cells if c.get("id") == "overview_panel")
        self.assertFalse(_is_logical_channel_cell(overview))

        from app.adapters.outbound.diagram.placement_fixers import _ensure_logical_journey_edges

        _ensure_logical_journey_edges(root, cells)
        pairs = {
            (c.get("source"), c.get("target"))
            for c in root.iter("mxCell") if c.get("edge") == "1"
        }
        self.assertIn(("ok", "portal"), pairs)
        self.assertFalse(any(t == "overview_panel" for _s, t in pairs))

    def test_overview_panel_never_reparented_into_overlapping_shelf(self) -> None:
        """10b: an overview_panel styled with container=1 that geometrically
        overlaps a shelf container must stay at parent="1" through repair_xml,
        never get swallowed into the shelf as a nesting violation."""
        page = _data_flow(
            '<mxCell id="phase_store" value="Store" style="container=1;fillColor=#D5E8D4;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="db" value="Database" vertex="1" parent="phase_store">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_saas" value="SaaS" style="container=1;fillColor=#FFF2CC;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="360" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="salesforce" value="Salesforce" shape_type="saas_salesforce" vertex="1" '
            'parent="phase_saas">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="Data journey overview." '
            'style="rounded=1;container=1;fillColor=#FFF8E1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="380" width="140" height="150" as="geometry"/></mxCell>'
        )
        out, _diag = repair_xml(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        overview = by_id["overview_panel"]
        self.assertEqual(overview.get("parent"), "1")
        style = overview.get("style") or ""
        self.assertNotIn("container=1", style)


if __name__ == "__main__":
    unittest.main()
