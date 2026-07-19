"""Deterministic structural diagnostics and repair for draw.io XML.

Catches the class of defects the LLM's one-shot geometry can get wrong:
overlapping leaf icons, dangling edge endpoints, duplicate ids, and empty
labels. Ported (stdlib-only, no new dependency) from the drawio-skill's
``validate.py`` linter and adapted to work on an in-memory XML string,
reusing this app's own cell-parsing helpers from ``xml_tools`` so the
notion of "container" and "geometry" stays consistent across the pipeline.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from app.adapters.outbound.diagram.xml_tools import (
    _cell_geometry,
    _find_diagram_root,
    _is_canvas_background_cell,
    _is_container_cell,
    _page_size,
)

_RESERVED_IDS = {"0", "1"}

_LEAF_GRID_PITCH_X = 200.0
_LEAF_GRID_PITCH_Y = 130.0
_LEAF_GRID_MAX_COLS = 4
_LEAF_MIN_GAP = 20.0
_LEAF_GRID_MAX_ROWS = 50


@dataclass
class XmlDiagnostics:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.warnings


def _parse_docroot(xml: str) -> tuple[ET.Element, ET.Element, bool, str] | None:
    """Parse an mxfile/root fragment; returns (tree, root, is_mxfile, trimmed) or None."""
    if not (xml or "").strip() or "<mxCell" not in xml:
        return None
    trimmed = xml.strip()
    is_mxfile = "<mxfile" in trimmed
    parse_src = trimmed
    if not is_mxfile and "<root>" not in trimmed:
        parse_src = f"<root>{trimmed}</root>"
    try:
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return None
    root = _find_diagram_root(tree) if is_mxfile else tree
    if root is None:
        root = tree.find("root")
    if root is None:
        return None
    return tree, root, is_mxfile, trimmed


def _serialize_docroot(tree: ET.Element, root: ET.Element, is_mxfile: bool, trimmed: str) -> str:
    if is_mxfile:
        return ET.tostring(tree, encoding="unicode")
    if "<root>" in trimmed:
        return ET.tostring(root, encoding="unicode")
    return "".join(ET.tostring(c, encoding="unicode") for c in root.findall("mxCell"))


def _is_edge_label_cell(cell: ET.Element) -> bool:
    """True for a draw.io edge label / relative-positioned child vertex."""
    if "edgeLabel" in (cell.get("style") or ""):
        return True
    geom = cell.find("mxGeometry")
    return geom is not None and geom.get("relative") == "1"


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float],
                    pad: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + pad and bx < ax + aw + pad and ay < by + bh + pad and by < ay + ah + pad


def diagnose_xml(xml: str) -> XmlDiagnostics:
    """Return structural errors/warnings found in ``xml`` — never raises."""
    diag = XmlDiagnostics()
    parsed = _parse_docroot(xml)
    if parsed is None:
        return diag
    tree, root, _is_mxfile, _trimmed = parsed
    page_w, page_h = _page_size(tree)

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids: dict[str, ET.Element] = {}
    for cell in cells:
        cid = cell.get("id") or ""
        if cid in ids:
            diag.errors.append(f"duplicate id {cid!r}")
        ids[cid] = cell

    parent_ids = {c.get("parent") for c in cells}

    for cell in cells:
        cid = cell.get("id") or ""
        parent = cell.get("parent")
        is_vertex = cell.get("vertex") == "1"
        is_edge = cell.get("edge") == "1"

        if parent is not None and parent not in ids and parent not in _RESERVED_IDS:
            diag.errors.append(f"cell {cid!r} parent {parent!r} does not exist")
        for end in ("source", "target"):
            ref = cell.get(end)
            if ref and ref not in ids:
                diag.errors.append(f"edge {cid!r} {end} {ref!r} does not exist")
        if (is_vertex or is_edge) and cid in _RESERVED_IDS:
            diag.errors.append(f"cell {cid!r} reuses reserved id 0/1")

        if is_vertex and not _is_edge_label_cell(cell):
            geo = _cell_geometry(cell)
            if geo is None:
                diag.errors.append(f"vertex {cid!r} has missing geometry")
            elif not _is_container_cell(cell) and not _is_canvas_background_cell(cell, page_w, page_h):
                x, y, w, h = geo
                if w <= 0 or h <= 0:
                    diag.warnings.append(f"vertex {cid!r} non-positive size {w:g}x{h:g}")
                if x < 0 or y < 0:
                    diag.warnings.append(f"vertex {cid!r} negative position ({x:g},{y:g})")
                if not (cell.get("value") or "").strip():
                    diag.warnings.append(f"vertex {cid!r} has an empty label")

    # Sibling leaf overlap — only leaves (containers legitimately wrap children;
    # the full-page background rect is intentionally under everything).
    boxes: list[tuple[str, str | None, tuple[float, float, float, float]]] = []
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h):
            continue
        if cell.get("id") in parent_ids:
            continue
        geo = _cell_geometry(cell)
        if geo:
            boxes.append((cell.get("id") or "", cell.get("parent"), geo))
    for i in range(len(boxes)):
        ia, pa, ra = boxes[i]
        for j in range(i + 1, len(boxes)):
            ib, pb, rb = boxes[j]
            if pa == pb and _rects_overlap(ra, rb):
                diag.warnings.append(f"vertices {ia!r} and {ib!r} overlap")

    return diag


def remove_dangling_edges(xml: str) -> str:
    """Drop edges whose source/target id doesn't resolve to an existing cell.

    Left alone, these render as broken floating arrows in the browser.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids = {c.get("id") for c in cells}
    to_remove = [
        c for c in cells
        if c.get("edge") == "1"
        and ((c.get("source") and c.get("source") not in ids) or (c.get("target") and c.get("target") not in ids))
    ]
    if not to_remove:
        return xml
    for cell in to_remove:
        root.remove(cell)
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _label_from_shape_type(shape_type: str) -> str:
    name = shape_type
    for prefix in ("aws_", "azure_", "gcp_", "multi_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    label = name.replace("_", " ").strip().title()
    return label or "Service"


def ensure_leaf_labels(xml: str) -> str:
    """Fill a fallback label (derived from shape_type) on any leaf shipped with an empty value.

    Last-resort safety net — the prompt already requires every leaf to carry
    a specific label; this only catches what slips through.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_w, page_h = _page_size(tree)

    changed = False
    for cell in root.findall("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        if _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h):
            continue
        if (cell.get("value") or "").strip():
            continue
        shape_type = cell.get("shape_type") or ""
        cell.set("value", _label_from_shape_type(shape_type) if shape_type else "Service")
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _next_free_slot(
    base_x: float, base_y: float, w: float, h: float,
    placed: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    for row in range(_LEAF_GRID_MAX_ROWS):
        for col in range(_LEAF_GRID_MAX_COLS):
            candidate = (base_x + col * _LEAF_GRID_PITCH_X, base_y + row * _LEAF_GRID_PITCH_Y, w, h)
            if not any(_rects_overlap(candidate, p, pad=_LEAF_MIN_GAP) for p in placed):
                return candidate
    max_bottom = max((p[1] + p[3] for p in placed), default=base_y)
    return (base_x, max_bottom + _LEAF_MIN_GAP, w, h)


def resolve_leaf_overlaps(xml: str) -> str:
    """Nudge overlapping leaf siblings to the next free grid slot.

    Mirrors the prompt's own grid convention (200px pitch, wrap after 4,
    ~130px row height) so the fallback matches what the LLM is asked to do.
    Only repositions cells actually found overlapping — everything else,
    including already-clear siblings, is left untouched.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_w, page_h = _page_size(tree)

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    parent_ids = {c.get("parent") for c in cells}
    by_parent: dict[str, list[ET.Element]] = {}
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h):
            continue
        if cell.get("id") in parent_ids:
            continue
        pid = cell.get("parent")
        if not pid:
            continue
        by_parent.setdefault(pid, []).append(cell)

    changed = False
    for kids in by_parent.values():
        geo_by_kid = {kid: _cell_geometry(kid) for kid in kids}
        ordered = sorted(
            (k for k in kids if geo_by_kid[k] is not None),
            key=lambda k: (geo_by_kid[k][1], geo_by_kid[k][0]),
        )
        if len(ordered) < 2:
            continue
        base_x = min(geo_by_kid[k][0] for k in ordered)
        base_y = min(geo_by_kid[k][1] for k in ordered)

        placed: list[tuple[float, float, float, float]] = []
        for kid in ordered:
            rect = geo_by_kid[kid]
            if any(_rects_overlap(rect, p, pad=_LEAF_MIN_GAP) for p in placed):
                rect = _next_free_slot(base_x, base_y, rect[2], rect[3], placed)
                geom_el = kid.find("mxGeometry")
                if geom_el is not None:
                    geom_el.set("x", str(rect[0]))
                    geom_el.set("y", str(rect[1]))
                    changed = True
            placed.append(rect)

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


