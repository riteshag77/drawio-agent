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

    # The Network compiler places every cell itself and marks the default
    # layer layout="ir" when it did — this heuristic exists to fix LLM-guessed
    # nesting, not to second-guess a compiler that already nested hubs
    # correctly.
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

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

    # Same reasoning as resolve_misplaced_connectivity_hubs: skip pages the
    # Network compiler already placed itself (layout="ir").
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

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

    # Same reasoning as resolve_misplaced_connectivity_hubs: skip pages the
    # Network compiler already placed itself (layout="ir").
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

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
# Logical domain app grid / corridor gap: uses logical_layout.py's canonical
# CONTAINER_PADDING_X/CONTAINER_LABEL_HEIGHT/ICON_SPACING/INTER_CONTAINER_GAP
# now (imported where needed) — the old locally-duplicated _LOGICAL_APP_*/
# _LOGICAL_DOMAIN_GAP constants (pitch 220/150 vs the canonical set's
# ICON_SPACING-derived pitch, and a separately-tracked 110 for the gap) were
# retired so this file has exactly one source of truth for corridor/shelf
# sizing, matching logical_layout.py's own single-source-of-truth block.
# Spine icon gap: enough horizontal run for "Authenticates via" without
# painting the label over the persona / IdP icons.
_LOGICAL_SPINE_GAP = 120.0
# Obstacle clearance used by the Logical edge router — 20px minimum per the
# user's edge-routing requirements (Fix 2d).
_LOGICAL_ROUTE_CLEARANCE = 20.0
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
    page_w: float = 0.0,
    page_h: float = 0.0,
) -> list[tuple[float, float, float, float]]:
    """Absolute leaf boxes (incl. under-icon label band) an edge must not cross."""
    from app.adapters.outbound.diagram.xml_diagnostics import _band_height, _is_canvas_background_cell

    rects: list[tuple[float, float, float, float]] = []
    for cell in cells:
        cid = cell.get("id") or ""
        if cid in skip_ids:
            continue
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            continue
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            continue
        # Found via real-capture replay: without this, the full-page
        # background rect (e.g. a 3586x1400 opaque backdrop) is treated as a
        # single giant obstacle, forcing every detour band computation
        # (_route_candidates) to the extreme edge of the whole canvas instead
        # of a sane local detour.
        if _is_canvas_background_cell(cell, page_w, page_h):
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
        # Only offer the trivial straight line when it's actually clear — if it's
        # blocked, letting it into the candidate pool lets it win ties against
        # every detour below (it's evaluated first, and _path_blocked_count's
        # coarse "1 blocked segment" often ties with a detour that's ALSO
        # partially blocked), which is how an edge ends up drawn straight
        # through the center of an icon instead of jogging around it.
        if abs(sy - ey) < 1.0 and not _segment_blocked(start, end, obstacles, _LOGICAL_ROUTE_CLEARANCE):
            out.append([start, end])
        # Same-row hop (source/target level, e.g. two icons in one corridor
        # with a third sitting between them): a mid-shift jog is a zero-height
        # no-op at this y, so it can never dodge anything on this row — the
        # only way over/under a same-row neighbor is an actual vertical
        # offset, tried at increasing distances before the whole-corridor
        # under-band detour below (which computes its band from every
        # obstacle/frame in the x-span and can land far outside the visible
        # area for a wide row, crossing even more unrelated shapes).
        if abs(sy - ey) < 1.0:
            for dy in (60.0, -60.0, 96.0, -96.0, 140.0, -140.0):
                jy = sy + dy
                out.append([start, (sx, jy), (ex, jy), end])
        mid = (sx + ex) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            mx = mid + shift
            out.append([start, (mx, sy), (mx, ey), end])
        # Y-proximity window on top of the existing X-range filter: without
        # it, any container that merely shares the edge's X-span — however
        # far away vertically (a whole other zone/row) — drags top/bottom to
        # its own extreme, pushing the detour band far outside the visible
        # area (found via real-capture replay: a same-row cross-zone edge on
        # a wide Security page computed a band at y=1444 on a ~600px-tall
        # canvas, crossing straight through unrelated shapes near that band).
        y_window = 320.0
        span = [
            r for r in obstacles + frames
            if min(sx, ex) - 40 < r[0] + r[2] and r[0] < max(sx, ex) + 40
            and min(sy, ey) - y_window < r[1] + r[3] and r[1] < max(sy, ey) + y_window
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
        if abs(sx - ex) < 1.0 and not _segment_blocked(start, end, obstacles, _LOGICAL_ROUTE_CLEARANCE):
            out.append([start, end])
        # Mirrors the "h" branch's same-column fix above.
        if abs(sx - ex) < 1.0:
            for dx in (60.0, -60.0, 96.0, -96.0, 140.0, -140.0):
                jx = sx + dx
                out.append([start, (jx, sy), (jx, ey), end])
        mid = (sy + ey) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            my = mid + shift
            out.append([start, (sx, my), (ex, my), end])
        # X-proximity window — mirrors the "h" branch's fix above.
        x_window = 320.0
        span = [
            r for r in obstacles + frames
            if min(sy, ey) - 40 < r[1] + r[3] and r[1] < max(sy, ey) + 40
            and min(sx, ex) - x_window < r[0] + r[2] and r[0] < max(sx, ex) + x_window
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


def repark_edge_labels_after_external_routing(xml: str) -> str:
    """Re-park label offsets onto the longest clear run of an externally-routed page.

    Used after libavoid (MCP ``create_diagram(routing="libavoid")``) rewrites a
    Logical/Security/Data-flow page's edges — that call is now the primary
    router for these pages (see ``xml_pipeline`` libavoid gating), but it has
    no notion of *this app's* label-placement convention. Unlike
    ``fix_edge_anchors_from_geometry``'s focused-view branch, this does **not**
    recompute routing — it only reads the waypoints libavoid already wrote and
    repositions each label onto the longest straight segment, so text lands in
    open space instead of on an icon or a bend.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return xml
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    edge_meta: dict[str, dict] = {}
    routed_paths: dict[str, list[tuple[float, float]]] = {}
    for edge in edges:
        eid = edge.get("id") or ""
        src_id, tgt_id = edge.get("source"), edge.get("target")
        if not eid or not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
            continue
        sx, sy = absolute_position(by_id[src_id], by_id)
        tx, ty = absolute_position(by_id[tgt_id], by_id)
        sgeo = _cell_geometry(by_id[src_id])
        tgeo = _cell_geometry(by_id[tgt_id])
        sw, sh = (sgeo[2], sgeo[3]) if sgeo else (78.0, 78.0)
        tw, th = (tgeo[2], tgeo[3]) if tgeo else (78.0, 78.0)
        edge_meta[eid] = {
            "scx": sx + sw / 2, "scy": sy + sh / 2,
            "tcx": tx + tw / 2, "tcy": ty + th / 2,
        }
        pts: list[tuple[float, float]] = []
        geom = edge.find("mxGeometry")
        if geom is not None:
            for arr in geom.findall("Array"):
                if arr.get("as") != "points":
                    continue
                for pt in arr.findall("mxPoint"):
                    try:
                        pts.append((float(pt.get("x") or 0), float(pt.get("y") or 0)))
                    except ValueError:
                        pass
        routed_paths[eid] = pts

    if _park_edge_labels_on_longest_segment(edges, edge_meta, routed_paths):
        return _serialize_docroot(tree, root, is_mxfile, trimmed)
    return xml


def fix_edge_anchors_from_geometry(xml: str) -> str:
    """Set exit/entry anchors from relative source→target placement + parallel offset.

    On Logical pages, also inserts staggered top/side waypoints so connectors
    do not run straight through intervening service icons.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    from app.adapters.outbound.diagram.xml_diagnostics import _page_size

    page_w, page_h = _page_size(tree)
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
                cells, by_id, {edge.get("source") or "", edge.get("target") or ""}, page_w, page_h
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
    """True for curated enterprise SaaS product leaves (not PrivateLink endpoints).

    Excludes the Overview Panel / legend by id — found via real-capture replay
    (Fix plan's final verification step): an LLM occasionally emits
    ``overview_panel`` with a stray leftover ``shape_type="saas_*"`` attribute
    (a copy-paste artifact from a nearby SaaS leaf, not a real classification
    choice), which without this guard gets reparented into a SaaS shelf and
    balloons it — the panel's own text-block geometry (hundreds of px) reads
    as a single oversized "icon" to the grid-sizing math.
    """
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    if (cell.get("id") or "") in {"overview_panel", "legend_box"}:
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
    # Security External SaaS Zone, and Data-flow's producer/consumer shelves,
    # are the correct homes for SaaS leaves — not a Network-style mega box
    # that should be emptied onto the canvas root. Found while verifying the
    # producer/consumer split (Fix 1): this "saas" in cid catch-all predated
    # those two ids and, unpatched, would hoist every leaf right back out of
    # them the moment they were created — undoing the split it never knew about.
    if cid.startswith("zone_external_saas") or (
        cid.startswith("zone_") and "external saas" in label
    ):
        return False
    if cid.startswith(("phase_saas_producer", "phase_saas_consumer")):
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
        ("identity", 2),
        ("partner", 3), ("b2b", 3),
        ("private", 4), ("restricted", 5), ("highly", 5), ("sensitive", 5),
        ("detect", 6), ("management", 6), ("monitoring", 6),
        ("external saas", 7), ("external_saas", 7), ("saas", 7),
    ]
    for needle, idx in order:
        if needle in blob:
            return (idx, label)
    return (4, label)


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


def _grid_logical_domain_apps(domain: ET.Element, cells: list[ET.Element]) -> bool:
    """Lay out non-SaaS leaves in a domain as a balanced grid, max 4 across.

    Mirrors Network's service-grid rule. ``domain_saas`` stays vertical via
    ``_sort_saas_children_in_shelf`` and is skipped here. Position/size math
    is the same canonical ``grid_fill``/``resize_container_to_grid`` every
    other corridor container uses (``logical_layout.py``) — this function
    keeps its own simpler stable-sort algorithm (no rank/barycenter ordering,
    unlike ``_layout_tiles_in_container``) since it runs as the thin-page
    fallback path, not the primary one, but there is no longer a second,
    independently-tuned set of pitch/padding constants behind it.
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
    from app.adapters.outbound.diagram.logical_layout import (
        CONTAINER_LABEL_HEIGHT,
        CONTAINER_PADDING_X,
        grid_cols,
        resize_container_to_grid,
    )
    from app.adapters.outbound.diagram import geometry_spec as geo

    changed = False
    cols_n = grid_cols(len(kids))
    # Canonical pitch (matches logical_layout._ICON_PITCH_X/Y) — not
    # ICON_SPACING + LEAF_W, since resolve_pitch_outliers/
    # resolve_moderate_pitch_deviations would just snap that back to this
    # value downstream anyway. Positioning here must already agree with the
    # canonical pitch, not merely converge to it after a repair pass.
    pitch_x = geo.DATA_CANONICAL_PITCH
    pitch_y = geo.ROW_PITCH_Y
    for i, kid in enumerate(kids):
        col = i % cols_n
        row = i // cols_n
        x = CONTAINER_PADDING_X + col * pitch_x
        y = CONTAINER_LABEL_HEIGHT + row * pitch_y
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
    shelf_geo = domain.find("mxGeometry")
    if shelf_geo is not None and resize_container_to_grid(shelf_geo, len(kids)):
        changed = True
    return changed


def _sort_saas_children_in_shelf(shelf: ET.Element, cells: list[ET.Element], *, vertical: bool = True) -> bool:
    """Grid-fill SaaS leaves in a shelf (wrap at grid_cols(), not one sparse column).

    ``vertical`` is accepted for call-site compatibility but no longer changes
    behavior — every call site always passed ``True``, and a forced single
    column regardless of leaf count was the direct cause of a shelf rendering
    far larger than its content (a 5-icon shelf needing 5 stacked rows instead
    of a compact 2-3 column grid). Sizing goes through the same canonical
    ``resize_container_to_grid`` every other corridor/shelf container uses —
    bidirectional (shrinks an oversized box too), not the old grow-only check.
    """
    from app.adapters.outbound.diagram.architecture_docs import saas_journey_sort_key
    from app.adapters.outbound.diagram.logical_layout import (
        CONTAINER_LABEL_HEIGHT,
        CONTAINER_PADDING_X,
        grid_cols,
        resize_container_to_grid,
    )
    from app.adapters.outbound.diagram import geometry_spec as geo

    sid = shelf.get("id") or ""
    kids = [c for c in cells if c.get("parent") == sid and _is_saas_leaf_cell(c)]
    if len(kids) < 1:
        return False
    kids.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))
    changed = False
    cols_n = grid_cols(len(kids))
    # Canonical pitch (geo.DATA_CANONICAL_PITCH/ROW_PITCH_Y) — same value
    # resize_container_to_grid sizes the shelf box for below, and the same
    # value xml_diagnostics's pitch-snap fixers enforce for every other leaf
    # grid in the app, so this shelf's positions are never overwritten by a
    # downstream pass expecting a different pitch (see logical_layout.py's
    # _ICON_PITCH_X/Y comment for why 118px — ICON_SPACING + LEAF_W — was
    # dropped in favor of this).
    pitch_x = geo.DATA_CANONICAL_PITCH
    pitch_y = geo.ROW_PITCH_Y
    for i, kid in enumerate(kids):
        col, row = i % cols_n, i // cols_n
        x = CONTAINER_PADDING_X + col * pitch_x
        y = CONTAINER_LABEL_HEIGHT + row * pitch_y
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
    shelf_geo = shelf.find("mxGeometry")
    if shelf_geo is not None and resize_container_to_grid(shelf_geo, len(kids)):
        changed = True
    return changed


_CORRIDOR_CONTAINER_STYLE = {
    "logical": (
        "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
        "collapsible=0;recursiveResize=0;fillColor=none;strokeColor=#6C8EBF;"
        "strokeWidth=1;dashed=1;align=left;verticalAlign=top;spacingLeft=12;"
        "spacingTop=8;fontStyle=1;fontSize=12;fontColor=#6C8EBF;"
    ),
    "security": (
        "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
        "collapsible=0;recursiveResize=0;fillColor=none;strokeColor=#B85450;"
        "strokeWidth=1;dashed=1;align=left;verticalAlign=top;spacingLeft=12;"
        "spacingTop=8;fontStyle=1;fontSize=12;fontColor=#B85450;"
    ),
    "data flow": (
        "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
        "collapsible=0;recursiveResize=0;fillColor=none;strokeColor=#82B366;"
        "strokeWidth=1;dashed=1;align=left;verticalAlign=top;spacingLeft=12;"
        "spacingTop=8;fontStyle=1;fontSize=12;fontColor=#82B366;"
    ),
}
_CORRIDOR_CONTAINER_STYLE["data_flow"] = _CORRIDOR_CONTAINER_STYLE["data flow"]

# Per-bucket-id (fillColor, strokeColor) — matches each view pack's own documented
# "Section colors" table exactly (config/xml_gen/views/logical|security|data_flow.txt).
# Auto-created containers (_synthesize_corridors, resolve_misplaced_security_controls)
# previously stamped one flat per-page style regardless of which specific domain/zone/
# phase they were, and dropped the fill entirely — every LLM-emitted, prompt-conformant
# container uses a real hex fill, not fillColor=none.
_ZONE_COLORS: dict[str, tuple[str, str]] = {
    # logical
    "domain_experience": ("#F5F5F5", "#666666"),
    "domain_identity": ("#E1D5E7", "#9673A6"),
    "domain_core": ("#DAE8FC", "#6C8EBF"),
    "domain_commerce": ("#DAE8FC", "#6C8EBF"),
    "domain_platform": ("#DAE8FC", "#147EBA"),
    "domain_data": ("#D5E8D4", "#82B366"),
    "domain_saas": ("#FFF2CC", "#D6B656"),
    # security
    "zone_public": ("#FFF2CC", "#D6B656"),
    "zone_dmz": ("#FFF2CC", "#D6B656"),
    "zone_identity": ("#E1D5E7", "#9673A6"),
    "zone_partner": ("#D5E8D4", "#82B366"),
    "zone_private": ("#DAE8FC", "#6C8EBF"),
    "zone_restricted": ("#F8CECC", "#B85450"),
    "zone_detect": ("#DAE8FC", "#147EBA"),
    "zone_external_saas": ("#FFF2CC", "#D6B656"),
    # data flow
    "phase_ingest": ("#FFF2CC", "#D6B656"),
    "phase_process": ("#DAE8FC", "#6C8EBF"),
    "phase_store": ("#D5E8D4", "#82B366"),
    "phase_consume": ("#D5E8D4", "#82B366"),
    "phase_control": ("#DAE8FC", "#147EBA"),
    "phase_saas": ("#FFF2CC", "#D6B656"),
}


def _zone_colors_for(bucket_id: str) -> tuple[str, str] | None:
    """(fillColor, strokeColor) for a bucket id, matched by exact id or prefix
    (so a de-duplicated id like ``domain_core_2`` still resolves)."""
    colors = _ZONE_COLORS.get(bucket_id)
    if colors is not None:
        return colors
    for prefix, pair in _ZONE_COLORS.items():
        if bucket_id.startswith(prefix):
            return pair
    return None


def _corridor_style_for(page_name: str, bucket_id: str) -> str:
    """Container style for a specific domain/zone/phase bucket id.

    Looks up ``_ZONE_COLORS`` by the id's own prefix (so a de-duplicated id like
    ``domain_core_2`` still resolves) and falls back to the flat per-page style
    only when the id has no table entry.
    """
    colors = _zone_colors_for(bucket_id)
    if colors is None:
        return _CORRIDOR_CONTAINER_STYLE.get(page_name, _CORRIDOR_CONTAINER_STYLE["logical"])
    fill, stroke = colors
    return (
        "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
        f"collapsible=0;recursiveResize=0;fillColor={fill};strokeColor={stroke};"
        "strokeWidth=1;dashed=1;align=left;verticalAlign=top;spacingLeft=12;"
        f"spacingTop=8;fontStyle=1;fontSize=12;fontColor={stroke};"
    )


def normalize_corridor_colors(xml: str) -> str:
    """Force every domain_/zone_/phase_ container's color to its canonical
    Section-colors value, regardless of who created it.

    Part 7b's ``_ZONE_COLORS`` table only applied to containers this pipeline
    creates itself (``_synthesize_corridors``, ``resolve_misplaced_security_
    controls``). The far more common case — the LLM emits the container
    itself — was left with whatever style the model wrote (often
    ``fillColor=none``), since nothing normalized it afterward. Authoritative:
    overwrites existing fill/stroke/font color keys rather than filling gaps.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return xml
    prefix = {
        "logical": "domain_", "security": "zone_",
        "data flow": "phase_", "data_flow": "phase_",
    }[page_name]

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    changed = False
    for cell in cells:
        cid = cell.get("id") or ""
        if not cid.lower().startswith(prefix) or not _is_container_cell(cell):
            continue
        colors = _zone_colors_for(cid.lower())
        if colors is None:
            continue
        fill, stroke = colors
        style = cell.get("style") or ""
        new_style = _set_style_kv(style, {
            "fillColor": fill, "strokeColor": stroke, "fontColor": stroke,
        })
        if new_style != style:
            cell.set("style", new_style)
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _leaf_corridor_bucket(page_name: str, cell: ET.Element) -> tuple[str, str, int]:
    """(container_id, container_label, sort_order) bucket for an ungrouped leaf.

    Keyword classifier mirroring the container-level ``_domain_corridor_key`` /
    ``_zone_corridor_key`` / ``_phase_corridor_key`` above, but applied to a
    *leaf* service label/shape_type rather than a container's own label —
    used only by ``_synthesize_corridors`` when the LLM emitted no corridor
    containers at all (leaves sitting flat on ``parent="1"``).
    """
    label = _plain_label(cell)
    blob = f"{_shape(cell)} {label}"
    if page_name == "logical":
        if any(k in blob for k in ("idp", "okta", "sso", "identity provider")):
            return ("domain_identity", "Identity", 1)
        from app.adapters.outbound.diagram.architecture_docs import _logical_leaf_role
        role = _logical_leaf_role(label)
        return {
            "actor": ("domain_experience", "Experience", 0),
            "channel": ("domain_experience", "Experience", 0),
            "saas": ("domain_saas", "External SaaS", 5),
            "platform": ("domain_platform", "Platform", 3),
            "datastore": ("domain_data", "Data", 4),
        }.get(role, ("domain_core", "Core", 2))
    if page_name == "security":
        if any(k in blob for k in (
            "waf", "shield", "cdn", "cloudfront", "front door", "api gateway",
            "alb", "load balancer", "application gateway",
        )):
            return ("zone_dmz", "Perimeter", 1)
        if any(k in blob for k in ("idp", "okta", "iam", "identity", "sso", "cognito")):
            return ("zone_identity", "Identity", 2)
        if any(k in blob for k in (
            "broker", "brokers", "agency", "agencies", "distributor", "reseller",
            "supplier", "suppliers", "vendor portal", "partner", "b2b", "edi",
            "marketplace", "reinsurer", "reinsurance",
        )):
            return ("zone_partner", "Partner / B2B", 3)
        if any(k in blob for k in (
            "guardduty", "cloudwatch", "monitor", "inspector", "security hub", "detect",
        )):
            return ("zone_detect", "Detect & Respond", 6)
        if any(k in blob for k in ("saas", "salesforce", "workday", "successfactors", "guidewire")):
            return ("zone_external_saas", "External SaaS Zone", 7)
        if any(k in blob for k in ("restricted", "sensitive", "vault", "kms", "secrets")):
            return ("zone_restricted", "Restricted", 5)
        return ("zone_private", "Private", 4)
    # data flow / data_flow
    if any(k in blob for k in (
        "ingest", "ingress", "queue", "event", "kinesis", "kafka", "pubsub",
        "event hub", "sqs", "sns",
    )):
        return ("phase_ingest", "Ingest", 0)
    if any(k in blob for k in (
        "database", "cache", "redis", "warehouse", "bucket", "blob", " s3", "store", "persist",
    )):
        return ("phase_store", "Store", 2)
    if any(k in blob for k in (
        "analytics", "dashboard", "report", " bi ", "athena", "looker", "tableau",
        "quicksight", "consume",
    )):
        return ("phase_consume", "Consume", 3)
    return ("phase_process", "Process", 1)


