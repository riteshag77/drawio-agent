"""diagnose_xml's edge-box/edge-edge overlap checks, and the obstacle-avoiding
router (fix_edge_anchors_from_geometry, now extended to Network) that resolves
most of what they find.

These two categories were previously only surfaced through validate_layout()'s
observational, non-gating log output (its own docstring: "logged for
visibility rather than acted on") — real capture-driven testing this session
showed why: the underlying geometry model (_edge_polyline) connects source/
target anchors with a straight line when an edge has no explicit waypoints,
which is accurate for the waypoint-bend-count/anchor-agreement checks that
still use it, but wrong for "does this edge's rendered path cross a box" —
compiler-emitted edges never set exit/entry anchors (compiler/theme.py's
flow_style()), so that straight line runs center-to-center and can cross any
box sitting geometrically between two connected, correctly-placed nodes.

_edge_routed_polyline fixes this for exactly these two checks: it synthesizes
the same single-bend "Z" jog orthogonalEdgeStyle renders by default (collapses
to a straight line automatically when source/target are already aligned), and
defers to explicit waypoints/anchors when a repair pass already routed the
edge for real. Verified against every corridor/network compiler fixture in
tests/test_corridor_compiler.py and tests/test_network_compiler.py: all
false positives from the naive straight-line model are gone post-repair,
except one genuine, documented residual (below).
"""

from __future__ import annotations

import importlib
import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_network_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import Leaf, Flow
from app.domain.ir.network import AvailabilityZone, CloudBoundary, NetworkIR, Region, Subnet, Vpc


def _box_overlap_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "passes through non-endpoint node" in w]


def _parallel_overlap_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "overlap" in w and "segment" in w]


class DiagnoseXmlEdgeBoxOverlapTests(unittest.TestCase):
    def test_flags_edge_crossing_an_unrelated_sibling(self) -> None:
        """Reuses test_network_compiler.py's realistic_ir() fixture — its
        alb-to-ecs1 edge crosses the sibling 'lambda1' on raw, unrouted
        compiler output (confirmed empirically), the exact real shape that
        exposed why the naive straight-line model was unsafe to gate on."""
        network_compiler_tests = importlib.import_module("tests.test_network_compiler")
        xml = compile_network_ir(network_compiler_tests.realistic_ir())
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        box_warnings = _box_overlap_warnings(diag.warnings)
        self.assertTrue(box_warnings, f"expected a box-overlap warning; got {diag.warnings}")
        self.assertTrue(
            any("lambda1" in w for w in box_warnings),
            f"expected the blocked sibling 'lambda1' to be named; got {box_warnings}",
        )

    def test_no_false_positive_on_clean_aligned_edge(self) -> None:
        """A straight, unobstructed edge between two aligned nodes must never
        be flagged — this is the case the old straight-line model got right
        and the fix must not regress."""
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS Cloud",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                    id="vpc",
                    zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                        Subnet(id="app", tier="app", leaves=[Leaf(id="svc", shape_type="aws_ecs", label="Service")]),
                        Subnet(id="data", tier="data", leaves=[Leaf(id="db", shape_type="aws_rds", label="DB")]),
                    ])],
                )])],
            ),
            flows=[Flow(source="svc", target="db")],
        )
        xml = compile_network_ir(ir)
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        diag = diagnose_xml(repaired)
        self.assertEqual(_box_overlap_warnings(diag.warnings), [])
        self.assertEqual(_parallel_overlap_warnings(diag.warnings), [])


class ObstacleRouterExtendedToNetworkTests(unittest.TestCase):
    """fix_edge_anchors_from_geometry's obstacle-avoiding router
    (_route_logical_edge) previously only ran for Logical/Security/Data-flow
    (LIBAVOID_FOCUSED_VIEWS's docstring: Network was designed to rely on
    libavoid alone). Extended to Network too this session, as a local
    fallback for the `local` renderer / libavoid-disabled path — Network's
    compiler never routes edges around obstacles itself (compiler/network.py
    has no such logic), so without this, Network edges crossing unrelated
    boxes had no repair-layer fix at all."""

    def test_network_edges_get_routed_around_obstacles(self) -> None:
        # A realistic-shaped topology (adapted from test_network_compiler.py's
        # realistic_ir): NAT gateways and app-tier services in the same
        # subnet, with cross-tier flows likely to cross a sibling if unrouted.
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS Cloud",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                    id="vpc",
                    zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                        Subnet(id="public", tier="public", leaves=[Leaf(id="nat", shape_type="aws_nat_gateway", label="NAT")]),
                        Subnet(id="app", tier="app", leaves=[
                            Leaf(id="svc1", shape_type="aws_ecs", label="Service 1"),
                            Leaf(id="svc2", shape_type="aws_lambda", label="Service 2"),
                        ]),
                        Subnet(id="data", tier="data", leaves=[Leaf(id="db", shape_type="aws_rds", label="DB")]),
                    ])],
                )])],
            ),
            flows=[
                Flow(source="nat", target="svc1"), Flow(source="nat", target="svc2"),
                Flow(source="svc1", target="db"), Flow(source="svc2", target="db"),
            ],
        )
        xml = compile_network_ir(ir)
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        # Confirm the router actually ran on this page (waypoints present on
        # at least one edge) rather than the test passing only because
        # nothing needed routing in the first place.
        tree = ET.fromstring(repaired)
        has_waypoints = any(
            c.get("edge") == "1" and c.find(".//Array[@as='points']") is not None
            for c in tree.iter("mxCell")
        )
        self.assertTrue(has_waypoints, "expected fix_edge_anchors_from_geometry to route at least one Network edge")


class KnownResidualTests(unittest.TestCase):
    """Pinned, documented residual — not a regression. `_route_logical_edge`
    is a best-effort candidate search over waypoints at a *fixed* entry face;
    it does not try alternate entry faces when the natural one is
    structurally blocked by a sibling. `test_network_compiler.py`'s own
    `realistic_ir()` fixture (NAT gateways + app-tier services sharing a
    subnet) reproduces exactly this: an edge entering `ecs1` from its right
    face must cross `lambda1`, which sits directly between the subnet's right
    edge and `ecs1`. Fixing this needs dynamic entry-face selection in the
    router itself — a genuinely larger change, deliberately out of scope this
    session. Pinned here (not asserted as "must be zero") so a future fix to
    the router is a visible, deliberate change to this test, not a silent
    behavior shift — and so this residual is never confused with the false
    positives the routed-polyline fix above already eliminated.
    """

    def test_realistic_network_fixture_has_one_documented_residual(self) -> None:
        network_compiler_tests = importlib.import_module("tests.test_network_compiler")
        xml = compile_network_ir(network_compiler_tests.realistic_ir())
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        diag = diagnose_xml(repaired)
        box_warnings = _box_overlap_warnings(diag.warnings)
        self.assertEqual(
            len(box_warnings), 1,
            f"expected exactly the one known residual, got: {box_warnings}",
        )
        self.assertIn("lambda1", box_warnings[0])


if __name__ == "__main__":
    unittest.main()
