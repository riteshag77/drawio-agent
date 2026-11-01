"""Tests for architecture docs pages and placement/line-direction fixers."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    AdrRecord,
    RaidItem,
    build_adr_page_xml,
    build_architecture_inventory,
    build_raid_page_xml,
    build_views_prompt_block,
    ensure_minimal_overview_panel,
    merge_mxfile_pages,
    normalize_diagram_views,
    page_has_overview_panel,
)
from app.adapters.outbound.llm.prompts.ir import get_corridor_ir_system_prompt
from app.adapters.outbound.diagram.edge_routing import (
    fix_edge_anchors_from_geometry,
)


def _wrap(inner: str, *, name: str = "Network") -> str:
    return (
        f'<mxfile host="embed.diagrams.net"><diagram name="{name}" id="page-1">'
        '<mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{inner}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class NormalizeViewsTests(unittest.TestCase):
    def test_default_logical(self) -> None:
        self.assertEqual(normalize_diagram_views(None), ["logical"])
        self.assertEqual(normalize_diagram_views([]), ["logical"])

    def test_multi_select_order(self) -> None:
        self.assertEqual(
            normalize_diagram_views(["security", "logical", "network"]),
            ["logical", "network", "security"],
        )

    def test_primary_prefers_network(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import (
            build_views_prompt_block,
            pick_primary_view,
        )
        self.assertEqual(pick_primary_view(["logical", "network", "security"]), "network")
        self.assertEqual(pick_primary_view(["logical", "security"]), "logical")
        # Network-only: no extra tokens in main prompt
        self.assertEqual(build_views_prompt_block(["network"]), "")
        # Multi-select: prompt focuses primary only, no multipage dump
        block = build_views_prompt_block(["logical", "network", "security"])
        self.assertIn("Network", block)
        self.assertIn("Primary IR view", block)
        self.assertNotIn("follow-up", block.lower())
        self.assertNotIn("multipage", block.lower())

    def test_merge_uses_canonical_tab_order_regardless_of_primary(self) -> None:
        """Tabs always read Logical, Network, Data flow, Security — independent
        of which view was generated as primary (Network, for content-completeness
        reasons — see pick_primary_view)."""
        from app.core.services.xml_pipeline import _merge_ordered_view_pages

        logical = _wrap(
            '<mxCell id="react" value="React" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>',
            name="Logical",
        )
        network = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="200" height="120" as="geometry"/></mxCell>',
            name="Network",
        )
        merged = _merge_ordered_view_pages(
            primary_view="network",
            primary_xml=network,
            extra_by_view={"logical": logical},
        )
        names = [d.get("name") for d in ET.fromstring(merged).findall("diagram")]
        self.assertEqual(names, ["Logical", "Network"])


class DocPagesTests(unittest.TestCase):
    def test_adr_and_raid_merge(self) -> None:
        base = _wrap(
            '<mxCell id="svc" value="API" shape_type="aws_alb" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        adr = build_adr_page_xml([AdrRecord(title="Multi-AZ", decision="Use 2 AZs")])
        raid = build_raid_page_xml([
            RaidItem(type="Risk", description="Quota", impact="Med", mitigation="Raise limit"),
        ])
        merged = merge_mxfile_pages([("Network", base), ("ADR Notes", adr), ("RAID", raid)])
        tree = ET.fromstring(merged)
        names = [d.get("name") for d in tree.findall("diagram")]
        self.assertEqual(names, ["Network", "ADR Notes", "RAID"])

    def test_merge_unwraps_nested_mxfile_pages(self) -> None:
        """LLM/MCP sometimes nest a full mxfile inside a page — must unwrap on merge."""
        from app.adapters.outbound.diagram.architecture_docs import normalize_single_page_xml

        outer = (
            '<mxfile host="embed.diagrams.net"><diagram name="Logical" id="page-1">'
            '<mxfile host="embed.diagrams.net"><diagram name="Page" id="inner">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="svc" value="API" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            "</root></mxGraphModel></diagram></mxfile>"
            "</diagram></mxfile>"
        )
        net = _wrap(
            '<mxCell id="alb" value="ALB" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        normalized = normalize_single_page_xml(outer, "Logical")
        self.assertEqual(normalized.lower().count("<mxfile"), 1)
        merged = merge_mxfile_pages([("Logical", normalized), ("Network", net)])
        self.assertEqual(merged.lower().count("<mxfile"), 1)
        tree = ET.fromstring(merged)
        self.assertEqual([d.get("name") for d in tree.findall("diagram")], ["Logical", "Network"])
        logical = tree.find("diagram")
        self.assertIsNotNone(logical)
        self.assertIsNone(logical.find("mxfile"))
        self.assertIsNotNone(logical.find("mxGraphModel"))

    def test_adr_raid_are_html_tables(self) -> None:
        adr = build_adr_page_xml([
            AdrRecord(
                title="Multi-AZ data",
                status="Accepted",
                context="Zone failure must not stop the business.",
                decision="Run databases in two zones.",
                consequences="Slightly higher cost; better uptime.",
                alternatives="Single zone with backups.",
            ),
            AdrRecord(title="Private data plane", decision="No public DB endpoints."),
        ])
        raid = build_raid_page_xml([
            RaidItem(
                type="Risk",
                description="One-region concentration",
                impact="High — full outage",
                mitigation="Add DR region later",
            ),
        ])
        adr_cell = next(c for c in ET.fromstring(adr).iter("mxCell") if c.get("id") == "adr_table")
        raid_cell = next(c for c in ET.fromstring(raid).iter("mxCell") if c.get("id") == "raid_table")
        adr_val = adr_cell.get("value") or ""
        raid_val = raid_cell.get("value") or ""
        self.assertIn("<table", adr_val)
        self.assertIn("<tr", adr_val)
        self.assertIn("<th", adr_val)
        self.assertIn("Situation", adr_val)
        self.assertIn("What we chose", adr_val)
        self.assertNotIn(" | ", adr_val)
        self.assertIn("<table", raid_val)
        self.assertIn("What this means", raid_val)
        self.assertIn("Why it matters", raid_val)
        self.assertNotIn(" | ", raid_val)

    def test_adr_not_hard_capped_at_seven(self) -> None:
        many = [AdrRecord(title=f"Decision {i}", decision=f"Choose {i}") for i in range(1, 12)]
        adr = build_adr_page_xml(many)
        val = next(c for c in ET.fromstring(adr).iter("mxCell") if c.get("id") == "adr_table").get("value") or ""
        self.assertIn("ADR-011", val)
        self.assertIn("Decision 11", val)

    def test_overview_stub_on_view_page(self) -> None:
        bare = _wrap(
            '<mxCell id="svc" value="API" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertFalse(page_has_overview_panel(bare))
        with_panel = ensure_minimal_overview_panel(bare, "Security")
        self.assertTrue(page_has_overview_panel(with_panel))
        panel = next(c for c in ET.fromstring(with_panel).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertIn("Security", panel.get("value") or "")

    def test_logical_overview_stub_collaboration_story(self) -> None:
        bare = _wrap(
            '<mxCell id="svc" value="Orders" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        with_panel = ensure_minimal_overview_panel(bare, "Logical")
        panel = next(c for c in ET.fromstring(with_panel).iter("mxCell") if c.get("id") == "overview_panel")
        val = panel.get("value") or ""
        self.assertIn("Logical architecture", val)
        self.assertIn("capabilities", val.lower())
        self.assertIn("Out of scope", val)
        self.assertNotIn("Recommended gaps", val)

    def test_security_overview_stub_plain_language(self) -> None:
        bare = _wrap(
            '<mxCell id="svc" value="WAF" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        with_panel = ensure_minimal_overview_panel(bare, "Security")
        val = next(c for c in ET.fromstring(with_panel).iter("mxCell") if c.get("id") == "overview_panel").get("value") or ""
        self.assertIn("Security & trust", val)
        self.assertIn("Outside users", val)
        self.assertIn("private area", val.lower())
        self.assertIn("Out of scope", val)
        self.assertNotIn("AuthN / TLS", val)

    def test_data_flow_overview_stub_plain_language(self) -> None:
        bare = _wrap(
            '<mxCell id="svc" value="RDS" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        with_panel = ensure_minimal_overview_panel(bare, "Data flow")
        val = next(c for c in ET.fromstring(with_panel).iter("mxCell") if c.get("id") == "overview_panel").get("value") or ""
        self.assertIn("Data journey", val)
        self.assertIn("enters from", val.lower())
        self.assertIn("personal data", val.lower())
        self.assertIn("Out of scope", val)

    def test_gap_candidates_split_by_view(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import _gap_candidates

        labels = ["ALB", "ECS", "Aurora"]
        req = "secure web app with PII and event pipeline"
        sec = _gap_candidates(req, labels, view="security")
        data = _gap_candidates(req, labels, view="data_flow")
        self.assertTrue(
            any("filter" in g.lower() or "login" in g.lower() or "vault" in g.lower() or "monitor" in g.lower()
                for g in sec),
            msg=f"security gaps={sec}",
        )
        self.assertFalse(any(g.startswith("Edge web") or "WAF" in g for g in data), msg=f"data gaps={data}")
        self.assertFalse(any("login" in g.lower() or "IdP" in g for g in data), msg=f"data gaps={data}")
        self.assertTrue(
            any(x in g for g in data for x in ("Dead-letter", "Backup", "Schema", "CDC")),
            msg=f"data gaps={data}",
        )

    def test_gap_candidates_saas_ecs_aurora_without_pii_keywords(self) -> None:
        """Enterprise SaaS + app + store must trigger resilience gaps without PCI/PII."""
        from app.adapters.outbound.diagram.architecture_docs import _gap_candidates

        labels = ["ECS Portal", "Aurora", "Workday", "SuccessFactors"]
        req = "HR portal integrates with Workday and SuccessFactors"
        sec = _gap_candidates(req, labels, view="security")
        data = _gap_candidates(req, labels, view="data_flow")
        logical = _gap_candidates(req, labels, view="logical")
        self.assertTrue(any("vault" in g.lower() or "Keys" in g for g in sec), msg=sec)
        self.assertTrue(any("monitor" in g.lower() or "Attack" in g for g in sec), msg=sec)
        self.assertTrue(any("Dead-letter" in g for g in data), msg=data)
        self.assertTrue(any("Backup" in g for g in data), msg=data)
        self.assertTrue(any("CDC" in g or "replica" in g.lower() for g in data), msg=data)
        self.assertTrue(any("Schema" in g for g in data), msg=data)
        self.assertTrue(any("Cache" in g for g in logical), msg=logical)

    def test_network_only_extra_view_context_empty(self) -> None:
        self.assertEqual(build_views_prompt_block(["network"]), "")
        block = build_views_prompt_block(["logical"])
        self.assertIn("Logical", block)
        self.assertIn("Primary IR view", block)

    def test_surgical_edit_gate_detects_success(self) -> None:
        from types import SimpleNamespace

        from app.core.services.xml_pipeline import _was_successful_surgical_edit

        logger = SimpleNamespace(entries=[
            SimpleNamespace(step="surgical_edit", level="success", message="Applied 2 surgical ops"),
        ])
        self.assertTrue(_was_successful_surgical_edit(logger))
        self.assertFalse(_was_successful_surgical_edit(SimpleNamespace(entries=[])))
        self.assertFalse(_was_successful_surgical_edit(None))


class ViewPackQualityTests(unittest.TestCase):
    def test_ir_packs_are_self_contained_lenses(self) -> None:
        logical = get_corridor_ir_system_prompt("logical", platform="aws")
        self.assertIn("Logical", logical)
        self.assertIn("Invokes", logical)
        self.assertIn("Application/Solution Architect", logical)
        self.assertIn("domain_experience", logical)
        self.assertIn("logical_", logical.lower())
        self.assertNotIn("Canonical Container Hierarchy", logical)
        self.assertNotIn("Cross-Cutting Groups", logical)

        security = get_corridor_ir_system_prompt("security", platform="aws")
        self.assertIn("zone_dmz", security)
        self.assertIn("Security/Trust Architect", security)
        self.assertIn("zone_external_saas", security)
        self.assertIn("25+", security)

        data_flow = get_corridor_ir_system_prompt("data_flow", platform="aws")
        self.assertIn("Integration/Data-Flow Architect", data_flow)
        self.assertIn("JSON:", data_flow)
        self.assertIn("phase_ingest", data_flow)

    def test_architecture_inventory_services_and_flows(self) -> None:
        xml = _wrap(
            '<mxCell id="alb" value="ALB" shape_type="aws_alb" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ecs" value="ECS" shape_type="aws_ecs" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="rds" value="Aurora" shape_type="aws_rds" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="step_badge_1" value="1" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="24" height="24" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="alb" target="ecs" value="HTTPS">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" parent="1" source="ecs" target="rds">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        inv = build_architecture_inventory(
            xml,
            request_text="HA web app with WAF, private subnets, CDN, cache, CloudWatch",
        )
        self.assertIn("ALB [edge]", inv)
        self.assertIn("shape_type=aws_alb", inv)
        self.assertIn("ECS [app]", inv)
        self.assertIn("Aurora [data]", inv)
        self.assertNotIn("- 1 [", inv)  # badge noise filtered
        self.assertIn("ALB → ECS (HTTPS)", inv)
        self.assertIn("ECS → Aurora", inv)
        self.assertIn("Control hints", inv)
        self.assertIn("Capability hints", inv)
        self.assertIn("Payload hints", inv)
        self.assertIn("Key flows", inv)
        self.assertIn("→", inv)


class ViewEnrichmentFixerTests(unittest.TestCase):
    def test_inject_shape_types_from_capability_labels(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import inject_shape_types_from_primary

        primary = _wrap(
            '<mxCell id="waf" value="WAF" shape_type="aws_waf" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="alb" value="ALB" shape_type="aws_application_load_balancer" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ec2" value="EC2" shape_type="aws_ec2" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        page = _wrap(
            '<mxCell id="shield" value="Edge shield" style="shape=ellipse;fillColor=#F5F5F5;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dist" value="Traffic distributor" style="shape=rectangle;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="app" value="Web application" style="shape=rectangle;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="Overview" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="40" width="300" height="120" as="geometry"/></mxCell>'
        )
        out = inject_shape_types_from_primary(page, primary)
        tree = ET.fromstring(out)
        by_id = {c.get("id"): c for c in tree.iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["shield"].get("shape_type"), "aws_waf")
        self.assertEqual(by_id["dist"].get("shape_type"), "aws_application_load_balancer")
        self.assertEqual(by_id["app"].get("shape_type"), "aws_ec2")
        self.assertNotIn("shape=ellipse", by_id["shield"].get("style") or "")

    def test_inject_shape_types_never_touches_an_external_actor(self) -> None:
        """Regression: a live capture showed Security/Data-flow's "Users"
        external-actor cell (styled correctly and completely by
        theme.actor_style() at compile time, with no shape_type at all — it
        never needs MCP resolution) end up with resIcon=mxgraph.aws4.lambda.
        Root cause: this function assigns *any* shape_type-less, non-container
        vertex a shape_type by inferred tier when no direct label match is
        found — and _infer_view_leaf_tier("Users") falls through to the "app"
        (compute) tier default, so the tier fallback grabbed whatever compute
        entry the primary inventory had (Lambda here). Logical already avoids
        this because assign_logical_shape_types runs first and gives its own
        actor a real shape_type ("logical_actor") before this function ever
        sees it — Security/Data-flow have no equivalent, so the actor must be
        excluded here directly instead."""
        from app.adapters.outbound.diagram.architecture_docs import inject_shape_types_from_primary

        primary = _wrap(
            '<mxCell id="lambda" value="Checkout Fn" shape_type="aws_lambda" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        page = _wrap(
            '<mxCell id="users" value="Users" '
            'style="shape=mxgraph.aws4.user;sketch=0;fillColor=#232F3E;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>',
            name="Security",
        )
        out = inject_shape_types_from_primary(page, primary)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "users")
        self.assertIsNone(cell.get("shape_type"))
        self.assertNotIn("resIcon=mxgraph.aws4.lambda", cell.get("style") or "")
        self.assertIn("shape=mxgraph.aws4.user", cell.get("style") or "")

    def test_inject_shape_types_never_touches_a_custom_role_actor_label(self) -> None:
        """Same bug class as the "Users"/Lambda regression above, but with a
        custom role label ("HR Managers") the old exact-whole-label
        _is_actor_label() check didn't recognize — a live capture showed it
        fall through to the tier fallback and get stamped aws_ec2/
        aws_api_gateway. _is_actor_label now also matches on the label's last
        word against a broader set of role nouns (manager, admin, rep, …)."""
        from app.adapters.outbound.diagram.architecture_docs import inject_shape_types_from_primary

        primary = _wrap(
            '<mxCell id="ec2" value="Checkout Service" shape_type="aws_ec2" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        page = _wrap(
            '<mxCell id="hr_mgrs" value="HR Managers" '
            'style="shape=mxgraph.aws4.user;sketch=0;fillColor=#232F3E;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>',
            name="Security",
        )
        out = inject_shape_types_from_primary(page, primary)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "hr_mgrs")
        self.assertIsNone(cell.get("shape_type"))
        self.assertNotIn("aws_ec2", cell.get("style") or "")

    def test_inject_shape_types_never_touches_a_data_flow_producer_actor(self) -> None:
        """Same bug class again, this time with Data-flow's own external-actor
        vocabulary ("Data Producers"/"Data Consumers" — see
        config/xml_gen/ir/data_flow.txt's phase_saas_producer/
        phase_saas_consumer section) rather than a human role noun. A live
        capture showed a "Data Producers" actor cell land on an unrelated
        aws_step_functions icon from the Network inventory's tier fallback.
        _is_actor_label's noun set now also covers producer/consumer/source/
        sink/publisher/subscriber/provider."""
        from app.adapters.outbound.diagram.architecture_docs import inject_shape_types_from_primary

        primary = _wrap(
            '<mxCell id="sfn" value="Order Pipeline" shape_type="aws_step_functions" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        page = _wrap(
            '<mxCell id="data_producers" value="Data Producers" '
            'style="shape=mxgraph.aws4.productIcon;sketch=0;fillColor=#232F3E;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>',
            name="Data Flow",
        )
        out = inject_shape_types_from_primary(page, primary)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "data_producers")
        self.assertIsNone(cell.get("shape_type"))
        self.assertNotIn("step_functions", cell.get("style") or "")

    def test_logical_assigns_mcp_shape_types(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types

        page = _wrap(
            '<mxCell id="ord" value="Orders capability" shape_type="aws_ecs" '
            'style="shape=rectangle;fillColor=#F5F5F5;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ds" value="Customer data store" shape_type="aws_rds" '
            'style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="user" value="Customer" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="60" height="80" as="geometry"/></mxCell>'
        )
        out = assign_logical_shape_types(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        # Intentional aws_* keys are preserved for MCP icon resolve.
        self.assertEqual(by_id["ord"].get("shape_type"), "aws_ecs")
        self.assertEqual(by_id["ds"].get("shape_type"), "aws_rds")
        self.assertEqual(by_id["user"].get("shape_type"), "logical_actor")
        self.assertIn("mxgraph.aws4.users", by_id["user"].get("style") or "")
        # People icons use AWS4 users on purpose (umlActor renders empty in embed).
        self.assertNotIn("resIcon=mxgraph.aws4.lambda", out)

    def test_logical_role_edge_cases(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import (
            _logical_leaf_role,
            assign_logical_shape_types,
        )

        self.assertEqual(_logical_leaf_role("User store"), "datastore")
        self.assertEqual(_logical_leaf_role("Customer data store"), "datastore")
        self.assertEqual(_logical_leaf_role("Admin portal"), "channel")
        self.assertEqual(_logical_leaf_role("Mobile app"), "channel")
        self.assertEqual(_logical_leaf_role("Application"), "channel")
        self.assertEqual(_logical_leaf_role("HR Application"), "channel")
        self.assertEqual(_logical_leaf_role("Okta"), "saas")
        self.assertEqual(_logical_leaf_role("Auth0 IdP"), "saas")
        self.assertEqual(_logical_leaf_role("Orders BFF"), "api")
        self.assertEqual(_logical_leaf_role("Checkout API"), "api")
        self.assertEqual(_logical_leaf_role("Customer"), "actor")
        self.assertEqual(_logical_leaf_role("Identity"), "capability")
        self.assertEqual(_logical_leaf_role("User management"), "capability")

        page = _wrap(
            '<mxCell id="us" value="User store" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ap" value="Admin portal" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="120" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="80" height="60" as="geometry"/></mxCell>'
        )
        out = assign_logical_shape_types(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["us"].get("shape_type"), "logical_datastore")
        self.assertEqual(by_id["ap"].get("shape_type"), "logical_channel")
        self.assertEqual(by_id["ok"].get("shape_type"), "logical_saas")

    def test_inject_logical_capability_and_data_store_labels(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import inject_shape_types_from_primary

        primary = _wrap(
            '<mxCell id="alb" value="ALB" shape_type="aws_application_load_balancer" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ecs" value="ECS" shape_type="aws_ecs" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="rds" value="Aurora" shape_type="aws_rds" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        page = _wrap(
            '<mxCell id="ch" value="Customer channel" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ord" value="Orders capability" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ds" value="Customer data store" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = inject_shape_types_from_primary(page, primary)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["ch"].get("shape_type"), "aws_application_load_balancer")
        self.assertEqual(by_id["ord"].get("shape_type"), "aws_ecs")
        self.assertEqual(by_id["ds"].get("shape_type"), "aws_rds")
        self.assertNotIn("shape=ellipse", by_id["ch"].get("style") or "")


class EdgeAnchorTests(unittest.TestCase):
    def test_leftward_edge_gets_left_exit(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" '
            'style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(xml)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        style = edge.get("style") or ""
        self.assertIn("exitX=0", style)
        self.assertIn("entryX=1", style)


if __name__ == "__main__":
    unittest.main()