def _synthesize_corridors(
    page_name: str,
    cells: list[ET.Element],
    root: ET.Element,
    by_id: dict[str, ET.Element],
    leaf_filter,
) -> list[ET.Element]:
    """Group ungrouped top-level leaves into fresh corridor containers.

    Deterministic safety net behind the view-pack prompt contract (which asks
    the LLM to emit ``domain_``/``zone_``/``phase_`` containers directly): if
    the LLM instead emitted a flat pile of leaves on ``parent="1"`` with no
    grouping at all, ``apply_layered_corridor_layout`` would have nothing to
    rank and the page would keep raw, unstructured LLM coordinates — the exact
    "capabilities not placed in a structured manner" defect this exists to fix.
    Only creates a corridor when at least two distinct buckets are found;
    a single bucket is not worth a synthetic container.
    """
    loose = [
        c for c in cells
        if c.get("parent") == "1" and leaf_filter(c) and not _is_saas_leaf_cell(c)
    ]
    if not loose:
        return []
    buckets: dict[str, list[ET.Element]] = {}
    meta: dict[str, tuple[str, int]] = {}
    for cell in loose:
        cid, label, order = _leaf_corridor_bucket(page_name, cell)
        buckets.setdefault(cid, []).append(cell)
        meta[cid] = (label, order)
    if len(buckets) < 2:
        return []

    existing_ids = set(by_id.keys())
    created: list[ET.Element] = []
    for cid, kids in sorted(buckets.items(), key=lambda kv: meta[kv[0]][1]):
        label, _order = meta[cid]
        container_id = cid
        n = 1
        while container_id in existing_ids:
            n += 1
            container_id = f"{cid}_{n}"
        existing_ids.add(container_id)
        container = ET.Element("mxCell")
        container.set("id", container_id)
        container.set("value", label)
        container.set("style", _corridor_style_for(page_name, cid))
        container.set("vertex", "1")
        container.set("parent", "1")
        geom = ET.SubElement(container, "mxGeometry")
        geom.set("x", "160")
        geom.set("y", "140")
        geom.set("width", "260")
        geom.set("height", "200")
        geom.set("as", "geometry")
        root.append(container)
        by_id[container_id] = container
        created.append(container)
        for kid in kids:
            kid.set("parent", container_id)
    return created


