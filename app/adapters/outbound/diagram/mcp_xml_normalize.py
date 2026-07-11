"""Normalize MCP-routed draw.io XML for local canvas coordinates."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document


def force_waypoint_edges_to_root(xml: str) -> str:
    """Force edges that carry waypoints onto parent=\"1\" (absolute canvas space).

    MCP libavoid waypoints are absolute; nesting under AZ/subnet parents displaces them.
    """
    trimmed = (xml or "").strip()
    if not trimmed or ("<mxPoint" not in trimmed and 'as="points"' not in trimmed):
        return ensure_diagram_document(trimmed)

    parse_src = trimmed
    wrapped = False
    if "<mxfile" not in trimmed and "<mxGraphModel" in trimmed:
        parse_src = f"<wrap>{trimmed}</wrap>"
        wrapped = True
    elif "<mxfile" not in trimmed:
        return ensure_diagram_document(trimmed)

    try:
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return ensure_diagram_document(trimmed)

    changed = False
    for cell in tree.iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        geom = cell.find("mxGeometry")
        if geom is None:
            continue
        has_points = any(
            (child.tag == "Array" and child.get("as") == "points") or child.tag == "mxPoint"
            for child in geom.iter()
        )
        if not has_points:
            continue
        if cell.get("parent") != "1":
            cell.set("parent", "1")
            changed = True

    if not changed:
        return ensure_diagram_document(trimmed)

    if wrapped:
        model = tree.find("mxGraphModel")
        raw = ET.tostring(model if model is not None else tree, encoding="unicode")
    else:
        raw = ET.tostring(tree, encoding="unicode")
    return ensure_diagram_document(raw)
