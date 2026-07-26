"""Deterministic hierarchy/placement fixers (hubs, edge services, LB, anchors)."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_diagnostics import (
    _RESERVED_IDS,
    _cell_geometry,
    _is_container_cell,
    _is_cross_cutting_group_cell,
    _parse_docroot,
    _serialize_docroot,
    absolute_position,
)

# Connectivity hubs — must sit at Cloud level beside Edge (not inside one VPC row).
_HUB_SHAPE_TYPES = {
    "aws_transit_gateway", "transit_gateway", "aws_vpn_gateway", "vpn_gateway",
    "aws_direct_connect", "direct_connect", "aws_privatelink", "privatelink",
    "aws_vpc_lattice", "vpc_lattice",
    "azure_virtual_wan", "azure_vpn_gateway", "azure_expressroute",
    "azure_express_route", "expressroute",
    "gcp_cloud_interconnect", "gcp_interconnect", "gcp_ha_vpn", "gcp_cloud_vpn",
    "gcp_network_connectivity_center", "ncc_hub",
}
_HUB_LABEL_HINTS = (
    "transit gateway", "vpn gateway", "direct connect", "privatelink",
    "private link", "expressroute", "express route", "virtual wan",
    "cloud interconnect", "ha vpn", "cloud vpn", "ncc hub",
)

_EDGE_SERVICE_SHAPE_TYPES = {
    "aws_cloudfront", "cloudfront", "aws_route_53", "aws_route53", "route53", "route_53",
    "aws_waf", "waf", "aws_shield", "shield", "aws_global_accelerator", "global_accelerator",
    "azure_front_door", "front_door", "azure_cdn", "azure_traffic_manager",
    "gcp_cloud_cdn", "cloud_cdn", "gcp_cloud_armor", "cloud_armor", "gcp_cloud_dns",
}
_EDGE_LABEL_HINTS = (
    "cloudfront", "route 53", "route53", "waf", "shield", "global accelerator",
    "front door", "cdn", "traffic manager", "cloud armor", "cloud dns",
)

_LB_SHAPE_TYPES = {
    "aws_alb", "alb", "aws_nlb", "nlb", "aws_elb", "elb", "aws_api_gateway", "api_gateway",
    "aws_application_load_balancer", "aws_network_load_balancer",
    "azure_application_gateway", "application_gateway", "azure_load_balancer",
    "azure_api_management", "api_management",
    "gcp_cloud_load_balancing", "cloud_load_balancing", "gcp_api_gateway",
}
_LB_LABEL_HINTS = (
    "application load balancer", "network load balancer", "load balancer",
    "alb", "nlb", "api gateway", "application gateway", "api management",
)

_ZONE_KEYWORDS = {"availability", "zone", "az"}
_VPC_KEYWORDS = {"vpc", "vnet", "virtual network", "virtualnet"}
_CLOUD_KEYWORDS = {"cloud", "aws", "azure", "gcp", "google cloud"}
_EDGE_LAYER_KEYWORDS = {"edge", "global"}
_SUBNET_KEYWORDS = {"subnet", "public", "private"}


def _plain_label(cell: ET.Element) -> str:
    value = (cell.get("value") or "").strip().lower()
    return re.sub(r"<[^>]+>", " ", value).strip()


def _shape(cell: ET.Element) -> str:
    return (cell.get("shape_type") or "").strip().lower()


def _label_matches(cell: ET.Element, hints: tuple[str, ...]) -> bool:
    label = _plain_label(cell)
    return any(h in label for h in hints)


def is_connectivity_hub_cell(cell: ET.Element) -> bool:
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    if _shape(cell) in _HUB_SHAPE_TYPES:
        return True
    return _label_matches(cell, _HUB_LABEL_HINTS)


def is_edge_service_cell(cell: ET.Element) -> bool:
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    if is_connectivity_hub_cell(cell):
        return False
    if _shape(cell) in _EDGE_SERVICE_SHAPE_TYPES:
        return True
    return _label_matches(cell, _EDGE_LABEL_HINTS)


def is_lb_gateway_cell(cell: ET.Element) -> bool:
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    if _shape(cell) in _LB_SHAPE_TYPES:
        return True
    label = _plain_label(cell)
    # Avoid "NAT Gateway" as VPC-sibling LB — it belongs in public subnet
    if "nat" in label:
        return False
    return _label_matches(cell, _LB_LABEL_HINTS)


def _first_word(value: str) -> str:
    parts = value.split()
    return parts[0] if parts else ""


def _is_zone_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    value = _plain_label(cell)
    return any(k in value for k in ("availability zone", " az", "zone ")) or value.startswith("az")


def _is_vpc_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    value = _plain_label(cell)
    fw = _first_word(value)
    return fw in {"vpc", "vnet"} or "virtual network" in value


def _is_cloud_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    value = _plain_label(cell)
    return any(k in value for k in _CLOUD_KEYWORDS) and not _is_vpc_container(cell)


def _is_edge_layer_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    value = _plain_label(cell)
    return value.startswith("edge") or "global layer" in value or value == "global"


def _is_subnet_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    value = _plain_label(cell)
    return "subnet" in value


def _find_ancestor(
    cell: ET.Element,
    by_id: dict[str, ET.Element],
    predicate,
) -> ET.Element | None:
    parent_id, seen = cell.get("parent"), set()
    while parent_id and parent_id not in _RESERVED_IDS and parent_id in by_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id[parent_id]
        if predicate(parent):
            return parent
        parent_id = parent.get("parent")
    return None


def _set_parent_relative(
    cell: ET.Element,
    new_parent: ET.Element,
    by_id: dict[str, ET.Element],
    *,
    x: float,
    y: float,
) -> None:
    cell.set("parent", new_parent.get("id") or "1")
    geom = cell.find("mxGeometry")
    if geom is not None:
        geom.set("x", str(x))
        geom.set("y", str(y))


def resolve_misplaced_connectivity_hubs(xml: str) -> str:
    """Hoist TGW/VPN/DX/PrivateLink-style hubs to Cloud level beside Edge."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    hubs = [c for c in cells if is_connectivity_hub_cell(c)]
    if not hubs:
        return xml

    clouds = [c for c in cells if _is_cloud_container(c)]
    if not clouds:
        return xml

    changed = False
    for hub in hubs:
        cloud = _find_ancestor(hub, by_id, _is_cloud_container)
        if cloud is None:
            cloud = clouds[0]
        parent = by_id.get(hub.get("parent") or "")
        # Already under Cloud and not under VPC/subnet/zone — OK
        if parent is not None and parent.get("id") == cloud.get("id"):
            continue
        if parent is not None and _is_edge_layer_container(parent):
            continue
        # Misplaced if under VPC/zone/subnet/group or deeper
        if parent is None or parent.get("id") == "1":
            # Root-level hub: pull into cloud
            pass
        elif not (
            _is_vpc_container(parent)
            or _is_zone_container(parent)
            or _is_subnet_container(parent)
            or _is_cross_cutting_group_cell(parent)
            or _find_ancestor(hub, by_id, _is_vpc_container)
        ):
            if _find_ancestor(hub, by_id, _is_cloud_container) and not _find_ancestor(
                hub, by_id, _is_vpc_container
            ):
                continue

        abs_x, abs_y = absolute_position(hub, by_id)
        cloud_abs = absolute_position(cloud, by_id)
        edge = next(
            (c for c in cells if _is_edge_layer_container(c) and c.get("parent") == cloud.get("id")),
            None,
        )
        if edge is not None:
            eg = _cell_geometry(edge) or (40.0, 40.0, 200.0, 120.0)
            hub_geo = _cell_geometry(hub) or (0.0, 0.0, 78.0, 78.0)
            rel_x = eg[0] + eg[2] + 40.0
            rel_y = eg[1]
            # Keep hub inside Cloud bounds when possible
            cg = _cell_geometry(cloud)
            if cg is not None:
                max_x = max(40.0, cg[2] - hub_geo[2] - 24.0)
                max_y = max(48.0, cg[3] - hub_geo[3] - 24.0)
                rel_x = min(rel_x, max_x)
                rel_y = min(max(48.0, rel_y), max_y)
        else:
            rel_x = max(40.0, abs_x - cloud_abs[0])
            rel_y = max(48.0, abs_y - cloud_abs[1])
        _set_parent_relative(hub, cloud, by_id, x=rel_x, y=rel_y)
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_misplaced_edge_services(xml: str) -> str:
    """Ensure CDN/DNS/WAF-style edge leaves sit under an Edge layer inside Cloud."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    edge_leaves = [c for c in cells if is_edge_service_cell(c)]
    if not edge_leaves:
        return xml

    clouds = [c for c in cells if _is_cloud_container(c)]
    if not clouds:
        return xml

    changed = False
    for leaf in edge_leaves:
        cloud = _find_ancestor(leaf, by_id, _is_cloud_container)
        if cloud is None:
            cloud = clouds[0]
        if _find_ancestor(leaf, by_id, _is_edge_layer_container) is not None:
            continue
        # Already cloud-level sibling of edge (not under vpc) — leave
        parent = by_id.get(leaf.get("parent") or "")
        if parent is not None and parent.get("id") == cloud.get("id"):
            continue
        if _find_ancestor(leaf, by_id, _is_vpc_container) is None and _find_ancestor(
            leaf, by_id, _is_subnet_container
        ) is None:
            if parent is not None and _is_cross_cutting_group_cell(parent):
                pass  # hoist out of Security etc.
            else:
                continue

        edge = next(
            (c for c in cells if _is_edge_layer_container(c) and c.get("parent") == cloud.get("id")),
            None,
        )
        if edge is None:
            # Create a minimal Edge layer under cloud, beside Region when present
            edge_id = "edge_layer_auto"
            n = 1
            while edge_id in by_id:
                n += 1
                edge_id = f"edge_layer_auto_{n}"
            edge = ET.Element("mxCell")
            edge.set("id", edge_id)
            edge.set("value", "Edge / Global Layer")
            edge.set(
                "style",
                "rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#E8F0FE;strokeColor=#4285F4;"
                "verticalAlign=top;align=left;spacingLeft=12;spacingTop=8;fontStyle=1;"
                "container=1;collapsible=0;dropTarget=1;",
            )
            edge.set("vertex", "1")
            edge.set("parent", cloud.get("id") or "1")
            # Place beside the widest region-like sibling, with padding
            siblings = [
                c for c in cells
                if c.get("parent") == cloud.get("id") and _is_container_cell(c)
            ]
            place_x, place_y = 40.0, 48.0
            if siblings:
                rightmost = max(
                    (_cell_geometry(s) or (40.0, 48.0, 200.0, 120.0) for s in siblings),
                    key=lambda g: g[0] + g[2],
                )
                place_x = rightmost[0] + rightmost[2] + 40.0
                place_y = rightmost[1]
            geom = ET.SubElement(edge, "mxGeometry")
            geom.set("x", str(int(place_x)))
            geom.set("y", str(int(place_y)))
            geom.set("width", "320")
            geom.set("height", "160")
            geom.set("as", "geometry")
            root.append(edge)
            by_id[edge_id] = edge
            cells.append(edge)
            changed = True

        kids = [
            c for c in cells
            if c.get("parent") == edge.get("id") and c.get("vertex") == "1" and c.get("edge") != "1"
        ]
        x = 24.0 + len(kids) * 100.0
        y = 48.0
        _set_parent_relative(leaf, edge, by_id, x=x, y=y)
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_misplaced_load_balancers(xml: str) -> str:
    """ALB/API Gateway should be VPC children (siblings of Zones), not subnet leaves."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    lbs = [c for c in cells if is_lb_gateway_cell(c)]
    if not lbs:
        return xml

    changed = False
    for lb in lbs:
        vpc = _find_ancestor(lb, by_id, _is_vpc_container)
        if vpc is None:
            continue
        parent = by_id.get(lb.get("parent") or "")
        if parent is not None and parent.get("id") == vpc.get("id"):
            continue
        if parent is not None and not (
            _is_subnet_container(parent) or _is_zone_container(parent)
        ):
            continue

        zones = [
            c for c in cells
            if c.get("parent") == vpc.get("id") and _is_zone_container(c)
        ]
        lb_geo = _cell_geometry(lb) or (0.0, 0.0, 78.0, 78.0)
        lb_h = lb_geo[3]
        if zones:
            zg = _cell_geometry(zones[0]) or (40.0, 80.0, 400.0, 200.0)
            rel_x = zg[0]
            # Sit clearly above the zone top with title-band clearance — never into it
            rel_y = max(48.0, zg[1] - lb_h - 24.0)
        else:
            rel_x, rel_y = 40.0, 48.0
        _set_parent_relative(lb, vpc, by_id, x=rel_x, y=rel_y)
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _set_style_kv(style: str, updates: dict[str, str]) -> str:
    parts = [p for p in (style or "").split(";") if p]
    keys = {p.split("=", 1)[0] for p in parts if "=" in p}
    out: list[str] = []
    for p in parts:
        if "=" not in p:
            out.append(p)
            continue
        k, _ = p.split("=", 1)
        if k in updates:
            out.append(f"{k}={updates[k]}")
        else:
            out.append(p)
    for k, v in updates.items():
        if k not in keys:
            out.append(f"{k}={v}")
    return ";".join(out) + (";" if out else "")