_SAAS_PRODUCER_SHELF_ID = "phase_saas_producer"
_SAAS_CONSUMER_SHELF_ID = "phase_saas_consumer"


def _classify_data_flow_saas_direction(
    leaf: ET.Element,
    edges: list[tuple[str, str]],
    inbound_target_ids: set[str],
    outbound_source_ids: set[str],
) -> str:
    """producer = feeds data INTO the pipeline; consumer = pipeline sends data OUT to it.

    Edge direction decides first, the same edges-before-defaults order
    ``resolve_saas_network_side`` uses for Network's ingress/egress axis (a
    different question — this doesn't reuse that function, only mirrors its
    pattern): an edge from the SaaS leaf into ingest/process means the vendor
    is a source (producer); an edge from process/store out to the SaaS leaf
    means the pipeline calls out to it (consumer). No edges → fall back to
    ``saas_default_data_flow_direction``'s vendor-role table.
    """
    leaf_id = leaf.get("id") or ""
    for source_id, target_id in edges:
        if source_id == leaf_id and target_id in inbound_target_ids:
            return "producer"
        if target_id == leaf_id and source_id in outbound_source_ids:
            return "consumer"
    from app.adapters.outbound.diagram.architecture_docs import saas_default_data_flow_direction
    return saas_default_data_flow_direction(_shape(leaf), _plain_label(leaf))


