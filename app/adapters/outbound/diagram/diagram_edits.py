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


def _edge_exists(cells: dict[str, ET.Element], source: str, target: str) -> bool:
    return any(
        cell.get("edge") == "1" and cell.get("source") == source and cell.get("target") == target
        for cell in cells.values()
    )


def _new_edge_cell(root: ET.Element, eid: str, source: str, target: str, label: str, style: str) -> ET.Element:
    cell = ET.SubElement(root, "mxCell", {
        "id": eid,
        "value": label or "",
        "style": style or _EDGE_STYLE,
        "edge": "1",
        "parent": "1",
        "source": source,
        "target": target,
    })
    ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    return cell


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


#: Grid pitch for auto-placed add_node cells — matches the compiler's own
#: leaf pitch (geometry_spec.DATA_CANONICAL_PITCH / ROW_PITCH_Y) closely
#: enough to look intentional, without importing the compiler's constants
#: into an unrelated surgical-edit module.
_PLACE_PITCH_X = 200.0
_PLACE_PITCH_Y = 130.0
_PLACE_PAD = 10.0


def _sibling_rects(cells: dict[str, ET.Element], parent: str) -> list[tuple[float, float, float, float]]:
    """Geometry of every existing vertex sharing ``parent`` — the collision set
    a newly added node under that same parent must clear."""
    rects: list[tuple[float, float, float, float]] = []
    for cell in cells.values():
        if cell.get("vertex") != "1" or cell.get("parent") != parent:
            continue
        geom = cell.find("mxGeometry")
        if geom is None:
            continue
        try:
            rects.append((
                float(geom.get("x", 0)), float(geom.get("y", 0)),
                float(geom.get("width", 78)), float(geom.get("height", 78)),
            ))
        except (TypeError, ValueError):
            continue
    return rects


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (
        ax < bx + bw + _PLACE_PAD and bx < ax + aw + _PLACE_PAD
        and ay < by + bh + _PLACE_PAD and by < ay + ah + _PLACE_PAD
    )


def _place_new_node(
    rects: list[tuple[float, float, float, float]],
    desired_x: float | None, desired_y: float | None,
    w: float = 78.0, h: float = 78.0,
) -> tuple[float, float]:
    """Pick a spot for a new sibling that doesn't land on top of an existing one.

    Trusts an explicit, non-colliding (x, y) from the LLM as-is. Otherwise
    (omitted, or colliding — e.g. two ``add_node`` ops in the same batch both
    defaulting to the same spot) scans a grid to the right of the parent's
    existing content until it finds a free cell.
    """
    if desired_x is not None and desired_y is not None:
        if not any(_rects_overlap((desired_x, desired_y, w, h), r) for r in rects):
            return desired_x, desired_y
    base_x = desired_x if desired_x is not None else (max((r[0] + r[2] for r in rects), default=40.0) + 80.0)
    base_y = desired_y if desired_y is not None else min((r[1] for r in rects), default=40.0)
    for row in range(40):
        for col in range(8):
            x, y = base_x + col * _PLACE_PITCH_X, base_y + row * _PLACE_PITCH_Y
            if not any(_rects_overlap((x, y, w, h), r) for r in rects):
                return x, y
    return base_x, base_y


def apply_diagram_edits(xml: str, plan: DiagramEditPlan) -> str:
    """Apply surgical ops to ``xml``. Raises ValueError on unusable input.

    Clears ``layout="ir"`` only when an op changes geometry (add/delete/move/
    connect). Relabel / shape_type updates keep the compiler band so
    ``fit_containers_to_children`` does not undo equalized corridor heights.
    """
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

    _GEOMETRY_OPS = frozenset({"add_node", "delete_node", "move", "connect", "delete_edge"})
    if any((op.op or "") in _GEOMETRY_OPS for op in plan.ops):
        layer = cells.get("1")
        if layer is not None and layer.get("layout") == "ir":
            del layer.attrib["layout"]

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
        rects = _sibling_rects(cells, parent)
        x, y = _place_new_node(rects, op.x, op.y)
        _ensure_geometry(cell, x, y)
        cells[cid] = cell
        existing.add(cid)
        return

    if kind == "delete_node":
        if not op.id or op.id not in cells:
            return
        # Bridge predecessor -> successor around the deleted node before it's
        # gone, so removing a pass-through step (e.g. "delete the cache")
        # doesn't sever the chain it sat in. Single-hop only — edges into/out
        # of op.id itself, not through cascaded children — mirroring
        # CorridorIR._bridge_removed_leaves's cross-product approach.
        incoming = [
            c for c in cells.values() if c.get("edge") == "1" and c.get("target") == op.id
        ]
        outgoing = [
            c for c in cells.values() if c.get("edge") == "1" and c.get("source") == op.id
        ]
        bridges: list[tuple[str, str, str, str]] = []  # (source, target, label, style)
        seen_pairs: set[tuple[str, str]] = set()
        for in_edge in incoming:
            src = in_edge.get("source") or ""
            if not src or src not in cells:
                continue
            for out_edge in outgoing:
                tgt = out_edge.get("target") or ""
                if not tgt or tgt not in cells:
                    continue
                if src == tgt or (src, tgt) in seen_pairs or _edge_exists(cells, src, tgt):
                    continue
                seen_pairs.add((src, tgt))
                label = out_edge.get("value") or in_edge.get("value") or ""
                style = out_edge.get("style") or in_edge.get("style") or ""
                bridges.append((src, tgt, label, style))

        # Cascade: children of this node, then edges touching any removed id.
        to_remove = {op.id}
        changed = True
        while changed:
            changed = False
            for eid, cell in list(cells.items()):
                if eid in to_remove:
                    continue
                if cell.get("parent") in to_remove:
                    to_remove.add(eid)
                    changed = True
        for eid, cell in list(cells.items()):
            if cell.get("edge") == "1" and (
                cell.get("source") in to_remove or cell.get("target") in to_remove
            ):
                to_remove.add(eid)
        for eid in list(to_remove):
            cell = cells.get(eid)
            if cell is None:
                continue
            root.remove(cell)
            cells.pop(eid, None)
            existing.discard(eid)

        for src, tgt, label, style in bridges:
            eid = _new_id("e", existing)
            cell = _new_edge_cell(root, eid, src, tgt, label, style)
            cells[eid] = cell
            existing.add(eid)
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
        if op.source == op.target:
            return  # self-loop is never meaningful
        if _edge_exists(cells, op.source, op.target):
            return  # exact duplicate — no-op
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
                    break  # "disconnect A from B" means remove the connection, singular


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