_PARALLEL_EDGE_OFFSET_PX = 20.0
_LOGICAL_PARALLEL_EDGE_OFFSET_PX = 36.0
_LOGICAL_EDGE_ROUTE_GAP = 56.0
_SAAS_LANE_GAP = 40.0
_SAAS_ICON = 78.0
_SAAS_PITCH_X = 100.0
_SAAS_PITCH_Y = 110.0
# Logical domain apps: Network-style service grid (max 4 across, then next row).
# Pitch leaves a gutter wide enough for a labelled connector between columns.
_LOGICAL_APP_MAX_COLS = 4
_LOGICAL_APP_PITCH_X = 220.0
_LOGICAL_APP_PITCH_Y = 150.0
_LOGICAL_APP_BASE_X = 28.0
_LOGICAL_APP_BASE_Y = 56.0
# Corridor between Logical domain containers — labelled edges live in here.
_LOGICAL_DOMAIN_GAP = 110.0
# Spine icon gap: enough horizontal run for "Authenticates via" without
# painting the label over the persona / IdP icons.
_LOGICAL_SPINE_GAP = 120.0
# Obstacle clearance used by the Logical edge router.
_LOGICAL_ROUTE_CLEARANCE = 16.0
_LOGICAL_ROUTE_BAND = 44.0
# Edges only count as "parallel" when they run in the same lane of the canvas.
_CORRIDOR_LANE_PX = 140.0
_CORRIDOR_BAND_STEP = 28.0
# Two runs must share at least this much travel to count as parallel.
_CORRIDOR_MIN_SHARED_PX = 60.0
_ACTOR_COL_X = 20.0
_MEGA_SAAS_LABEL_HINTS = (
    "external saas", "enterprise saas", "saas apps", "saas applications",
    "third party saas", "3rd party saas",
)


def _style_map(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _logical_obstacle_rects(
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
    skip_ids: set[str],
) -> list[tuple[float, float, float, float]]:
    """Absolute leaf boxes (incl. under-icon label band) an edge must not cross."""
    from app.adapters.outbound.diagram.xml_diagnostics import _band_height

    rects: list[tuple[float, float, float, float]] = []
    for cell in cells:
        cid = cell.get("id") or ""
        if cid in skip_ids:
            continue
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            continue
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            continue
        geo = _cell_geometry(cell)
        if geo is None:
            continue
        ax, ay = absolute_position(cell, by_id)
        rects.append((ax, ay, geo[2], geo[3] + _band_height(cell)))
    return rects


def _segment_blocked(
    p: tuple[float, float],
    q: tuple[float, float],
    obstacles: list[tuple[float, float, float, float]],
    clearance: float,
) -> bool:
    x1, x2 = sorted((p[0], q[0]))
    y1, y2 = sorted((p[1], q[1]))
    for ox, oy, ow, oh in obstacles:
        if (
            x1 < ox + ow + clearance
            and ox - clearance < x2
            and y1 < oy + oh + clearance
            and oy - clearance < y2
        ):
            return True
    return False


def _path_blocked_count(
    points: list[tuple[float, float]],
    obstacles: list[tuple[float, float, float, float]],
    clearance: float,
) -> int:
    return sum(
        1 for a, b in zip(points, points[1:])
        if _segment_blocked(a, b, obstacles, clearance)
    )


def _route_candidates(
    start: tuple[float, float],
    end: tuple[float, float],
    axis: str,
    obstacles: list[tuple[float, float, float, float]],
    offsets: tuple[float, float],
    frames: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float]]]:
    """Orthogonal paths to try, cheapest (straightest) first.

    ``frames`` are domain boxes: a detour has to clear the whole box, otherwise
    the connector cuts across a container title. ``offsets`` is
    ``(mid_shift, band_shift)`` — the first spreads parallel elbow columns, the
    second stacks parallel detours into their own lanes.
    """
    mid_shift, band_shift = offsets
    sx, sy = start
    ex, ey = end
    out: list[list[tuple[float, float]]] = []
    if axis == "h":
        if abs(sy - ey) < 1.0:
            out.append([start, end])
        mid = (sx + ex) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            mx = mid + shift
            out.append([start, (mx, sy), (mx, ey), end])
        span = [
            r for r in obstacles + frames
            if min(sx, ex) - 40 < r[0] + r[2] and r[0] < max(sx, ex) + 40
        ]
        top = min((r[1] for r in span), default=min(sy, ey))
        bottom = max((r[1] + r[3] for r in span), default=max(sy, ey))
        # Under-corridor first: the space above holds titles and the overview panel.
        for band_y in (
            bottom + _LOGICAL_ROUTE_BAND + band_shift,
            top - _LOGICAL_ROUTE_BAND - band_shift,
        ):
            out.append([
                start, (sx + 24.0, sy), (sx + 24.0, band_y),
                (ex - 24.0, band_y), (ex - 24.0, ey), end,
            ])
    else:
        if abs(sx - ex) < 1.0:
            out.append([start, end])
        mid = (sy + ey) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            my = mid + shift
            out.append([start, (sx, my), (ex, my), end])
        span = [
            r for r in obstacles + frames
            if min(sy, ey) - 40 < r[1] + r[3] and r[1] < max(sy, ey) + 40
        ]
        left = min((r[0] for r in span), default=min(sx, ex))
        right = max((r[0] + r[2] for r in span), default=max(sx, ex))
        for band_x in (
            right + _LOGICAL_ROUTE_BAND + band_shift,
            left - _LOGICAL_ROUTE_BAND - band_shift,
        ):
            out.append([
                start, (sx, sy + 24.0), (band_x, sy + 24.0),
                (band_x, ey - 24.0), (ex, ey - 24.0), end,
            ])
    return out