def _consolidate_data_flow_saas_shelf(
    root: ET.Element,
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
) -> list[ET.Element]:
    """Pull SaaS producer/consumer leaves out of every other phase and into
    dedicated, vertically-stacked ``phase_saas_producer``/``phase_saas_consumer``
    shelves (mirrors Logical's ``domain_saas``), instead of leaving them
    "in-phase" mixed with cloud-native leaves. Catches SaaS leaves under
    **any** other ``phase_*`` container (not just ingest/process/store) — a
    SaaS leaf the LLM parented under ``phase_consume`` previously survived
    untouched. Each leaf is classified by direction (see
    ``_classify_data_flow_saas_direction``) and reparented into the matching
    shelf. Returns the list of newly-created shelf containers (possibly
    empty even when leaves were reparented, if both shelves already existed).
    """
    saas_leaves = [
        c for c in cells
        if _is_saas_leaf_cell(c)
        and (c.get("parent") == "1" or (
            by_id.get(c.get("parent") or "") is not None
            and (by_id[c.get("parent")].get("id") or "").lower().startswith("phase_")
            and not (by_id[c.get("parent")].get("id") or "").lower().startswith("phase_saas")
        ))
    ]
    if not saas_leaves:
        return []

    from app.adapters.outbound.diagram.logical_layout import build_edge_pairs

    def _phase_id_of(cell: ET.Element) -> str:
        parent = by_id.get(cell.get("parent") or "")
        return (parent.get("id") or "").lower() if parent is not None else ""

    edges = build_edge_pairs(cells)
    inbound_target_ids = {c.get("id") or "" for c in cells if _phase_id_of(c) in {"phase_ingest", "phase_process"}}
    outbound_source_ids = {c.get("id") or "" for c in cells if _phase_id_of(c) in {"phase_process", "phase_store"}}

    created: list[ET.Element] = []
    for leaf in saas_leaves:
        direction = _classify_data_flow_saas_direction(leaf, edges, inbound_target_ids, outbound_source_ids)
        shelf_id = _SAAS_PRODUCER_SHELF_ID if direction == "producer" else _SAAS_CONSUMER_SHELF_ID
        shelf = by_id.get(shelf_id)
        if shelf is None:
            shelf = ET.Element("mxCell")
            shelf.set("id", shelf_id)
            shelf.set("value", "SaaS Producers" if direction == "producer" else "SaaS Consumers")
            shelf.set("style", _corridor_style_for("data flow", shelf_id))
            shelf.set("vertex", "1")
            shelf.set("parent", "1")
            geom = ET.SubElement(shelf, "mxGeometry")
            geom.set("x", "160")
            geom.set("y", "140")
            geom.set("width", "180")
            geom.set("height", "200")
            geom.set("as", "geometry")
            root.append(shelf)
            by_id[shelf_id] = shelf
            created.append(shelf)
        leaf.set("parent", shelf_id)
    return created


