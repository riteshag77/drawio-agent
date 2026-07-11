"""Regression: structural layout rules (AZ stack, bands, clamps, hard validation)."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.diagram_classify import is_az_container, is_subnet_container
from app.adapters.outbound.diagram.draftsman import _prepare_architecture, generate_xml
from app.adapters.outbound.diagram.layout import absolute_box, build_children_map, build_layout
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.adapters.outbound.diagram.layout_validate import assert_layout_valid, validate_layout_structure
from app.adapters.outbound.diagram.service_bands import layout_bands
from app.adapters.outbound.diagram.topology_repair import ensure_az_hierarchy
from app.domain.entities import ArchitectureDiagram, Edge, Node


def _flat_multi_az_arch() -> ArchitectureDiagram:
    """VPC with AZ-suffixed subnets but no AZ containers — repair must insert them."""
    return ArchitectureDiagram(
        title="architecture_aws_3_regression",
        nodes=[
            Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="region", label="Region", shape_type="aws_region", is_container=True, parent_id="cloud"),
            Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="region"),
            Node(id="pub_a", label="Public Subnet A", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
            Node(id="app_a", label="App Subnet A", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
            Node(id="data_a", label="Data Subnet A", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
            Node(id="pub_b", label="Public Subnet B", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
            Node(id="app_b", label="App Subnet B", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
            Node(id="data_b", label="Data Subnet B", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
            Node(id="eks_a", label="EKS Cluster", shape_type="aws_eks", is_container=True, parent_id="app_a"),
            Node(id="svc1", label="Orders", shape_type="aws_fargate", parent_id="eks_a"),
            Node(id="svc2", label="Payments", shape_type="aws_fargate", parent_id="eks_a"),
            Node(id="svc3", label="Users", shape_type="aws_fargate", parent_id="eks_a"),
            Node(id="alb_a", label="ALB", shape_type="aws_alb", parent_id="pub_a"),
            Node(id="db_a", label="Aurora", shape_type="aws_aurora", parent_id="data_a"),
            Node(id="waf", label="WAF", shape_type="aws_waf", parent_id="cloud"),
            Node(id="cw", label="CloudWatch", shape_type="aws_cloudwatch", parent_id="cloud"),
            Node(id="cf", label="CloudFront", shape_type="aws_cloudfront", parent_id="cloud"),
        ],
        edges=[
            Edge(source_id="cf", target_id="alb_a", label="HTTPS"),
            Edge(source_id="alb_a", target_id="svc1", label="HTTP"),
            Edge(source_id="svc1", target_id="db_a", label="SQL"),
        ],
        suggestions=[],
    )


class StructuralLayoutRegressionTests(unittest.TestCase):
    def test_ensure_az_wraps_flat_multi_az_subnets(self) -> None:
        repaired = ensure_az_hierarchy(_flat_multi_az_arch())
        nodes = {n.id: n for n in repaired.nodes}
        azs = [n for n in repaired.nodes if is_az_container(n)]
        self.assertGreaterEqual(len(azs), 2)
        for sid in ("pub_a", "app_a", "data_a", "pub_b", "app_b", "data_b"):
            parent = nodes[sid].parent_id
            self.assertTrue(parent and is_az_container(nodes[parent]), f"{sid} parent={parent}")

    def test_subnets_stack_vertically_inside_az(self) -> None:
        prep = _prepare_architecture(_flat_multi_az_arch(), "aws")
        cm, roots, nbi = build_children_map(prep.nodes)
        layout = build_layout(nbi, cm, roots)
        # Find AZ A subnets
        az_a = next(
            n for n in prep.nodes
            if is_az_container(n) and (n.id.endswith("_a") or "az_a" in n.id)
        )
        subnets = sorted(
            [cid for cid in cm.get(az_a.id, []) if is_subnet_container(nbi[cid])],
            key=lambda cid: layout[cid]["y"],
        )
        self.assertGreaterEqual(len(subnets), 3)
        for a, b in zip(subnets, subnets[1:]):
            self.assertLess(layout[a]["y"], layout[b]["y"])
            self.assertEqual(layout[a]["x"], layout[b]["x"])
            # Content-sized — not equal to full AZ height
            self.assertLess(layout[a]["h"], layout[az_a.id]["h"])

    def test_workload_smaller_than_subnet(self) -> None:
        prep = _prepare_architecture(_flat_multi_az_arch(), "aws")
        cm, roots, nbi = build_children_map(prep.nodes)
        layout = build_layout(nbi, cm, roots)
        self.assertIn("eks_a", layout)
        self.assertIn("app_a", layout)
        self.assertLess(layout["eks_a"]["w"], layout["app_a"]["w"])
        self.assertLess(layout["eks_a"]["h"], layout["app_a"]["h"])

    def test_igw_y_clamped_to_half_icon(self) -> None:
        prep = _prepare_architecture(_flat_multi_az_arch(), "aws")
        cm, roots, nbi = build_children_map(prep.nodes)
        layout = build_layout(nbi, cm, roots)
        igw_id = next(nid for nid, n in nbi.items() if "internet_gateway" in (n.shape_type or ""))
        h = layout[igw_id]["h"]
        self.assertGreaterEqual(layout[igw_id]["y"], -h // 2)

    def test_bands_bordered_and_inside_cloud(self) -> None:
        prep = _prepare_architecture(_flat_multi_az_arch(), "aws")
        cm, roots, nbi = build_children_map(prep.nodes)
        layout = build_layout(nbi, cm, roots)
        from app.adapters.outbound.diagram.layout import absolute_layout_bounds
        mb = absolute_layout_bounds(layout, nbi, cm, roots)
        layout_bands(layout, nbi, cm, main_bounds=mb)
        cloud = layout["cloud"]
        for band_id in ("band_security", "band_logging"):
            self.assertIn(band_id, layout)
            b = layout[band_id]
            self.assertEqual(nbi[band_id].parent_id, "cloud")
            self.assertLessEqual(b["x"] + b["w"], cloud["w"] + 1)
            self.assertLessEqual(b["y"] + b["h"], cloud["h"] + 1)
        xml = generate_xml(_flat_multi_az_arch(), platform="aws")
        self.assertIn("strokeColor=#DD344C", xml)
        self.assertNotIn("strokeColor=none;fillColor=none", xml)

    def test_validation_reports_zero_violations(self) -> None:
        layout_settings.cache_clear()
        prep = _prepare_architecture(_flat_multi_az_arch(), "aws")
        cm, roots, nbi = build_children_map(prep.nodes)
        layout = build_layout(nbi, cm, roots)
        from app.adapters.outbound.diagram.layout import absolute_layout_bounds
        mb = absolute_layout_bounds(layout, nbi, cm, roots)
        layout_bands(layout, nbi, cm, main_bounds=mb)
        _, _, max_x, max_y = absolute_layout_bounds(
            layout, nbi, cm, roots + [b for b in nbi if b.startswith("band_")],
        )
        s = layout_settings()
        page_w = max(max_x + s.page_margin, s.default_page_w)
        page_h = max(max_y + s.page_margin, s.default_page_h)
        violations = validate_layout_structure(
            prep, layout, nbi, cm, page_w=page_w, page_h=page_h,
        )
        self.assertEqual(violations, [], [v.message for v in violations])
        assert_layout_valid(prep, layout, nbi, cm, page_w=page_w, page_h=page_h)
        # Full pipeline must also succeed under blocking gates
        xml = generate_xml(_flat_multi_az_arch(), platform="aws")
        self.assertIn("<mxGraphModel", xml)


if __name__ == "__main__":
    unittest.main()