def _cluster_overlapping_edges(
    bucket: list[ET.Element],
    edge_meta: dict[str, dict],
) -> list[list[ET.Element]]:
    """Split a lane bucket into runs whose travel ranges actually overlap."""
    items = []
    for edge in bucket:
        meta = edge_meta.get(edge.get("id") or "")
        if meta and meta.get("travel"):
            items.append((meta["travel"], edge))
    items.sort(key=lambda it: it[0][0])
    clusters: list[list[ET.Element]] = []
    current: list[ET.Element] = []
    reach = None
    for (lo, hi), edge in items:
        if current and reach is not None and lo < reach - _CORRIDOR_MIN_SHARED_PX:
            current.append(edge)
            reach = max(reach, hi)
            continue
        if current:
            clusters.append(current)
        current, reach = [edge], hi
    if current:
        clusters.append(current)
    return clusters


def _route_logical_edge(
    edge: ET.Element,
    meta: dict,
    obstacles: list[tuple[float, float, float, float]],
    offsets: tuple[float, float],
    frames: list[tuple[float, float, float, float]],
) -> bool:
    """Pick the straightest orthogonal path that clears every other icon."""
    kv = _style_map(edge.get("style") or "")

    def _num(key: str, default: float) -> float:
        try:
            return float(kv.get(key, default))
        except (TypeError, ValueError):
            return default

    start = (
        meta["sx"] + meta["sw"] * _num("exitX", 0.5) + _num("exitDx", 0.0),
        meta["sy"] + meta["sh"] * _num("exitY", 0.5) + _num("exitDy", 0.0),
    )
    end = (
        meta["tx"] + meta["tw"] * _num("entryX", 0.5) + _num("entryDx", 0.0),
        meta["ty"] + meta["th"] * _num("entryY", 0.5) + _num("entryDy", 0.0),
    )
    axis = meta["axis"]
    candidates = _route_candidates(start, end, axis, obstacles, offsets, frames)
    best: list[tuple[float, float]] | None = None
    best_score = None
    for points in candidates:
        score = _path_blocked_count(points, obstacles, _LOGICAL_ROUTE_CLEARANCE)
        if score == 0:
            best = points
            break
        if best_score is None or score < best_score:
            best, best_score = points, score
    if best is None:
        return False
    interior = best[1:-1]
    _set_edge_waypoints(edge, interior)
    return True


def _set_edge_waypoints(edge: ET.Element, points: list[tuple[float, float]]) -> None:
    """Replace intermediate routing points on an edge (orthogonal elbows)."""
    geom = edge.find("mxGeometry")
    if geom is None:
        geom = ET.SubElement(edge, "mxGeometry")
        geom.set("relative", "1")
        geom.set("as", "geometry")
    # Drop existing Array points
    for child in list(geom):
        if child.tag == "Array" and child.get("as") == "points":
            geom.remove(child)
    if not points:
        return
    arr = ET.SubElement(geom, "Array")
    arr.set("as", "points")
    for px, py in points:
        pt = ET.SubElement(arr, "mxPoint")
        pt.set("x", str(round(px, 1)))
        pt.set("y", str(round(py, 1)))


def _bundle_under_corridor_trunks(
    edges: list[ET.Element],
    edge_meta: dict[str, dict],
    routed_paths: dict[str, list[tuple[float, float]]],
) -> bool:
    """Collapse 3+ parallel under-corridor detours onto a shared trunk with taps."""
    # Group by shared horizontal under-band (y of longest horizontal mid segment).
    bands: dict[int, list[str]] = {}
    for edge in edges:
        eid = edge.get("id") or ""
        pts = routed_paths.get(eid) or []
        if len(pts) < 2:
            continue
        # Find longest near-horizontal segment among waypoints.
        best = None
        best_len = 0.0
        seq = pts
        meta = edge_meta.get(eid)
        if not meta:
            continue
        # Include start/end for length estimate
        full = [
            (meta["sx"] + meta["sw"], meta["scy"]),
            *pts,
            (meta["tx"], meta["tcy"]),
        ]
        for a, b in zip(full, full[1:]):
            if abs(a[1] - b[1]) < 8.0:  # horizontal
                length = abs(b[0] - a[0])
                if length > best_len:
                    best_len = length
                    best = round(a[1] / 14.0) * 14  # snap band key
        if best is not None and best_len > 200:
            bands.setdefault(int(best), []).append(eid)

    changed = False
    for band_y, eids in bands.items():
        if len(eids) < 3:
            continue
        trunk_y = float(band_y)
        for i, eid in enumerate(eids):
            edge = next((e for e in edges if e.get("id") == eid), None)
            meta = edge_meta.get(eid)
            if edge is None or meta is None:
                continue
            tap = (i - (len(eids) - 1) / 2.0) * 18.0
            start = (meta["sx"] + meta["sw"], meta["scy"] + float(
                _style_map(edge.get("style") or "").get("exitDy", 0) or 0
            ))
            end = (meta["tx"], meta["tcy"] + float(
                _style_map(edge.get("style") or "").get("entryDy", 0) or 0
            ))
            interior = [
                (start[0] + 24.0, start[1]),
                (start[0] + 24.0, trunk_y + tap),
                (end[0] - 24.0, trunk_y + tap),
                (end[0] - 24.0, end[1]),
            ]
            _set_edge_waypoints(edge, interior)
            routed_paths[eid] = interior
            changed = True
    return changed


def _park_edge_labels_on_longest_segment(
    edges: list[ET.Element],
    edge_meta: dict[str, dict],
    routed_paths: dict[str, list[tuple[float, float]]],
) -> bool:
    """Park labelled-edge text on the longest straight segment (mxGeometry offset)."""
    changed = False
    for edge in edges:
        label = (edge.get("value") or "").strip()
        if not label:
            continue
        eid = edge.get("id") or ""
        meta = edge_meta.get(eid)
        if not meta:
            continue
        pts = routed_paths.get(eid) or []
        full = [
            (meta["scx"], meta["scy"]),
            *pts,
            (meta["tcx"], meta["tcy"]),
        ]
        best_i = 0
        best_len = 0.0
        for i, (a, b) in enumerate(zip(full, full[1:])):
            length = abs(b[0] - a[0]) + abs(b[1] - a[1])
            if length > best_len:
                best_len = length
                best_i = i
        # Relative offset along the edge: fraction of segment midpoints.
        # draw.io x/y on relative geometry are -1..1 normalised offsets.
        # Prefer mid of longest segment ≈ (best_i + 0.5) / (n_segs).
        n_segs = max(1, len(full) - 1)
        frac = (best_i + 0.5) / n_segs
        # Convert to draw.io label offset: x along edge (-1 left .. 1 right of mid)
        # 0.5 frac → x=0 (mid); earlier → negative.
        label_x = (frac - 0.5) * 2.0
        geom = edge.find("mxGeometry")
        if geom is None:
            continue
        # Only set when different enough
        try:
            cur = float(geom.get("x") or 0)
        except ValueError:
            cur = 0.0
        if abs(cur - label_x) > 0.05:
            geom.set("x", str(round(label_x, 3)))
            changed = True
        # Soft vertical nudge off the stroke when label present
        if geom.get("y") in (None, ""):
            geom.set("y", "-1")
            changed = True
    return changed