_SECURITY_CANONICAL_ZONE_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("waf", "shield", "cloudfront", "front door", "cloud armor", "api gateway",
         "alb", "load balancer", "application gateway"),
        "zone_dmz", "Perimeter",
    ),
    (("idp", "okta", "cognito", "sso"), "zone_identity", "Identity"),
    (
        ("guardduty", "cloudwatch", "inspector", "security hub", "detect"),
        "zone_detect", "Detect & Respond",
    ),
    (("secrets manager", "secrets", "kms", "key vault", "vault"), "zone_private", "Private"),
    (
        ("broker", "agency", "agencies", "distributor", "reseller", "supplier",
         "vendor portal", "partner", "b2b", "edi", "marketplace", "reinsurer",
         "reinsurance"),
        "zone_partner", "Partner / B2B",
    ),
)


def _canonical_security_zone(cell: ET.Element) -> tuple[str, str] | None:
    """High-confidence-only canonical zone for a known security-control leaf.

    Deliberately narrower than ``_leaf_corridor_bucket``'s security branch (which has
    a catch-all default of zone_private) — this only reparents leaves matching a
    specific, unambiguous keyword. A generic/ambiguous leaf is left exactly where the
    LLM or corridor synthesis put it.
    """
    blob = f"{_shape(cell)} {_plain_label(cell)}"
    for needles, zone_id, zone_label in _SECURITY_CANONICAL_ZONE_RULES:
        if any(n in blob for n in needles):
            return zone_id, zone_label
    return None


