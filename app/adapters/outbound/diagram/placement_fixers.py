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
            rel_x = eg[0] + eg[2] + 40.0
            rel_y = eg[1]
        else:
            rel_x = max(40.0, abs_x - cloud_abs[0])
            rel_y = max(40.0, abs_y - cloud_abs[1])
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
        if _find_ancestor(leaf, by_id, _is_edge_layer_container):
            continue
        # Already cloud-level sibling of edge (not under vpc) — leave
        parent = by_id.get(leaf.get("parent") or "")
        if parent is not None and parent.get("id") == cloud.get("id"):
            continue
        if not _find_ancestor(leaf, by_id, _is_vpc_container) and not _find_ancestor(
            leaf, by_id, _is_subnet_container
        ):
            if parent is not None and _is_cross_cutting_group_cell(parent):
                pass  # hoist out of Security etc.
            else:
                continue

        edge = next(
            (c for c in cells if _is_edge_layer_container(c) and c.get("parent") == cloud.get("id")),
            None,
        )
        if edge is None:
            # Create a minimal Edge layer under cloud
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
                "rounded=0;whiteSpace=wrap;html=1;fillColor=#E8F0FE;strokeColor=#4285F4;"
                "verticalAlign=top;fontStyle=1;container=1;collapsible=0;",
            )
            edge.set("vertex", "1")
            edge.set("parent", cloud.get("id") or "1")
            geom = ET.SubElement(edge, "mxGeometry")
            geom.set("x", "40")
            geom.set("y", "40")
            geom.set("width", "320")
            geom.set("height", "160")
            geom.set("as", "geometry")
            root.append(edge)
            by_id[edge_id] = edge
            cells.append(edge)
            changed = True

        kids = [c for c in cells if c.get("parent") == edge.get("id") and c.get("vertex") == "1"]
        x = 20.0 + len(kids) * 100.0
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
        if zones:
            zg = _cell_geometry(zones[0]) or (40.0, 80.0, 400.0, 200.0)
            rel_x = zg[0]
            rel_y = max(40.0, zg[1] - 100.0)
        else:
            rel_x, rel_y = 40.0, 40.0
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


def fix_edge_anchors_from_geometry(xml: str) -> str:
    """Set exit/entry anchors from relative source→target placement."""
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    # Group edges by shared face for spread
    face_buckets: dict[tuple[str, str, str], list[ET.Element]] = {}
    changed = False

    for edge in edges:
        src_id, tgt_id = edge.get("source"), edge.get("target")
        if not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
            continue
        sx, sy = absolute_position(by_id[src_id], by_id)
        tx, ty = absolute_position(by_id[tgt_id], by_id)
        sgeo = _cell_geometry(by_id[src_id])
        tgeo = _cell_geometry(by_id[tgt_id])
        scx = sx + (sgeo[2] if sgeo else 78) / 2
        scy = sy + (sgeo[3] if sgeo else 78) / 2
        tcx = tx + (tgeo[2] if tgeo else 78) / 2
        tcy = ty + (tgeo[3] if tgeo else 78) / 2
        dx, dy = tcx - scx, tcy - scy

        if abs(dx) >= abs(dy):
            if dx >= 0:
                anchors = {"exitX": "1", "exitY": "0.5", "entryX": "0", "entryY": "0.5"}
                face = ("h", src_id, "right")
            else:
                anchors = {"exitX": "0", "exitY": "0.5", "entryX": "1", "entryY": "0.5"}
                face = ("h", src_id, "left")
        else:
            if dy >= 0:
                anchors = {"exitX": "0.5", "exitY": "1", "entryX": "0.5", "entryY": "0"}
                face = ("v", src_id, "bottom")
            else:
                anchors = {"exitX": "0.5", "exitY": "0", "entryX": "0.5", "entryY": "1"}
                face = ("v", src_id, "top")

        face_buckets.setdefault(face, []).append(edge)
        style = edge.get("style") or ""
        new_style = _set_style_kv(style, {
            **anchors,
            "exitDx": "0",
            "exitDy": "0",
            "entryDx": "0",
            "entryDy": "0",
        })
        if new_style != style:
            edge.set("style", new_style)
            changed = True

    # Spread multiple edges sharing a source face
    spreads = ("0.25", "0.5", "0.75")
    for face, group in face_buckets.items():
        if len(group) < 2:
            continue
        axis = face[0]
        for i, edge in enumerate(group):
            frac = spreads[i % 3]
            style = edge.get("style") or ""
            if axis == "h":
                updates = {"exitY": frac, "entryY": frac}
            else:
                updates = {"exitX": frac, "entryX": frac}
            edge.set("style", _set_style_kv(style, updates))
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)