_ACTOR_SHAPE_TYPES = {
    "aws_users", "aws_user", "users", "user", "aws_client", "client", "aws_mobile_client",
    "azure_users", "azure_user", "azure_client",
    "gcp_users", "gcp_user", "gcp_client",
}
_ACTOR_EXACT_LABELS = {
    "users", "user", "client", "mobile client", "internet", "end users",
    "external users", "end user", "customer", "customers",
}


def _is_external_actor_cell(cell: ET.Element) -> bool:
    """True for a Users/Client/Internet-style external-actor leaf.

    Deliberately narrow (exact shape_type / exact trimmed label match, not
    substring) to avoid false positives on real services that merely contain
    a matching word, e.g. a Cognito "User Pool" or an "API Client" service.
    """
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    shape_type = (cell.get("shape_type") or "").strip().lower()
    if shape_type in _ACTOR_SHAPE_TYPES:
        return True
    value = (cell.get("value") or "").strip().lower()
    return value in _ACTOR_EXACT_LABELS


def _absolute_position(cell: ET.Element, by_id: dict[str, ET.Element]) -> tuple[float, float]:
    """Sum ancestor container offsets to get a cell's position in canvas (parent="1") space."""
    geo = _cell_geometry(cell)
    x, y = (geo[0], geo[1]) if geo else (0.0, 0.0)
    parent_id, seen = cell.get("parent"), set()
    while parent_id and parent_id not in _RESERVED_IDS and parent_id in by_id and parent_id not in seen:
        seen.add(parent_id)
        parent_cell = by_id[parent_id]
        pgeo = _cell_geometry(parent_cell)
        if pgeo:
            x += pgeo[0]
            y += pgeo[1]
        parent_id = parent_cell.get("parent")
    return x, y