def resolve_misplaced_security_controls(xml: str) -> str:
    """Reparent well-known security-control leaves to their canonical trust zone.

    Mirrors Network's resolve_misplaced_connectivity_hubs / _edge_services /
    _load_balancers pattern: the LLM sometimes places a known control (e.g. Secrets
    Manager) in a plausible-but-wrong zone (Identity, instead of Private next to the
    app it protects) and nothing previously corrected it. Runs before
    ``enforce_view_journey_columns``'s corridor ranking so a misplacement like this
    can never scramble the canonical zone order (see that function's sort-key note).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    if _diagram_page_name(tree).lower() != "security":
        return xml

    # Security's IR/layered compiler already places every leaf in its
    # validated zone and marks the default layer layout="ir" — this
    # keyword-matching heuristic exists to fix LLM-guessed zoning, not to
    # override a compiler that already got it right (and validated).
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    changed = False
    for cell in cells:
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            continue
        cid = cell.get("id") or ""
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            continue  # UI chrome, not a service leaf — its narrative text can
            # innocently mention "WAF"/"CloudWatch"/etc. and must never be
            # keyword-matched into a zone (found via real-output review: this
            # was ballooning zone_dmz to ~4000px wide and causing edge crossings)
        if _is_saas_leaf_cell(cell):
            continue  # handled by hoist_security_saas_from_private_zones
        match = _canonical_security_zone(cell)
        if match is None:
            continue
        zone_id, zone_label = match
        if (cell.get("parent") or "") == zone_id:
            continue

        zone = by_id.get(zone_id)
        if zone is None:
            zone = ET.Element("mxCell")
            zone.set("id", zone_id)
            zone.set("value", zone_label)
            zone.set("style", _corridor_style_for("security", zone_id))
            zone.set("vertex", "1")
            zone.set("parent", "1")
            geom = ET.SubElement(zone, "mxGeometry")
            geom.set("x", "160")
            geom.set("y", "140")
            geom.set("width", "260")
            geom.set("height", "200")
            geom.set("as", "geometry")
            root.append(zone)
            by_id[zone_id] = zone
            cells.append(zone)
            changed = True

        cell.set("parent", zone_id)
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _remove_empty_corridor_containers(root: ET.Element, prefix: str) -> bool:
    """Delete any domain_/zone_/phase_ container left with zero leaf children.

    The view packs already tell the LLM to omit empty corridor shells, but nothing
    deterministically enforced it — a thin brief could still come back with a
    visually empty box (e.g. an unused domain/zone the LLM emitted out of habit).
    Must run **last**, after all reparenting/hoisting/synthesis, so it sees final
    occupancy rather than removing something about to receive a leaf.
    """
    removed_any = False
    while True:
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        parent_ids = {c.get("parent") for c in cells}
        removed_this_pass = False
        for cell in cells:
            cid = cell.get("id") or ""
            if not cid.lower().startswith(prefix):
                continue
            if not _is_container_cell(cell) or cell.get("parent") != "1":
                continue
            if cid in parent_ids:
                continue  # has at least one child
            root.remove(cell)
            removed_this_pass = True
            removed_any = True
        if not removed_this_pass:
            break
    return removed_any


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

    # Logical/Security/Data-flow's IR compiler is geometrically authoritative
    # (compiler/corridor.py's layered path, layout_corridor_ir) — it marks
    # the page's default layer `layout="ir"` when it produced the geometry
    # itself, and this whole function exists to fix LLM-guessed placement
    # that page never had. Re-running it would at best waste work re-deriving
    # a graph the compiler already had as typed objects, and at worst
    # silently rewrite already-correct geometry to a different (not
    # necessarily better) one.
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

    if page_name == "security":
        xml = hoist_security_saas_from_private_zones(xml)
        parsed = _parse_docroot(xml)
        if parsed is None:
            return xml
        tree, root, is_mxfile, trimmed = parsed

    from app.adapters.outbound.diagram.logical_layout import (
        INTER_CONTAINER_GAP,
        _is_data_flow_shelf,
        _is_external_saas_zone,
        _is_restricted_zone,
        _is_saas_domain,
        _is_saas_phase,
        _is_store_phase,
        apply_layered_corridor_layout,
    )
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

    def _is_layout_leaf(cell: ET.Element) -> bool:
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            return False
        cid = cell.get("id") or ""
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            return False
        return True

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
    if not corridors:
        # LLM emitted zero corridor containers — leaves are a flat pile on
        # parent="1". Synthesize domain_/zone_/phase_ buckets so the layered
        # layout engine always has real corridors to rank (see docstring on
        # _synthesize_corridors for why this matters).
        synthesized = _synthesize_corridors(page_name, cells, root, by_id, _is_layout_leaf)
        if synthesized:
            corridors = synthesized
            cells = [c for c in root.findall("mxCell") if c.get("id")]
            changed = True

    is_data_flow_page = page_name in {"data flow", "data_flow"}
    if is_data_flow_page:
        # Pull SaaS producers/consumers out of ingest/process/store into their
        # own dedicated, vertically-stacked shelf instead of leaving them
        # in-phase mixed with cloud-native leaves. Refresh by_id first — cells
        # may have been reparented/synthesized just above.
        by_id = {c.get("id"): c for c in cells if c.get("id")}
        saas_shelves = _consolidate_data_flow_saas_shelf(root, cells, by_id)
        if saas_shelves:
            cells = [c for c in root.findall("mxCell") if c.get("id")]
            existing_ids = {(c.get("id") or "") for c in corridors}
            for shelf in saas_shelves:
                if (shelf.get("id") or "") not in existing_ids:
                    corridors.append(shelf)
            changed = True

    gap = INTER_CONTAINER_GAP if page_name in {"logical", "security", "data flow", "data_flow"} else 40.0
    if is_data_flow_page:
        shelf_kwargs = {"shelf_filter": _is_data_flow_shelf, "shelf_anchor_filter": _is_store_phase}
    elif page_name == "security":
        shelf_kwargs = {
            "shelf_filter": _is_external_saas_zone, "shelf_anchor_filter": _is_restricted_zone,
        }
    else:
        shelf_kwargs = {}
    if corridors and apply_layered_corridor_layout(
        cells,
        corridors=corridors,
        leaf_filter=_is_layout_leaf,
        keyword_key=key_fn,
        start_x=160.0,
        gap=gap,
        mark_layout=True,
        **shelf_kwargs,
    ):
        changed = True
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        # Re-stack SaaS children vertically after the shelf was re-anchored.
        # Every view's SaaS shelf needs this, not just Logical's domain_saas —
        # _layout_tiles_in_container deliberately skips SaaS shelves' children
        # (they stay vertical, handled here instead), so without this restack
        # a shelf that just moved keeps stale child geometry from its old
        # position, which fit_containers_to_children then reads as "content
        # extent" and balloons the shelf box around (a real, previously
        # Data-flow/Security-only bug — Logical was the only view this ever
        # ran for). Only matches genuinely SaaS-only shelf ids — deliberately
        # excludes phase_consume, which _is_data_flow_shelf also matches but
        # which uses the normal tile grid, not a vertical SaaS stack.
        for cell in cells:
            if _is_saas_domain(cell) or _is_saas_phase(cell) or _is_external_saas_zone(cell):
                if _sort_saas_children_in_shelf(cell, cells, vertical=True):
                    changed = True
    else:
        # Fallback: previous keyword corridor + grid for thin pages
        vertical_saas = True
        if len(corridors) >= 2:
            corridors.sort(key=key_fn)
            x = 160.0
            for cell in corridors:
                cid_lower = (cell.get("id") or "").lower()
                if cid_lower.startswith("domain_saas") or (
                    is_data_flow_page and (
                        cid_lower.startswith("phase_consume") or cid_lower.startswith("phase_saas")
                    )
                ) or (page_name == "security" and cid_lower.startswith("zone_external_saas")):
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

    ranked_now = sorted(
        [c for c in cells if _is_container_cell(c) and c.get("parent") == "1"
         and (c.get("id") or "").lower().startswith(prefix)
         and (c.get("id") or "") not in {"overview_panel", "legend_box"}
         and not _is_anchored_shelf(page_name, c)],
        key=lambda c: (_cell_geometry(c) or (0.0, 0.0, 0.0, 0.0))[0],
    )
    connectivity_label = {
        "logical": "Invokes", "security": "private path",
        "data flow": "JSON: Payload", "data_flow": "JSON: Payload",
    }.get(page_name, "Invokes")
    control = None
    if is_data_flow_page:
        control = next(
            (c for c in cells if (c.get("id") or "").lower().startswith("phase_control")),
            None,
        )
    if _ensure_minimum_corridor_connectivity(
        root, cells, ranked_now, _is_layout_leaf, connectivity_label, control=control,
    ):
        changed = True
        cells = [c for c in root.findall("mxCell") if c.get("id")]

    if _remove_empty_corridor_containers(root, prefix):
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _is_anchored_shelf(page_name: str, cell: ET.Element) -> bool:
    """True for a corridor container anchored *under* another, not part of the L→R row.

    ``domain_saas`` (Logical) and ``phase_consume`` (Data flow) are positioned by
    ``_anchor_shelf_under``/``apply_layered_corridor_layout``'s shelf mechanism, not
    ranked in the horizontal row — ``enforce_corridor_gap_consistency`` must exclude
    them or it would re-flow them back into the row.
    """
    cid = (cell.get("id") or "").lower()
    if page_name == "logical":
        return cid.startswith("domain_saas") or ("saas" in cid and cid.startswith("domain_"))
    if page_name == "security":
        return cid.startswith("zone_external_saas") or ("saas" in cid and cid.startswith("zone_"))
    if page_name in {"data flow", "data_flow"}:
        return cid.startswith("phase_consume") or cid.startswith("phase_saas")
    return False


def enforce_corridor_gap_consistency(xml: str) -> str:
    """Re-flow domain_/zone_/phase_ corridors to a consistent gap after width changes.

    ``apply_layered_corridor_layout`` sets each corridor's x using the gap constant
    and whatever width the corridor had *at that moment*. ``fit_containers_to_children``
    runs later in ``repair_xml`` and can independently **shrink** a corridor's width
    (its own, separate width computation) — nothing re-flows the *next* corridor's x
    afterward, so the visible gap balloons by the shrink delta (found via live-
    generation review: a documented ~110px gap rendered as ~358px in practice). This
    re-spaces corridors using their now-final widths, preserving whatever left-to-right
    order was already established (sorts by current x — does not re-derive ranking),
    then re-aligns any anchored shelf (domain_saas / phase_consume) under its anchor's
    possibly-shifted x.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return xml

    # Same skip as enforce_view_journey_columns, same reason: this function
    # exists to re-flow gaps after a *width change* — the IR compiler's
    # layered path (layout_corridor_ir) never leaves a stale width for a
    # later pass to react to, and this function's own "always anchor under
    # the rightmost ranked corridor" shelf realignment (below) actively
    # disagrees with the IR's own validated anchor_id in some views (found
    # while building the new path — see test_corridor_ir_layout.py) — running
    # it would silently move an already-correct shelf to the wrong anchor.
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

    prefix = {
        "logical": "domain_", "security": "zone_",
        "data flow": "phase_", "data_flow": "phase_",
    }[page_name]
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    all_corridors = [
        c for c in cells
        if _is_container_cell(c) and c.get("parent") == "1"
        and (c.get("id") or "").lower().startswith(prefix)
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
    ]
    row = [c for c in all_corridors if not _is_anchored_shelf(page_name, c)]
    shelves = [c for c in all_corridors if _is_anchored_shelf(page_name, c)]
    if not row and not shelves:
        return xml

    from app.adapters.outbound.diagram.logical_layout import INTER_CONTAINER_GAP

    row.sort(key=lambda c: (_cell_geometry(c) or (0.0, 0.0, 0.0, 0.0))[0])
    changed = False
    if len(row) >= 2:
        first_geo = _cell_geometry(row[0])
        if first_geo is not None:
            cursor = first_geo[0] + first_geo[2]
            for cell in row[1:]:
                geo = _cell_geometry(cell)
                geom = cell.find("mxGeometry")
                if geo is None or geom is None:
                    continue
                target_x = cursor + INTER_CONTAINER_GAP
                if abs(geo[0] - target_x) > 1.0:
                    geom.set("x", str(target_x))
                    changed = True
                cursor = target_x + geo[2]

    # Re-align any anchored shelf under its anchor's (possibly shifted) x/y — the
    # anchor is the rightmost ranked corridor, matching _anchor_shelf_under's own
    # selection (domain_data / phase_store rank highest among non-shelf keys).
    # Fixes both axes: a prior version only corrected x, leaving a shelf whose
    # anchor grew/shrank in height (fit_containers_to_children) drifting far
    # below INTER_CONTAINER_GAP (found via live-generation review: domain_saas
    # ended up ~200px below domain_data's bottom instead of the intended 110px).
    if shelves and row:
        anchor_geo = _cell_geometry(row[-1])
        if anchor_geo is not None:
            # Stack with a running cursor rather than assigning every shelf the
            # same target_y — Data-flow can have >1 shelf under the same anchor
            # (phase_consume + phase_saas_producer + phase_saas_consumer), and
            # the previous single-target_y version collapsed all of them onto
            # identical coordinates (a real, previously undetected bug: this
            # function was written assuming at most one shelf per view, true
            # for Logical's lone domain_saas but never true for Data-flow).
            # `shelves` preserves document order, which is the compiler's
            # emission order — the same ordering contract _anchor_shelf_under
            # already relies on for its own stacking cursor.
            cursor_y = anchor_geo[1] + anchor_geo[3] + INTER_CONTAINER_GAP
            for shelf in shelves:
                shelf_geom = shelf.find("mxGeometry")
                shelf_geo = _cell_geometry(shelf)
                if shelf_geom is None or shelf_geo is None:
                    continue
                if abs(shelf_geo[0] - anchor_geo[0]) > 1.0:
                    shelf_geom.set("x", str(anchor_geo[0]))
                    changed = True
                if abs(shelf_geo[1] - cursor_y) > 1.0:
                    shelf_geom.set("y", str(cursor_y))
                    changed = True
                cursor_y += shelf_geo[3] + INTER_CONTAINER_GAP

    # Security/Data-flow have no journey-spine mechanism (Logical's is Logical-only),
    # so external actors the LLM placed at an arbitrary x can end up hundreds of
    # pixels from the first corridor once the row above shifts right (found via
    # live-generation review: Employees at x=20, zone_dmz pushed out to x=510).
    # Wrap them in a lightweight band container (Fix 2c) instead of leaving each
    # a bare parent="1" child at an independent y-offset — still parent="1"
    # itself (a peer of the main row, not logically inside the architecture,
    # same "never nested inside" semantic the old bare-actor placement had),
    # but grid-sized/aligned like every other container via
    # resize_container_to_grid so it stops floating at an arbitrary baseline.
    # Scoped to Security/Data-flow only — Logical's actor placement is a
    # structurally different spine mechanism (_layout_logical_journey_spine),
    # and Network is deferred per the overall plan; bolting this on either
    # risks breaking a distinct, working mechanism for a non-goal of this pass.
    if row and page_name in {"security", "data flow", "data_flow"}:
        by_id = {c.get("id"): c for c in cells if c.get("id")}
        changed = _containerize_corridor_actors(root, cells, by_id, row, page_name) or changed

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