def fix_edge_anchors_from_geometry(xml: str) -> str:
    """Set exit/entry anchors from relative source→target placement + parallel offset.

    On Logical pages, also inserts staggered top/side waypoints so connectors
    do not run straight through intervening service icons.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    page = _diagram_page_name(tree).lower()
    is_focused = page in {"logical", "security", "data flow", "data_flow"}
    parallel_px = _LOGICAL_PARALLEL_EDGE_OFFSET_PX if is_focused else _PARALLEL_EDGE_OFFSET_PX

    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    # Group edges by shared face for spread
    face_buckets: dict[tuple[str, str, str], list[ET.Element]] = {}
    corridor_buckets: dict[tuple, list[ET.Element]] = {}
    edge_meta: dict[str, dict] = {}
    changed = False

    for edge in edges:
        src_id, tgt_id = edge.get("source"), edge.get("target")
        if not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
            continue
        sx, sy = absolute_position(by_id[src_id], by_id)
        tx, ty = absolute_position(by_id[tgt_id], by_id)
        sgeo = _cell_geometry(by_id[src_id])
        tgeo = _cell_geometry(by_id[tgt_id])
        sw = sgeo[2] if sgeo else 78
        sh = sgeo[3] if sgeo else 78
        tw = tgeo[2] if tgeo else 78
        th = tgeo[3] if tgeo else 78
        scx = sx + sw / 2
        scy = sy + sh / 2
        tcx = tx + tw / 2
        tcy = ty + th / 2
        dx, dy = tcx - scx, tcy - scy

        if abs(dx) >= abs(dy):
            if dx >= 0:
                anchors = {"exitX": "1", "exitY": "0.5", "entryX": "0", "entryY": "0.5"}
                face = ("h", src_id, "right")
                corridor = ("h", "right", "left")
            else:
                anchors = {"exitX": "0", "exitY": "0.5", "entryX": "1", "entryY": "0.5"}
                face = ("h", src_id, "left")
                corridor = ("h", "left", "right")
        else:
            if dy >= 0:
                anchors = {"exitX": "0.5", "exitY": "1", "entryX": "0.5", "entryY": "0"}
                face = ("v", src_id, "bottom")
                corridor = ("v", "bottom", "top")
            else:
                anchors = {"exitX": "0.5", "exitY": "0", "entryX": "0.5", "entryY": "1"}
                face = ("v", src_id, "top")
                corridor = ("v", "top", "bottom")

        face_buckets.setdefault(face, []).append(edge)
        # Only stagger edges that genuinely share a corridor — bucketing every
        # left→right edge on the page together pushed anchors off their shapes.
        if corridor[0] == "h":
            lane = (round(scy / _CORRIDOR_LANE_PX), round(tcy / _CORRIDOR_LANE_PX))
            travel = (min(scx, tcx), max(scx, tcx))
        else:
            lane = (round(scx / _CORRIDOR_LANE_PX), round(tcx / _CORRIDOR_LANE_PX))
            travel = (min(scy, tcy), max(scy, tcy))
        corridor_buckets.setdefault((*corridor, *lane), []).append(edge)
        eid = edge.get("id") or id(edge)
        edge_meta[str(eid)] = {
            "travel": travel,
            "axis": corridor[0],
            "sx": sx, "sy": sy, "sw": sw, "sh": sh,
            "tx": tx, "ty": ty, "tw": tw, "th": th,
            "scx": scx, "scy": scy, "tcx": tcx, "tcy": tcy,
        }
        style = edge.get("style") or ""
        updates = {
            **anchors,
            "exitDx": "0",
            "exitDy": "0",
            "entryDx": "0",
            "entryDy": "0",
            "edgeStyle": "orthogonalEdgeStyle",
            "rounded": "1",
            "orthogonalLoop": "1",
            "jettySize": "auto",
            "html": "1",
        }
        new_style = _set_style_kv(style, updates)
        if new_style != style:
            edge.set("style", new_style)
            changed = True

    # Spread multiple edges sharing a source face (finer on Logical)
    spreads = ("0.15", "0.35", "0.5", "0.65", "0.85") if is_focused else ("0.25", "0.5", "0.75")
    for face, group in face_buckets.items():
        if len(group) < 2:
            continue
        axis = face[0]
        for i, edge in enumerate(group):
            frac = spreads[i % len(spreads)]
            style = edge.get("style") or ""
            if axis == "h":
                updates = {"exitY": frac, "entryY": frac}
            else:
                updates = {"exitX": frac, "entryX": frac}
            edge.set("style", _set_style_kv(style, updates))
            changed = True

    # CORE parallel-corridor offset: stagger Dx/Dy so stacked orthogonal paths
    # separate. Sequential hops (a→b→c) share a lane but never overlay, so only
    # edges whose travel ranges actually overlap are treated as parallel.
    band_offsets: dict[str, tuple[float, float]] = {}
    for corridor, bucket in corridor_buckets.items():
        axis = corridor[0]
        for group in _cluster_overlapping_edges(bucket, edge_meta):
            n = len(group)
            if n < 2:
                continue
            for i, edge in enumerate(group):
                slot = i - (n - 1) / 2.0
                meta = edge_meta.get(edge.get("id") or "") or {}
                # Keep the anchor on the shape: a big Dy on a 68px icon starts
                # the line in empty space beside it.
                span = min(meta.get("sh", 78.0), meta.get("th", 78.0)) if axis == "h" else \
                    min(meta.get("sw", 78.0), meta.get("tw", 78.0))
                limit = max(0.0, span / 2.0 - 10.0)
                offset = max(-limit, min(limit, slot * parallel_px))
                style = edge.get("style") or ""
                if axis == "h":
                    updates = {"exitDy": str(offset), "entryDy": str(offset)}
                else:
                    updates = {"exitDx": str(offset), "entryDx": str(offset)}
                edge.set("style", _set_style_kv(style, updates))
                band_offsets[edge.get("id") or ""] = (
                    slot * _CORRIDOR_BAND_STEP,
                    i * _CORRIDOR_BAND_STEP,
                )
                changed = True

    # Focused views: route each connector around other icons instead of through them.
    if is_focused:
        frames = [
            (*absolute_position(c, by_id), _cell_geometry(c)[2], _cell_geometry(c)[3])
            for c in cells
            if _is_container_cell(c)
            and _cell_geometry(c) is not None
            and (c.get("id") or "") not in {"overview_panel", "legend_box"}
        ]
        routed_paths: dict[str, list[tuple[float, float]]] = {}
        for edge in edges:
            eid = edge.get("id") or ""
            meta = edge_meta.get(eid)
            if not meta:
                continue
            obstacles = _logical_obstacle_rects(
                cells, by_id, {edge.get("source") or "", edge.get("target") or ""}
            )
            if _route_logical_edge(
                edge, meta, obstacles, band_offsets.get(eid, (0.0, 0.0)), frames
            ):
                changed = True
            # Capture waypoints for label parking / trunk bundling.
            geom = edge.find("mxGeometry")
            pts = []
            if geom is not None:
                for arr in geom.findall("Array"):
                    if arr.get("as") == "points":
                        for pt in arr.findall("mxPoint"):
                            try:
                                pts.append((float(pt.get("x") or 0), float(pt.get("y") or 0)))
                            except ValueError:
                                pass
            routed_paths[eid] = pts

        if _bundle_under_corridor_trunks(edges, edge_meta, routed_paths):
            changed = True
        if _park_edge_labels_on_longest_segment(edges, edge_meta, routed_paths):
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _is_saas_leaf_cell(cell: ET.Element) -> bool:
    """True for curated enterprise SaaS product leaves (not PrivateLink endpoints)."""
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    st = _shape(cell)
    if st.startswith("saas_"):
        return True
    from app.adapters.outbound.diagram.architecture_docs import match_saas_shape_type
    return match_saas_shape_type(_plain_label(cell)) is not None


def _diagram_page_name(tree: ET.Element) -> str:
    if tree.tag == "mxfile":
        diag = tree.find("diagram")
        if diag is not None:
            return (diag.get("name") or "").strip()
    return ""


def _is_network_page(tree: ET.Element) -> bool:
    return _diagram_page_name(tree).lower() == "network"


def _is_mega_saas_container(cell: ET.Element) -> bool:
    if not _is_container_cell(cell):
        return False
    label = _plain_label(cell)
    cid = (cell.get("id") or "").lower()
    if cid.startswith("domain_"):
        return False
    # Security External SaaS Zone is the correct home for SaaS leaves — not a
    # Network-style mega box that should be emptied onto the canvas root.
    if cid.startswith("zone_external_saas") or (
        cid.startswith("zone_") and "external saas" in label
    ):
        return False
    if "saas" in cid and "domain" not in cid:
        return True
    return any(h in label for h in _MEGA_SAAS_LABEL_HINTS) or label in {
        "saas", "external saas", "enterprise saas",
    }


def _find_primary_cloud(
    cells: list[ET.Element],
    page_w: float,
    page_h: float,
) -> ET.Element | None:
    from app.adapters.outbound.diagram.xml_diagnostics import _is_canvas_background_cell

    clouds = [
        c for c in cells
        if _is_cloud_container(c)
        and c.get("parent") == "1"
        and not _is_canvas_background_cell(c, page_w, page_h)
    ]
    if clouds:
        def _area(c: ET.Element) -> float:
            g = _cell_geometry(c)
            return (g[2] * g[3]) if g else 0.0
        return max(clouds, key=_area)
    # Fallback: largest top-level container (never overview/legend).
    top = [
        c for c in cells
        if _is_container_cell(c)
        and c.get("parent") == "1"
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
        and not _is_canvas_background_cell(c, page_w, page_h)
        and not _is_mega_saas_container(c)
    ]
    if not top:
        return None

    def _area2(c: ET.Element) -> float:
        g = _cell_geometry(c)
        return (g[2] * g[3]) if g else 0.0

    return max(top, key=_area2)


def _saas_under_forbidden_ancestor(cell: ET.Element, by_id: dict[str, ET.Element]) -> bool:
    if cell.get("parent") in (None, "1"):
        return False
    if _find_ancestor(cell, by_id, _is_vpc_container) is not None:
        return True
    if _find_ancestor(cell, by_id, _is_cloud_container) is not None:
        return True
    cur = by_id.get(cell.get("parent") or "")
    seen: set[str] = set()
    while cur is not None:
        cid = cur.get("id") or ""
        if not cid or cid in seen:
            break
        seen.add(cid)
        if _is_cross_cutting_group_cell(cur) or _is_mega_saas_container(cur):
            return True
        cur = by_id.get(cur.get("parent") or "")
    return False


def _hoist_saas_to_root(cell: ET.Element, by_id: dict[str, ET.Element]) -> None:
    abs_x, abs_y = absolute_position(cell, by_id)
    cell.set("parent", "1")
    geom = cell.find("mxGeometry")
    if geom is not None:
        geom.set("x", str(abs_x))
        geom.set("y", str(abs_y))


def _edge_band_y(cells: list[ET.Element], cloud: ET.Element, by_id: dict[str, ET.Element]) -> float:
    cloud_id = cloud.get("id")
    edge = None
    for c in cells:
        if not _is_edge_layer_container(c):
            continue
        if c.get("parent") == cloud_id:
            edge = c
            break
        anc = _find_ancestor(c, by_id, _is_cloud_container)
        if anc is not None and anc.get("id") == cloud_id:
            edge = c
            break
    if edge is not None:
        _ex, ey = absolute_position(edge, by_id)
        return max(40.0, ey)
    cg = _cell_geometry(cloud)
    if cg is not None:
        return max(40.0, cg[1] + 48.0)
    return 80.0


def _cloud_descendant_ids(cloud: ET.Element, cells: list[ET.Element], by_id: dict[str, ET.Element]) -> set[str]:
    """All cell ids under the Cloud wrapper (inclusive)."""
    cloud_id = cloud.get("id") or ""
    out: set[str] = {cloud_id} if cloud_id else set()
    children_by: dict[str, list[str]] = {}
    for c in cells:
        pid = c.get("parent") or ""
        cid = c.get("id") or ""
        if pid and cid:
            children_by.setdefault(pid, []).append(cid)
    stack = [cloud_id] if cloud_id else []
    while stack:
        cur = stack.pop()
        for child in children_by.get(cur, ()):
            if child not in out:
                out.add(child)
                stack.append(child)
    return out


def _collect_edge_pairs(cells: list[ET.Element]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for c in cells:
        if c.get("edge") != "1":
            continue
        src, tgt = c.get("source") or "", c.get("target") or ""
        if src and tgt:
            pairs.append((src, tgt))
    return pairs


def _layout_saas_side_band(
    band: list[ET.Element],
    *,
    start_x: float,
    band_y: float,
    grow_right: bool,
) -> tuple[bool, float]:
    """Place a SaaS side band L→R by journey role. Returns (changed, right_edge_x)."""
    from app.adapters.outbound.diagram.architecture_docs import (
        saas_journey_role,
        saas_journey_sort_key,
    )

    if not band:
        return False, start_x

    role_cols: dict[str, list[ET.Element]] = {}
    for cell in band:
        role = saas_journey_role(_shape(cell), _plain_label(cell))
        role_cols.setdefault(role, []).append(cell)
    role_order = sorted(
        role_cols.keys(),
        key=lambda r: saas_journey_sort_key(
            next((_shape(c) for c in role_cols[r]), ""), ""
        )[0],
    )
    changed = False
    x = start_x
    max_right = start_x
    for role in role_order:
        for row_i, cell in enumerate(role_cols[role]):
            y = band_y + row_i * _SAAS_PITCH_Y
            geom_el = cell.find("mxGeometry")
            if geom_el is None:
                continue
            try:
                cur_x = float(geom_el.get("x") or 0)
                cur_y = float(geom_el.get("y") or 0)
            except ValueError:
                cur_x, cur_y = 0.0, 0.0
            if abs(cur_x - x) > 0.5 or abs(cur_y - y) > 0.5 or cell.get("parent") != "1":
                geom_el.set("x", str(x))
                geom_el.set("y", str(y))
                cell.set("parent", "1")
                changed = True
            max_right = max(max_right, x + _SAAS_ICON)
        x += _SAAS_PITCH_X
    return changed, max_right


def _place_network_saas_lane(
    tree: ET.Element,
    root: ET.Element,
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
    saas_leaves: list[ET.Element],
    *,
    page_w: float,
    page_h: float,
) -> bool:
    """Place SaaS by call path: ingress left of Cloud, egress right of Cloud.

    Users → Okta → Cloud  ⇒ Okta on the left.  Cloud apps → Workday ⇒ Workday on the right.
    Edges override role defaults.
    """
    from app.adapters.outbound.diagram.architecture_docs import (
        resolve_saas_network_side,
        saas_journey_role,
        saas_journey_sort_key,
    )
    from app.adapters.outbound.diagram.xml_diagnostics import _is_external_actor_cell

    cloud = _find_primary_cloud(cells, page_w, page_h)
    if cloud is None:
        return False
    cloud_geo = _cell_geometry(cloud)
    if cloud_geo is None:
        return False

    changed = False

    # Flatten mega External SaaS containers on root.
    for cell in list(cells):
        if cell.get("parent") != "1" or not _is_mega_saas_container(cell):
            continue
        mega_id = cell.get("id") or ""
        kids = [c for c in cells if c.get("parent") == mega_id and _is_saas_leaf_cell(c)]
        for kid in kids:
            _hoist_saas_to_root(kid, by_id)
            changed = True
        remaining = [c for c in cells if c.get("parent") == mega_id]
        if not remaining and mega_id:
            try:
                root.remove(cell)
                by_id.pop(mega_id, None)
                changed = True
            except ValueError:
                pass

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    saas_leaves = [c for c in cells if _is_saas_leaf_cell(c)]

    for cell in saas_leaves:
        if _saas_under_forbidden_ancestor(cell, by_id):
            _hoist_saas_to_root(cell, by_id)
            changed = True

    band = [c for c in saas_leaves if c.get("parent") == "1"]
    if not band:
        return changed

    actors = [
        c for c in cells
        if _is_external_actor_cell(c) and c.get("parent") == "1"
    ]
    actors_max_x = _ACTOR_COL_X
    for i, actor in enumerate(sorted(actors, key=lambda c: (_cell_geometry(c) or (0, 0, 0, 0))[1])):
        geo = _cell_geometry(actor)
        w = geo[2] if geo else _SAAS_ICON
        target_x = _ACTOR_COL_X
        target_y = 40.0 + i * _SAAS_PITCH_Y
        geom_el = actor.find("mxGeometry")
        if geom_el is not None:
            try:
                cur_x = float(geom_el.get("x") or 0)
            except ValueError:
                cur_x = 0.0
            if cur_x >= cloud_geo[0] - _SAAS_LANE_GAP or cur_x < _ACTOR_COL_X - 1:
                geom_el.set("x", str(target_x))
                geom_el.set("y", str(target_y))
                changed = True
                cur_x = target_x
            actors_max_x = max(actors_max_x, cur_x + w)
        else:
            actors_max_x = max(actors_max_x, target_x + w)

    actor_ids = {c.get("id") or "" for c in actors if c.get("id")}
    cloud_geo = _cell_geometry(cloud) or cloud_geo
    cloud_desc = _cloud_descendant_ids(cloud, cells, by_id)
    edges = _collect_edge_pairs(cells)
    band_y = _edge_band_y(cells, cloud, by_id)

    ingress: list[ET.Element] = []
    egress: list[ET.Element] = []
    for cell in band:
        side = resolve_saas_network_side(
            _shape(cell),
            _plain_label(cell),
            cell_id=cell.get("id") or "",
            edges=edges,
            actor_ids=actor_ids,
            cloud_descendant_ids=cloud_desc,
        )
        (ingress if side == "ingress" else egress).append(cell)

    ingress.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))
    egress.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))

    # --- Ingress band: Users → SaaS → Cloud ---
    ingress_start = max(_ACTOR_COL_X + _SAAS_ICON + 20.0, actors_max_x + _SAAS_LANE_GAP)
    if ingress:
        n_roles = len({saas_journey_role(_shape(c), _plain_label(c)) for c in ingress})
        ingress_needed = (max(1, n_roles) - 1) * _SAAS_PITCH_X + _SAAS_ICON
    else:
        ingress_needed = 0.0
    min_cloud_x = ingress_start + (ingress_needed + _SAAS_LANE_GAP if ingress else 0.0)
    cloud_x = cloud_geo[0]
    if cloud_x < min_cloud_x:
        geom_el = cloud.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(min_cloud_x))
            changed = True
            cloud_x = min_cloud_x
            cloud_geo = _cell_geometry(cloud) or cloud_geo

    if ingress:
        ch, _ = _layout_saas_side_band(ingress, start_x=ingress_start, band_y=band_y, grow_right=True)
        changed = changed or ch

    # --- Egress band: Cloud → SaaS (apps pull profile/data) ---
    if egress:
        cloud_right = cloud_x + cloud_geo[2]
        egress_start = cloud_right + _SAAS_LANE_GAP
        ch, _ = _layout_saas_side_band(egress, start_x=egress_start, band_y=band_y, grow_right=True)
        changed = changed or ch

    return changed


def _stamp_saas_types(saas_leaves: list[ET.Element]) -> bool:
    from app.adapters.outbound.diagram.architecture_docs import match_saas_shape_type

    changed = False
    for cell in saas_leaves:
        st = _shape(cell)
        if not st.startswith("saas_"):
            matched = match_saas_shape_type(_plain_label(cell))
            if matched:
                cell.set("shape_type", matched)
                changed = True
    return changed


def _reparent_forbidden_saas(saas_leaves: list[ET.Element], by_id: dict[str, ET.Element]) -> bool:
    changed = False
    for cell in saas_leaves:
        if _saas_under_forbidden_ancestor(cell, by_id):
            _hoist_saas_to_root(cell, by_id)
            changed = True
    return changed


def _domain_corridor_key(cell: ET.Element) -> tuple[int, str]:
    cid = (cell.get("id") or "").lower()
    label = _plain_label(cell)
    blob = f"{cid} {label}"
    if "saas" in blob:
        return (5, label)
    if any(k in blob for k in ("channel", "experience", "client", "portal", "persona", "actor")):
        return (0, label)
    if any(k in blob for k in ("identity", "auth", "idp")):
        return (1, label)
    if any(k in blob for k in ("data", "store", "persist", "warehouse")):
        return (4, label)
    if any(k in blob for k in ("platform", "observ", "shared")):
        return (3, label)
    return (2, label)


def _zone_corridor_key(cell: ET.Element) -> tuple[int, str]:
    cid = (cell.get("id") or "").lower()
    label = _plain_label(cell)
    blob = f"{cid} {label}"
    order = [
        ("public", 0), ("perimeter", 1), ("dmz", 1), ("edge", 1),
        ("identity", 2), ("private", 3), ("restricted", 4), ("highly", 4),
        ("sensitive", 4), ("detect", 5), ("management", 5), ("monitoring", 5),
        ("external saas", 6), ("external_saas", 6), ("saas", 6),
    ]
    for needle, idx in order:
        if needle in blob:
            return (idx, label)
    return (3, label)


_SECURITY_PRIVATE_ZONE_HINTS = (
    "private", "restricted", "highly", "sensitive", "app zone", "app tier",
)
_EXTERNAL_SAAS_ZONE_ID = "zone_external_saas"
_EXTERNAL_SAAS_ZONE_STYLE = (
    "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
    "collapsible=0;recursiveResize=0;fillColor=#FFF2CC;strokeColor=#D6B656;"
    "strokeWidth=1;dashed=1;align=left;verticalAlign=top;spacingLeft=12;"
    "spacingTop=8;fontStyle=1;fontSize=12;fontColor=#D6B656;"
)


def _security_zone_is_private_like(cell: ET.Element | None) -> bool:
    if cell is None or not _is_container_cell(cell):
        return False
    blob = f"{(cell.get('id') or '').lower()} {_plain_label(cell)}"
    if "saas" in blob or "public" in blob or "identity" in blob or "detect" in blob:
        return False
    return any(h in blob for h in _SECURITY_PRIVATE_ZONE_HINTS)


def _ensure_external_saas_zone(
    root: ET.Element,
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
) -> ET.Element:
    """Return existing or newly created External SaaS Zone on parent=1."""
    existing = by_id.get(_EXTERNAL_SAAS_ZONE_ID)
    if existing is not None:
        return existing
    for c in cells:
        blob = f"{(c.get('id') or '').lower()} {_plain_label(c)}"
        if _is_container_cell(c) and "external saas" in blob:
            return c
    zone = ET.SubElement(root, "mxCell")
    zone.set("id", _EXTERNAL_SAAS_ZONE_ID)
    zone.set("value", "External SaaS Zone")
    zone.set("style", _EXTERNAL_SAAS_ZONE_STYLE)
    zone.set("vertex", "1")
    zone.set("parent", "1")
    geom = ET.SubElement(zone, "mxGeometry")
    geom.set("x", "900")
    geom.set("y", "80")
    geom.set("width", "220")
    geom.set("height", "220")
    geom.set("as", "geometry")
    by_id[_EXTERNAL_SAAS_ZONE_ID] = zone
    cells.append(zone)
    return zone


def hoist_security_saas_from_private_zones(xml: str) -> str:
    """Move enterprise SaaS out of Private/Restricted zones into External SaaS Zone."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    if _diagram_page_name(tree).lower() != "security":
        return xml

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    saas_leaves = [c for c in cells if _is_saas_leaf_cell(c)]
    if not saas_leaves:
        return xml

    changed = False
    zone: ET.Element | None = None
    for leaf in saas_leaves:
        pid = leaf.get("parent") or ""
        private_ancestor = None
        while pid and pid not in {"0", "1"}:
            parent = by_id.get(pid)
            if parent is None:
                break
            if _security_zone_is_private_like(parent):
                private_ancestor = parent
                break
            blob = f"{(parent.get('id') or '').lower()} {_plain_label(parent)}"
            if any(x in blob for x in ("external saas", "external_saas", "public", "identity", "detect")):
                private_ancestor = None
                break
            if not _is_container_cell(parent):
                break
            pid = parent.get("parent") or ""
        if private_ancestor is None and leaf.get("parent") not in (None, "1"):
            parent = by_id.get(leaf.get("parent") or "")
            if _security_zone_is_private_like(parent):
                private_ancestor = parent
        if private_ancestor is None:
            continue
        if zone is None:
            zone = _ensure_external_saas_zone(root, cells, by_id)
            changed = True
        ax, ay = absolute_position(leaf, by_id)
        zx, zy = absolute_position(zone, by_id)
        leaf.set("parent", zone.get("id") or _EXTERNAL_SAAS_ZONE_ID)
        geom = leaf.find("mxGeometry")
        if geom is not None:
            geom.set("x", str(max(24.0, ax - zx)))
            geom.set("y", str(max(48.0, ay - zy)))
        changed = True

    if not changed:
        return xml
    if zone is not None:
        _sort_saas_children_in_shelf(zone, cells, vertical=True)
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _phase_corridor_key(cell: ET.Element) -> tuple[int, str]:
    cid = (cell.get("id") or "").lower()
    label = _plain_label(cell)
    blob = f"{cid} {label}"
    order = [
        ("ingest", 0), ("ingress", 0), ("collect", 0),
        ("process", 1), ("transform", 1), ("compute", 1),
        ("store", 2), ("persist", 2), ("warehouse", 2),
        ("consume", 3), ("serve", 3), ("analytics", 3),
    ]
    for needle, idx in order:
        if needle in blob:
            return (idx, label)
    return (1, label)