def resolve_misplaced_actors(xml: str) -> str:
    """Reparent external-actor icons nested inside a container back to parent="1".

    The prompt requires Users/Client/Internet-style actors to always sit
    outside the cloud boundary (they represent people/systems, not a
    classifiable service) — this is the deterministic safety net for when
    the LLM nests one inside Cloud/Region/VPC/etc. anyway. Repositions the
    cell just left of the leftmost top-level container, mirroring the
    positioning formula already proven in the AWS skill's actor-placement
    fixer (cloud_x - width - 30, clamped to a 20px minimum margin).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    misplaced = [c for c in cells if _is_external_actor_cell(c) and c.get("parent") not in (None, "1")]
    if not misplaced:
        return xml

    page_w, page_h = _page_size(tree)
    top_level_containers = [
        c for c in cells
        if _is_container_cell(c) and c.get("parent") == "1" and not _is_canvas_background_cell(c, page_w, page_h)
    ]
    if not top_level_containers:
        return xml
    cloud_geo = min(
        (g for g in (_cell_geometry(c) for c in top_level_containers) if g is not None),
        key=lambda g: g[0],
        default=None,
    )
    if cloud_geo is None:
        return xml
    cloud_x = cloud_geo[0]

    changed = False
    for cell in misplaced:
        abs_x, abs_y = _absolute_position(cell, by_id)
        geo = _cell_geometry(cell)
        w = geo[2] if geo else 78.0
        new_x = max(20.0, cloud_x - w - 30.0)
        new_y = max(20.0, abs_y)
        cell.set("parent", "1")
        geom_el = cell.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(new_x))
            geom_el.set("y", str(new_y))
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def repair_xml(xml: str) -> tuple[str, XmlDiagnostics]:
    """Run deterministic fixers in sequence, then re-diagnose what's left.

    Order matters: drop unfixable dangling edges first (nothing downstream
    should route through cells that are about to disappear), fill missing
    labels, fix structural placement (misplaced actors), then resolve
    overlaps last so the grid math sees final content and final parents.
    """
    fixed = remove_dangling_edges(xml)
    fixed = ensure_leaf_labels(fixed)
    fixed = resolve_misplaced_actors(fixed)
    fixed = resolve_leaf_overlaps(fixed)
    return fixed, diagnose_xml(fixed)