_ACTORS_BAND_ID = "actors_left"


def _containerize_corridor_actors(
    root: ET.Element,
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
    row: list[ET.Element],
    page_name: str,
) -> bool:
    """Wrap bare parent="1" external actors in a single grid-sized band
    container, just left of the leftmost row corridor (Fix 2c)."""
    from app.adapters.outbound.diagram.logical_layout import (
        CONTAINER_LABEL_HEIGHT,
        CONTAINER_PADDING_X,
        INTER_CONTAINER_GAP,
        resize_container_to_grid,
    )
    from app.adapters.outbound.diagram import geometry_spec as geo
    from app.adapters.outbound.diagram.xml_diagnostics import _is_external_actor_cell

    band = by_id.get(_ACTORS_BAND_ID)
    actors = [
        c for c in cells
        if _is_external_actor_cell(c)
        and (c.get("parent") == "1" or c.get("parent") == _ACTORS_BAND_ID)
    ]
    if not actors:
        return False
    leftmost_geo = _cell_geometry(row[0])
    if leftmost_geo is None:
        return False

    changed = False
    if band is None:
        band = ET.Element("mxCell")
        band.set("id", _ACTORS_BAND_ID)
        band.set("value", "External Actors")
        band.set("style", _corridor_style_for(page_name, _ACTORS_BAND_ID))
        band.set("vertex", "1")
        band.set("parent", "1")
        geom = ET.SubElement(band, "mxGeometry")
        geom.set("x", "20")
        geom.set("y", str(leftmost_geo[1]))
        geom.set("width", "160")
        geom.set("height", "120")
        geom.set("as", "geometry")
        root.append(band)
        by_id[_ACTORS_BAND_ID] = band
        cells.append(band)
        changed = True

    pitch_y = geo.ROW_PITCH_Y
    for i, actor in enumerate(actors):
        if actor.get("parent") != _ACTORS_BAND_ID:
            actor.set("parent", _ACTORS_BAND_ID)
            changed = True
        a_geom = actor.find("mxGeometry")
        if a_geom is None:
            continue
        target_ax = CONTAINER_PADDING_X
        target_ay = CONTAINER_LABEL_HEIGHT + i * pitch_y
        try:
            cur_ax = float(a_geom.get("x") or 0)
            cur_ay = float(a_geom.get("y") or 0)
        except ValueError:
            cur_ax, cur_ay = 0.0, 0.0
        if abs(cur_ax - target_ax) > 0.5 or abs(cur_ay - target_ay) > 0.5:
            a_geom.set("x", str(target_ax))
            a_geom.set("y", str(target_ay))
            changed = True

    band_geom = band.find("mxGeometry")
    if band_geom is not None and resize_container_to_grid(band_geom, len(actors), cols=1):
        changed = True
    band_geo = _cell_geometry(band)
    if band_geom is not None and band_geo is not None:
        target_bx = max(20.0, leftmost_geo[0] - band_geo[2] - INTER_CONTAINER_GAP)
        target_by = leftmost_geo[1]
        if abs(band_geo[0] - target_bx) > 1.0:
            band_geom.set("x", str(target_bx))
            changed = True
        if abs(band_geo[1] - target_by) > 1.0:
            band_geom.set("y", str(target_by))
            changed = True
    return changed


