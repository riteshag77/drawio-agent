"""Apply DiagramEditPlan ops onto existing draw.io XML."""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET

from app.domain.entities.diagram_edit import DiagramEditOp, DiagramEditPlan

_PLACEHOLDER_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#f5f5f5;strokeColor=#666666;"
    "fontColor=#333333;verticalLabelPosition=bottom;verticalAlign=top;"
    "align=center;fontSize=11;"
)
_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
    "html=1;endArrow=block;endFill=1;"
)


def _find_root(tree: ET.Element) -> ET.Element | None:
    root = tree.find(".//root")
    if root is not None:
        return root
    if tree.tag.endswith("root"):
        return tree
    return None


def _cells_by_id(root: ET.Element) -> dict[str, ET.Element]:
    out: dict[str, ET.Element] = {}
    for cell in root.findall("mxCell"):
        cid = cell.get("id")
        if cid:
            out[cid] = cell
    return out


def _new_id(prefix: str, existing: set[str]) -> str:
    for _ in range(20):
        cid = f"{prefix}_{uuid.uuid4().hex[:8]}"
        if cid not in existing:
            return cid
    return f"{prefix}_{uuid.uuid4().hex}"


def _ensure_geometry(cell: ET.Element, x: float, y: float, w: float = 78, h: float = 78) -> None:
    geom = cell.find("mxGeometry")
    if geom is None:
        geom = ET.SubElement(cell, "mxGeometry", {"as": "geometry"})
    geom.set("x", f"{x:.1f}")
    geom.set("y", f"{y:.1f}")
    geom.set("width", f"{w:.1f}")
    geom.set("height", f"{h:.1f}")
    if cell.get("edge") == "1":
        geom.set("relative", "1")


def apply_diagram_edits(xml: str, plan: DiagramEditPlan) -> str:
    """Apply surgical ops to ``xml``. Raises ValueError on unusable input."""
    if not plan or not plan.applicable or not plan.ops:
        raise ValueError("Edit plan is not applicable")
    tree = ET.fromstring(xml)
    root = _find_root(tree)
    if root is None:
        raise ValueError("No mxGraph root in diagram XML")
    cells = _cells_by_id(root)
    existing = set(cells.keys())

    for op in plan.ops:
        _apply_one(root, cells, existing, op)

    return ET.tostring(tree, encoding="unicode")


def _apply_one(
    root: ET.Element,
    cells: dict[str, ET.Element],
    existing: set[str],
    op: DiagramEditOp,
) -> None:
    kind = op.op
    if kind == "add_node":
        cid = op.id if op.id and op.id not in existing else _new_id("n", existing)
        parent = op.parent if op.parent and op.parent in cells else "1"
        cell = ET.SubElement(root, "mxCell", {
            "id": cid,
            "value": op.label or cid,
            "style": _PLACEHOLDER_STYLE,
            "vertex": "1",
            "parent": parent,
        })
        if op.shape_type:
            cell.set("shape_type", op.shape_type)
        _ensure_geometry(cell, op.x if op.x is not None else 40, op.y if op.y is not None else 40)
        cells[cid] = cell
        existing.add(cid)
        return

    if kind == "delete_node":
        if not op.id or op.id not in cells:
            return
        # Remove edges touching this node
        for eid, cell in list(cells.items()):
            if cell.get("edge") == "1" and (cell.get("source") == op.id or cell.get("target") == op.id):
                root.remove(cell)
                cells.pop(eid, None)
                existing.discard(eid)
        root.remove(cells[op.id])
        cells.pop(op.id, None)
        existing.discard(op.id)
        return

    if kind == "relabel":
        if op.id and op.id in cells and op.label is not None:
            cells[op.id].set("value", op.label)
        return

    if kind == "update_shape":
        if op.id and op.id in cells and op.shape_type:
            cells[op.id].set("shape_type", op.shape_type)
        return

    if kind == "move":
        if not op.id or op.id not in cells:
            return
        cell = cells[op.id]
        geom = cell.find("mxGeometry")
        if geom is None:
            _ensure_geometry(cell, op.x or 0, op.y or 0)
            return
        if op.x is not None:
            geom.set("x", f"{op.x:.1f}")
        if op.y is not None:
            geom.set("y", f"{op.y:.1f}")
        if op.parent and op.parent in cells:
            cell.set("parent", op.parent)
        return

    if kind == "connect":
        if not op.source or not op.target:
            return
        if op.source not in cells or op.target not in cells:
            return
        eid = op.edge_id if op.edge_id and op.edge_id not in existing else _new_id("e", existing)
        cell = ET.SubElement(root, "mxCell", {
            "id": eid,
            "value": op.edge_label or "",
            "style": _EDGE_STYLE,
            "edge": "1",
            "parent": "1",
            "source": op.source,
            "target": op.target,
        })
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        cells[eid] = cell
        existing.add(eid)
        return

    if kind == "disconnect":
        if op.edge_id and op.edge_id in cells:
            root.remove(cells[op.edge_id])
            cells.pop(op.edge_id, None)
            existing.discard(op.edge_id)
            return
        if op.source and op.target:
            for eid, cell in list(cells.items()):
                if cell.get("edge") == "1" and cell.get("source") == op.source and cell.get("target") == op.target:
                    root.remove(cell)
                    cells.pop(eid, None)
                    existing.discard(eid)


_SMALL_EDIT_RE = re.compile(
    r"\b(add|remove|delete|connect|disconnect|link|unlink|rename|relabel|move|relocate|"
    r"update|change|insert|drop|wire|attach)\b",
    re.IGNORECASE,
)
_LARGE_REDESIGN_RE = re.compile(
    r"\b(redesign|rebuild|regenerate|from scratch|start over|replace (the )?whole|"
    r"completely new|overhaul|re[- ]?architect)\b",
    re.IGNORECASE,
)


def looks_like_surgical_edit(prompt: str, *, has_current_xml: bool) -> bool:
    """Heuristic: small local change vs full redesign."""
    if not has_current_xml or not (prompt or "").strip():
        return False
    text = prompt.strip()
    if _LARGE_REDESIGN_RE.search(text):
        return False
    if len(text) > 600:
        return False
    return bool(_SMALL_EDIT_RE.search(text))
