"""Deterministic hierarchy/placement fixers (hubs, edge services, LB, anchors)."""

from __future__ import annotations

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
_LOGICAL_APP_MAX_COLS = 4
_LOGICAL_APP_PITCH_X = 180.0
_LOGICAL_APP_PITCH_Y = 120.0
_LOGICAL_APP_BASE_X = 24.0
_LOGICAL_APP_BASE_Y = 48.0
_ACTOR_COL_X = 20.0
_MEGA_SAAS_LABEL_HINTS = (
    "external saas", "enterprise saas", "saas apps", "saas applications",
    "third party saas", "3rd party saas",
)


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
    is_logical = _diagram_page_name(tree).lower() == "logical"
    parallel_px = _LOGICAL_PARALLEL_EDGE_OFFSET_PX if is_logical else _PARALLEL_EDGE_OFFSET_PX

    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    # Group edges by shared face for spread
    face_buckets: dict[tuple[str, str, str], list[ET.Element]] = {}
    corridor_buckets: dict[tuple[str, str, str], list[ET.Element]] = {}
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
        corridor_buckets.setdefault(corridor, []).append(edge)
        eid = edge.get("id") or id(edge)
        edge_meta[str(eid)] = {
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
    spreads = ("0.15", "0.35", "0.5", "0.65", "0.85") if is_logical else ("0.25", "0.5", "0.75")
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

    # CORE parallel-corridor offset: stagger Dx/Dy so stacked orthogonal paths separate.
    for corridor, group in corridor_buckets.items():
        if len(group) < 2:
            continue
        axis = corridor[0]
        n = len(group)
        for i, edge in enumerate(group):
            offset = (i - (n - 1) / 2.0) * parallel_px
            style = edge.get("style") or ""
            if axis == "h":
                updates = {"exitDy": str(offset), "entryDy": str(offset)}
            else:
                updates = {"exitDx": str(offset), "entryDx": str(offset)}
            edge.set("style", _set_style_kv(style, updates))
            changed = True

            # Logical: light elbow only for parallel edges (avoid huge detours).
            if not is_logical or n < 2:
                continue
            eid = edge.get("id") or ""
            meta = edge_meta.get(eid)
            if not meta:
                continue
            band = (i - (n - 1) / 2.0) * 28.0
            if meta["axis"] == "h":
                top = min(meta["sy"], meta["ty"])
                wy = top - 36.0 - abs(band)
                mid_x = (meta["scx"] + meta["tcx"]) / 2.0
                _set_edge_waypoints(edge, [(mid_x, wy)])
            else:
                left = min(meta["sx"], meta["tx"])
                wx = left - 36.0 - abs(band)
                mid_y = (meta["scy"] + meta["tcy"]) / 2.0
                _set_edge_waypoints(edge, [(wx, mid_y)])
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
        ("sensitive", 4),
    ]
    for needle, idx in order:
        if needle in blob:
            return (idx, label)
    return (3, label)


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
    """Lay out non-SaaS leaves in a domain: max 4 across, then wrap to next row.

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
    for i, kid in enumerate(kids):
        col = i % _LOGICAL_APP_MAX_COLS
        row = i // _LOGICAL_APP_MAX_COLS
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
        rows = (len(kids) - 1) // _LOGICAL_APP_MAX_COLS + 1
        cols = min(len(kids), _LOGICAL_APP_MAX_COLS)
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
    """L→R corridor for Logical domains / Security zones / Data phases + SaaS in shelf."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return xml

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
    # Logical: vertical SaaS column. Other focused views: keep vertical too for clarity.
    vertical_saas = True

    corridors = [
        c for c in cells
        if _is_container_cell(c)
        and c.get("parent") == "1"
        and (c.get("id") or "").lower().startswith(prefix)
        and not _is_canvas_background_cell(c, page_w, page_h)
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
    ]
    # Also match unlabeled containers by role keywords when ids lack prefix.
    if not corridors:
        corridors = [
            c for c in cells
            if _is_container_cell(c)
            and c.get("parent") == "1"
            and (c.get("id") or "") not in {"overview_panel", "legend_box"}
            and not _is_canvas_background_cell(c, page_w, page_h)
            and not _is_cloud_container(c)
        ]

    if len(corridors) >= 2:
        corridors.sort(key=key_fn)
        x = 160.0
        gap = 40.0
        for cell in corridors:
            geo = _cell_geometry(cell)
            geom = cell.find("mxGeometry")
            if geo is None or geom is None:
                continue
            _, y, w, h = geo
            if abs(geo[0] - x) > 1.0:
                geom.set("x", str(x))
                changed = True
            if page_name == "logical" and _grid_logical_domain_apps(cell, cells):
                changed = True
                geo = _cell_geometry(cell) or geo
            if _sort_saas_children_in_shelf(cell, cells, vertical=vertical_saas):
                changed = True
                # Width may have shrunk for SaaS column — re-read before advancing x
                geo = _cell_geometry(cell) or geo
            x += geo[2] + gap
    else:
        for cell in corridors:
            if page_name == "logical" and _grid_logical_domain_apps(cell, cells):
                changed = True
            if _sort_saas_children_in_shelf(cell, cells, vertical=vertical_saas):
                changed = True

    # Root-level SaaS on focused views: vertical stack beside first corridor.
    root_saas = [c for c in cells if _is_saas_leaf_cell(c) and c.get("parent") == "1"]
    if root_saas and page_name != "logical":
        from app.adapters.outbound.diagram.architecture_docs import saas_journey_sort_key

        root_saas.sort(key=lambda c: saas_journey_sort_key(_shape(c), _plain_label(c)))
        base_x = 40.0
        base_y = 80.0
        if corridors:
            leftmost = min(
                (_cell_geometry(c)[0] for c in corridors if _cell_geometry(c)),
                default=200.0,
            )
            base_x = max(40.0, leftmost - _SAAS_ICON - 40.0)
        for i, cell in enumerate(root_saas):
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
    gap = 36.0
    icon = 64.0

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
            if group is actors and len(group) > 1:
                continue  # stacked — same x
            cx += icon + gap
        if group is actors and group:
            return start_x + icon + gap
        return cx if group else start_x

    x = _park(actors[:2], x)  # at most two actor icons on spine
    x = _park(id_saas[:2], x)
    x = _park(channels[:2], x)

    # Pull domain containers close to the spine (no huge empty gap).
    domains = [
        c for c in cells
        if _is_container_cell(c)
        and c.get("parent") == "1"
        and (c.get("id") or "").startswith("domain_")
    ]
    domains.sort(key=_domain_corridor_key)
    dx = max(x + 20.0, 280.0)
    for cell in domains:
        geom = cell.find("mxGeometry")
        geo = _cell_geometry(cell)
        if geom is None or geo is None:
            continue
        if abs(geo[0] - dx) > 1.0 or abs(geo[1] - 60.0) > 40.0:
            geom.set("x", str(dx))
            if geo[1] > 200 or geo[1] < 40:
                geom.set("y", "60")
            changed = True
        # Apps: max-4 horizontal wrap; SaaS shelf: vertical column.
        if (cell.get("id") or "").startswith("domain_saas"):
            _sort_saas_children_in_shelf(cell, cells, vertical=True)
        else:
            _grid_logical_domain_apps(cell, cells)
        geo = _cell_geometry(cell) or geo
        dx += geo[2] + 32.0

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