def _is_logical_actor_cell(cell: ET.Element) -> bool:
    st = _shape(cell)
    if st == "logical_actor":
        return True
    from app.adapters.outbound.diagram.architecture_docs import _logical_leaf_role
    return _logical_leaf_role(_plain_label(cell)) == "actor"


def _is_logical_channel_cell(cell: ET.Element) -> bool:
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    cid = cell.get("id") or ""
    if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
        return False
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

    def _reachable_forward(src_id: str, tgt_id: str) -> bool:
        """BFS over existing directed edges — true if tgt is reachable from src
        via any path, not just a direct edge. The LLM may already have drawn
        Okta -> Portal -> App as a two-hop chain; synthesizing a *direct*
        Okta -> App edge on top of that (because the exact-pair check missed
        the chain) creates a redundant parallel route into the same node."""
        adj: dict[str, list[str]] = {}
        for e in cells:
            if e.get("edge") != "1":
                continue
            s, t = e.get("source"), e.get("target")
            if s and t:
                adj.setdefault(s, []).append(t)
        seen = {src_id}
        stack = list(adj.get(src_id, []))
        while stack:
            cur = stack.pop()
            if cur == tgt_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return False

    existing_ids = {c.get("id") for c in cells if c.get("id")}
    changed = False
    n = 0

    def _add(src: ET.Element, tgt: ET.Element, label: str) -> None:
        nonlocal n, changed
        sid0, tid0 = src.get("id"), tgt.get("id")
        if _has_edge(src, tgt) or (sid0 and tid0 and _reachable_forward(sid0, tid0)):
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
            cid = c.get("id") or ""
            if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
                continue  # UI chrome — its narrative text can innocently contain
                # "portal" ("access the portal...") and must never be mistaken
                # for the real spine node (found via live-generation review).
            if _logical_leaf_role(_plain_label(c)) == "channel":
                portals.append(c)
    if portals:
        _add(okta, portals[0], "Redirects to")

    # Portal -> first domain: the journey spine (Employees -> Okta -> Portal)
    # otherwise dead-ends at the portal leaf, leaving every domain container
    # unconnected to how a request actually reaches it (found via live-
    # generation review: portal parented on "1", never wired into any
    # domain_*). Pick the leftmost non-SaaS domain (the entry point in
    # reading order) and connect to its first leaf.
    if portals:
        domains = [
            c for c in cells
            if _is_container_cell(c) and c.get("parent") == "1"
            and (c.get("id") or "").lower().startswith("domain_")
            and not (c.get("id") or "").lower().startswith("domain_saas")
        ]
        if domains:
            domains.sort(key=lambda c: (_cell_geometry(c) or (0.0, 0.0, 0.0, 0.0))[0])
            entry_domain = domains[0]
            domain_leaves = [
                c for c in cells
                if c.get("parent") == entry_domain.get("id")
                and c.get("vertex") == "1" and c.get("edge") != "1"
            ]
            if domain_leaves:
                _add(portals[0], domain_leaves[0], "Routes to")
    return changed


def _ensure_minimum_corridor_connectivity(
    root: ET.Element,
    cells: list[ET.Element],
    row: list[ET.Element],
    leaf_filter,
    label: str,
    *,
    control: ET.Element | None = None,
) -> bool:
    """Guarantee at least one edge connects each adjacent corridor pair, and
    at least one edge touches the control-plane shelf if it has content.

    The prompt's own "Canonical data journey" / "REQUIRED companions" sections
    describe these edges (e.g. Experience→Core→Data in Logical, app→schema
    registry in Data-flow), but the LLM sometimes omits them entirely (found
    via live-generation review: zero edges between adjacent domains, zero
    edges touching ``phase_control``). Mirrors ``_ensure_logical_journey_edges``'s
    philosophy for the spine, extended one level further — a synthesized
    generic edge between two representative leaves beats a silently
    disconnected diagram.
    """
    if len(row) < 2 and control is None:
        return False

    def _leaves_in(cid: str) -> list[ET.Element]:
        return [c for c in cells if c.get("parent") == cid and leaf_filter(c)]

    adj: dict[str, list[str]] = {}
    for e in cells:
        if e.get("edge") != "1":
            continue
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, []).append(t)
            adj.setdefault(t, []).append(s)

    def _reachable(src_id: str, tgt_id: str) -> bool:
        seen = {src_id}
        stack = list(adj.get(src_id, []))
        while stack:
            cur = stack.pop()
            if cur == tgt_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return False

    existing_ids = {c.get("id") for c in cells if c.get("id")}
    changed = False
    n = 0

    def _add_edge(src: ET.Element, tgt: ET.Element, edge_label: str) -> None:
        nonlocal n, changed
        eid = f"corridor_flow_e{n}"
        n += 1
        while eid in existing_ids:
            eid = f"corridor_flow_e{n}"
            n += 1
        existing_ids.add(eid)
        edge = ET.SubElement(root, "mxCell")
        edge.set("id", eid)
        edge.set("edge", "1")
        edge.set("parent", "1")
        edge.set("source", src.get("id"))
        edge.set("target", tgt.get("id"))
        edge.set("value", edge_label)
        edge.set(
            "style",
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            "strokeWidth=2;labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;",
        )
        geom = ET.SubElement(edge, "mxGeometry")
        geom.set("relative", "1")
        geom.set("as", "geometry")
        changed = True

    for a, b in zip(row, row[1:]):
        a_leaves = _leaves_in(a.get("id") or "")
        b_leaves = _leaves_in(b.get("id") or "")
        if not a_leaves or not b_leaves:
            continue
        if any(_reachable(al.get("id") or "", bl.get("id") or "") for al in a_leaves for bl in b_leaves):
            continue
        _add_edge(a_leaves[0], b_leaves[0], label)

    if control is not None:
        control_leaves = _leaves_in(control.get("id") or "")
        if control_leaves:
            control_ids = {c.get("id") for c in control_leaves}
            touched = any(
                e.get("source") in control_ids or e.get("target") in control_ids
                for e in cells if e.get("edge") == "1"
            )
            if not touched:
                source_leaves: list[ET.Element] = []
                for c in reversed(row):
                    ls = _leaves_in(c.get("id") or "")
                    if ls:
                        source_leaves = ls
                        break
                if source_leaves:
                    _add_edge(source_leaves[0], control_leaves[0], "Validates Payload Schema")

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

    # Every IR compiler already places SaaS leaves correctly (Network's
    # ingress/egress split, the corridor views' domain_saas/zone_external_saas/
    # phase_saas_* shelves) and marks the default layer layout="ir" when it
    # did — this heuristic exists to fix LLM-guessed SaaS placement.
    default_layer = next((c for c in root.findall("mxCell") if c.get("id") == "1"), None)
    if default_layer is not None and default_layer.get("layout") == "ir":
        return xml

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