def _logical_grid_cols(count: int) -> int:
    """Columns for a domain's app grid — balanced, never more than 4.

    A hard 4-wide row makes a single domain ~900px, so three domains no longer
    fit on screen at a readable zoom. Keeping the grid roughly square packs the
    same apps into a compact block while still honouring the max-4 cap.
    """
    if count <= 3:
        return max(1, count)
    return min(_LOGICAL_APP_MAX_COLS, math.ceil(math.sqrt(count)))


def _grid_logical_domain_apps(domain: ET.Element, cells: list[ET.Element]) -> bool:
    """Lay out non-SaaS leaves in a domain as a balanced grid, max 4 across.

    Mirrors Network's service-grid rule. ``domain_saas`` stays vertical via
    ``_sort_saas_children_in_shelf`` and is skipped here.
    """
    did = (domain.get("id") or "").lower()
    if did.startswith("domain_saas") or "saas" in did:
        return False
    kids = [
        c for c in cells
        if c.get("parent") == domain.get("id")
        and c.get("vertex") == "1"
        and c.get("edge") != "1"
        and not _is_container_cell(c)
        and not _is_saas_leaf_cell(c)
        and not (c.get("id") or "").startswith("step_badge_")
    ]
    if len(kids) < 1:
        return False
    # Stable reading order: existing top→left, then grid.
    kids.sort(key=lambda c: (
        (_cell_geometry(c) or (0, 0, 0, 0))[1],
        (_cell_geometry(c) or (0, 0, 0, 0))[0],
        c.get("id") or "",
    ))
    changed = False
    cols_n = _logical_grid_cols(len(kids))
    for i, kid in enumerate(kids):
        col = i % cols_n
        row = i // cols_n
        x = _LOGICAL_APP_BASE_X + col * _LOGICAL_APP_PITCH_X
        y = _LOGICAL_APP_BASE_Y + row * _LOGICAL_APP_PITCH_Y
        geom = kid.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_x = float(geom.get("x") or 0)
            cur_y = float(geom.get("y") or 0)
        except ValueError:
            cur_x, cur_y = 0.0, 0.0
        if abs(cur_x - x) > 0.5 or abs(cur_y - y) > 0.5:
            geom.set("x", str(x))
            geom.set("y", str(y))
            changed = True
    # Grow domain so wrapped rows are not clipped.
    if kids:
        rows = (len(kids) - 1) // cols_n + 1
        cols = min(len(kids), cols_n)
        needed_w = _LOGICAL_APP_BASE_X + cols * _LOGICAL_APP_PITCH_X + 20.0
        needed_h = _LOGICAL_APP_BASE_Y + rows * _LOGICAL_APP_PITCH_Y + 24.0
        shelf_geo = domain.find("mxGeometry")
        if shelf_geo is not None:
            try:
                cur_w = float(shelf_geo.get("width") or 0)
                cur_h = float(shelf_geo.get("height") or 0)
            except ValueError:
                cur_w, cur_h = 0.0, 0.0
            if cur_w < needed_w:
                shelf_geo.set("width", str(needed_w))
                changed = True
            if cur_h < needed_h:
                shelf_geo.set("height", str(needed_h))
                changed = True
    return changed


def _sort_saas_children_in_shelf(
    shelf: ET.Element,
    cells: list[ET.Element],
    *,
    vertical: bool = True,
) -> bool:
    """Stack SaaS leaves in a shelf. Logical uses a vertical column (clearer edges)."""
    from app.adapters.outbound.diagram.architecture_docs import saas_journey_sort_key

    sid = shelf.get("id") or ""
    kids = [c for c in cells if c.get("parent") == sid and _is_saas_leaf_cell(c)]
    if len(kids) < 1:
        return False
    kids.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))
    changed = False
    base_x, base_y = 24.0, 48.0
    for i, kid in enumerate(kids):
        if vertical:
            x = base_x
            y = base_y + i * _SAAS_PITCH_Y
        else:
            x = base_x + (i % 4) * _SAAS_PITCH_X
            y = base_y + (i // 4) * _SAAS_PITCH_Y
        geom = kid.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_x = float(geom.get("x") or 0)
            cur_y = float(geom.get("y") or 0)
        except ValueError:
            cur_x, cur_y = 0.0, 0.0
        if abs(cur_x - x) > 0.5 or abs(cur_y - y) > 0.5:
            geom.set("x", str(x))
            geom.set("y", str(y))
            changed = True
    # Grow shelf height so the vertical stack is not clipped.
    if vertical and kids:
        needed_h = base_y + len(kids) * _SAAS_PITCH_Y + 24.0
        shelf_geo = shelf.find("mxGeometry")
        if shelf_geo is not None:
            try:
                cur_h = float(shelf_geo.get("height") or 0)
            except ValueError:
                cur_h = 0.0
            if cur_h < needed_h:
                shelf_geo.set("height", str(needed_h))
                # Keep a readable width for one icon column + labels
                try:
                    cur_w = float(shelf_geo.get("width") or 0)
                except ValueError:
                    cur_w = 0.0
                if cur_w < 160.0:
                    shelf_geo.set("width", "180")
                changed = True
    return changed


def enforce_view_journey_columns(xml: str) -> str:
    """L→R corridor for Logical domains / Security zones / Data phases + SaaS shelf.

    Logical / Security / Data flow use dependency-driven layered placement so
    most edges are short adjacent-rank hops; External SaaS anchors under Data.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return xml

    if page_name == "security":
        xml = hoist_security_saas_from_private_zones(xml)
        parsed = _parse_docroot(xml)
        if parsed is None:
            return xml
        tree, root, is_mxfile, trimmed = parsed

    from app.adapters.outbound.diagram.logical_layout import apply_layered_corridor_layout
    from app.adapters.outbound.diagram.xml_diagnostics import (
        _is_canvas_background_cell,
        _page_size,
    )

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    page_w, page_h = _page_size(tree)
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    # Always hoist SaaS out of VPC/Cloud/Integration if wrongly nested on focused views.
    saas_leaves = [c for c in cells if _is_saas_leaf_cell(c)]
    changed = _stamp_saas_types(saas_leaves)
    if _reparent_forbidden_saas(saas_leaves, by_id):
        changed = True
        cells = [c for c in root.findall("mxCell") if c.get("id")]

    prefix = {
        "logical": "domain_",
        "security": "zone_",
        "data flow": "phase_",
        "data_flow": "phase_",
    }[page_name]
    key_fn = {
        "logical": _domain_corridor_key,
        "security": _zone_corridor_key,
        "data flow": _phase_corridor_key,
        "data_flow": _phase_corridor_key,
    }[page_name]

    corridors = [
        c for c in cells
        if _is_container_cell(c)
        and c.get("parent") == "1"
        and (c.get("id") or "").lower().startswith(prefix)
        and not _is_canvas_background_cell(c, page_w, page_h)
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
    ]
    if not corridors:
        corridors = [
            c for c in cells
            if _is_container_cell(c)
            and c.get("parent") == "1"
            and (c.get("id") or "") not in {"overview_panel", "legend_box"}
            and not _is_canvas_background_cell(c, page_w, page_h)
            and not _is_cloud_container(c)
        ]

    def _is_layout_leaf(cell: ET.Element) -> bool:
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            return False
        cid = cell.get("id") or ""
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            return False
        return True

    gap = _LOGICAL_DOMAIN_GAP if page_name in {"logical", "security", "data flow", "data_flow"} else 40.0
    if corridors and apply_layered_corridor_layout(
        cells,
        corridors=corridors,
        leaf_filter=_is_layout_leaf,
        keyword_key=key_fn,
        start_x=160.0,
        gap=gap,
        mark_layout=True,
    ):
        changed = True
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        # Re-stack SaaS children vertically after the shelf was re-anchored.
        for cell in cells:
            cid = (cell.get("id") or "").lower()
            if cid.startswith("domain_saas") or (cid.startswith("domain_") and "saas" in cid):
                if _sort_saas_children_in_shelf(cell, cells, vertical=True):
                    changed = True
    else:
        # Fallback: previous keyword corridor + grid for thin pages
        vertical_saas = True
        if len(corridors) >= 2:
            corridors.sort(key=key_fn)
            x = 160.0
            for cell in corridors:
                if (cell.get("id") or "").lower().startswith("domain_saas"):
                    continue
                geo = _cell_geometry(cell)
                geom = cell.find("mxGeometry")
                if geo is None or geom is None:
                    continue
                if abs(geo[0] - x) > 1.0:
                    geom.set("x", str(x))
                    changed = True
                if page_name == "logical" and _grid_logical_domain_apps(cell, cells):
                    changed = True
                    geo = _cell_geometry(cell) or geo
                if _sort_saas_children_in_shelf(cell, cells, vertical=vertical_saas):
                    changed = True
                    geo = _cell_geometry(cell) or geo
                x += geo[2] + gap
        else:
            for cell in corridors:
                if page_name == "logical" and _grid_logical_domain_apps(cell, cells):
                    changed = True
                if _sort_saas_children_in_shelf(cell, cells, vertical=vertical_saas):
                    changed = True

    # Root-level SaaS on Security / Data flow: vertical stack beside first corridor.
    root_saas = [c for c in cells if _is_saas_leaf_cell(c) and c.get("parent") == "1"]
    if root_saas and page_name != "logical":
        from app.adapters.outbound.diagram.architecture_docs import saas_journey_sort_key

        root_saas.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))
        base_x = 40.0
        base_y = 80.0
        ranked = [c for c in corridors if not (c.get("id") or "").lower().startswith("domain_saas")]
        if ranked:
            leftmost = min(
                (_cell_geometry(c)[0] for c in ranked if _cell_geometry(c)),
                default=200.0,
            )
            base_x = max(40.0, leftmost - _SAAS_ICON - 40.0)
        for i, cell in enumerate(root_saas):
            # Identity SaaS on Logical spine handled below; on Security keep left band.
            x = base_x
            y = base_y + i * _SAAS_PITCH_Y
            geom = cell.find("mxGeometry")
            if geom is None:
                continue
            try:
                cur_x = float(geom.get("x") or 0)
                cur_y = float(geom.get("y") or 0)
            except ValueError:
                cur_x, cur_y = 0.0, 0.0
            if abs(cur_x - x) > 0.5 or abs(cur_y - y) > 0.5:
                geom.set("x", str(x))
                geom.set("y", str(y))
                changed = True
            if cell.get("layout") != "1":
                cell.set("layout", "1")
                changed = True

    if page_name == "logical":
        if _layout_logical_journey_spine(root, cells):
            changed = True
        if _ensure_logical_journey_edges(root, cells):
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _is_logical_actor_cell(cell: ET.Element) -> bool:
    st = _shape(cell)
    if st == "logical_actor":
        return True
    from app.adapters.outbound.diagram.architecture_docs import _logical_leaf_role
    return _logical_leaf_role(_plain_label(cell)) == "actor"


def _is_logical_channel_cell(cell: ET.Element) -> bool:
    st = _shape(cell)
    if st == "logical_channel":
        return True
    from app.adapters.outbound.diagram.architecture_docs import _logical_leaf_role
    return _logical_leaf_role(_plain_label(cell)) == "channel"


def _is_identity_saas_cell(cell: ET.Element) -> bool:
    st = _shape(cell)
    if st == "saas_okta":
        return True
    low = _plain_label(cell)
    return _is_saas_leaf_cell(cell) and any(
        x in low for x in ("okta", "auth0", "idp", "sso", "entra")
    )


def _layout_logical_journey_spine(root: ET.Element, cells: list[ET.Element]) -> bool:
    """Compact Network-inspired spine: Actor → Okta → Portal → domains (tight gaps)."""
    leaves = [
        c for c in cells
        if c.get("vertex") == "1" and c.get("edge") != "1" and not _is_container_cell(c)
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
        and not (c.get("id") or "").startswith("step_badge_")
    ]
    actors = [c for c in leaves if _is_logical_actor_cell(c)]
    channels = [c for c in leaves if _is_logical_channel_cell(c)]
    id_saas = [c for c in leaves if _is_identity_saas_cell(c)]
    other_saas = [
        c for c in leaves
        if _is_saas_leaf_cell(c) and c not in id_saas
    ]

    # Hoist spine leaves to parent=1 with absolute coords so gaps are controllable.
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    changed = False
    for cell in actors + channels + id_saas + other_saas:
        if cell.get("parent") not in (None, "1"):
            # Keep non-identity SaaS nested in domain shelves.
            if cell in other_saas:
                continue
            # Hoist actors, portals, and Okta onto the journey spine.
            ax, ay = absolute_position(cell, by_id)
            cell.set("parent", "1")
            geom = cell.find("mxGeometry")
            if geom is not None:
                geom.set("x", str(ax))
                geom.set("y", str(ay))
            changed = True

    # Refresh lists for parent=1 placement
    actors = [c for c in actors if c.get("parent") == "1"]
    channels = [c for c in channels if c.get("parent") == "1"]
    id_saas = [c for c in id_saas if c.get("parent") == "1"]

    spine_y = 100.0
    x = 40.0
    gap = _LOGICAL_SPINE_GAP
    icon = 68.0

    def _park(group: list[ET.Element], start_x: float) -> float:
        nonlocal changed
        cx = start_x
        for i, cell in enumerate(group):
            geom = cell.find("mxGeometry")
            if geom is None:
                continue
            tx, ty = cx, spine_y + i * 0  # single row for spine roles
            if len(group) > 1 and group is actors:
                ty = spine_y + i * _SAAS_PITCH_Y
            try:
                cur_x = float(geom.get("x") or 0)
                cur_y = float(geom.get("y") or 0)
            except ValueError:
                cur_x, cur_y = 0.0, 0.0
            if abs(cur_x - tx) > 1.0 or abs(cur_y - ty) > 1.0:
                geom.set("x", str(tx))
                geom.set("y", str(ty))
                geom.set("width", str(icon))
                geom.set("height", str(icon))
                changed = True
            if cell.get("spine") != "1":
                cell.set("spine", "1")
                changed = True
            if group is actors and len(group) > 1:
                continue  # stacked — same x
            cx += icon + gap
        if group is actors and group:
            return start_x + icon + gap
        return cx if group else start_x

    x = _park(actors[:2], x)  # at most two actor icons on spine
    x = _park(id_saas[:2], x)
    x = _park(channels[:2], x)

    # Domains are placed by apply_layered_corridor_layout (incl. SaaS under Data).
    # If the leftmost ranked domain still overlaps the spine, shift the whole
    # domain band (and any SaaS shelf under it) right together.
    domains = [
        c for c in cells
        if _is_container_cell(c)
        and c.get("parent") == "1"
        and (c.get("id") or "").startswith("domain_")
        and not (c.get("id") or "").startswith("domain_saas")
    ]
    shelves = [
        c for c in cells
        if _is_container_cell(c)
        and (c.get("id") or "").startswith("domain_saas")
    ]
    min_domain_x = max(x + 20.0, 280.0)
    left_xs = [
        (_cell_geometry(c) or (min_domain_x, 0, 0, 0))[0] for c in domains
    ]
    if left_xs:
        leftmost = min(left_xs)
        if leftmost < min_domain_x - 1.0:
            shift = min_domain_x - leftmost
            for cell in domains + shelves:
                geom = cell.find("mxGeometry")
                geo = _cell_geometry(cell)
                if geom is None or geo is None:
                    continue
                geom.set("x", str(geo[0] + shift))
                changed = True

    return changed


def _ensure_logical_journey_edges(root: ET.Element, cells: list[ET.Element]) -> bool:
    """Add missing Actor→Okta and Okta→Portal edges for a readable auth journey."""
    actors = [c for c in cells if _is_logical_actor_cell(c)]
    channels = [c for c in cells if _is_logical_channel_cell(c)]
    id_saas = [c for c in cells if _is_identity_saas_cell(c)]
    if not id_saas:
        return False

    def _has_edge(a: ET.Element, b: ET.Element) -> bool:
        aid, bid = a.get("id"), b.get("id")
        for e in cells:
            if e.get("edge") != "1":
                continue
            s, t = e.get("source"), e.get("target")
            if {s, t} == {aid, bid}:
                return True
        return False

    existing_ids = {c.get("id") for c in cells if c.get("id")}
    changed = False
    n = 0

    def _add(src: ET.Element, tgt: ET.Element, label: str) -> None:
        nonlocal n, changed
        if _has_edge(src, tgt):
            return
        sid, tid = src.get("id"), tgt.get("id")
        if not sid or not tid:
            return
        eid = f"logical_journey_e{n}"
        n += 1
        while eid in existing_ids:
            eid = f"logical_journey_e{n}"
            n += 1
        existing_ids.add(eid)
        edge = ET.SubElement(root, "mxCell")
        edge.set("id", eid)
        edge.set("edge", "1")
        edge.set("parent", "1")
        edge.set("source", sid)
        edge.set("target", tid)
        edge.set("value", label)
        edge.set(
            "style",
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            "strokeWidth=2;labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;",
        )
        geom = ET.SubElement(edge, "mxGeometry")
        geom.set("relative", "1")
        geom.set("as", "geometry")
        changed = True

    okta = id_saas[0]
    if actors:
        _add(actors[0], okta, "Authenticates via")
    portals = list(channels)
    if not portals:
        # "Application" / experience caps when LLM skipped portal wording.
        from app.adapters.outbound.diagram.architecture_docs import _logical_leaf_role
        for c in cells:
            if c.get("vertex") != "1" or c.get("edge") == "1" or _is_container_cell(c):
                continue
            if _logical_leaf_role(_plain_label(c)) == "channel":
                portals.append(c)
    if portals:
        _add(okta, portals[0], "Redirects to")
    return changed


def resolve_misplaced_saas_leaves(xml: str) -> str:
    """Keep vendor SaaS outside VPC; on Network place by call path (ingress/egress).

    Network: IdP/user-facing SaaS left of Cloud; Cloud→SaaS dependencies on the right.
    Other pages: hoist out of VPC/Cloud/Integration only (corridor L→R is separate).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    saas_leaves = [c for c in cells if _is_saas_leaf_cell(c)]
    if not saas_leaves and not any(_is_mega_saas_container(c) for c in cells):
        return xml

    from app.adapters.outbound.diagram.xml_diagnostics import _page_size

    page_w, page_h = _page_size(tree)
    changed = _stamp_saas_types(saas_leaves)

    if _is_network_page(tree):
        if _place_network_saas_lane(
            tree, root, cells, by_id, saas_leaves, page_w=page_w, page_h=page_h,
        ):
            changed = True
    else:
        if _reparent_forbidden_saas(saas_leaves, by_id):
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)
