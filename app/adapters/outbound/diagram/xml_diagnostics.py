"""Deterministic structural diagnostics and repair for draw.io XML.

Catches the class of defects the LLM's one-shot geometry can get wrong:
overlapping leaf icons, dangling edge endpoints, duplicate ids, empty
labels, edges into cross-cutting groups, coordinate-only nesting,
service-grid row overflow, and right-hand overview-panel placement. Ported
(stdlib-only, no new dependency) from the
drawio-skill's ``validate.py`` linter and adapted to work on an in-memory
XML string, reusing this app's own cell-parsing helpers from ``xml_tools``
so the notion of "container" and "geometry" stays consistent across the
pipeline.

Each new deterministic check here mirrors an "ABSOLUTE RULE"/"FORBIDDEN"
clause in ``config/xml_gen_core.txt`` — the goal is to catch and auto-fix
those violations without spending an LLM repair call on them.
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
    fit_containers_to_children,
)

_RESERVED_IDS = {"0", "1"}

_LEAF_GRID_PITCH_X = 200.0
_LEAF_GRID_PITCH_Y = 130.0
_LEAF_GRID_MAX_COLS = 4
_LEAF_MIN_GAP = 20.0
_LEAF_GRID_MAX_ROWS = 50

# Rows are clustered by top-y proximity within this tolerance (px).
_ROW_TOL = 20.0

# Canonical pitch ranges from xml_gen_core.txt's "Density tiers" table —
# data-path subnets prefer 200, allowed 180-220 (widened slightly here to
# avoid flagging legitimate edge-of-range layouts); supporting (cross-cutting)
# groups allow a tighter 160-180.
_DATA_PITCH_RANGE = (170.0, 230.0)
_GROUP_PITCH_RANGE = (150.0, 190.0)

# Under-icon label band height (px) added to a leaf's detection rect when its
# style places the label below the icon — mirrors xml_tools._FIT_LABEL_BAND
# so overlap detection accounts for the label, not just the icon box.
_LABEL_BAND_HEIGHT = 28.0

# Cross-cutting group containers (config/xml_gen_core.txt "Cross-Cutting
# Groups — NO EDGES"): matched by the container's first label word, mirroring
# the short-form naming already used in this app's fixtures/tests ("Security",
# "Monitoring", ...) and the longer "Security Group" form from the container-
# style table — both start with the same keyword.
_CROSS_CUTTING_KEYWORDS = {"security", "monitoring", "integration", "management"}

# Stable id the prompt (config/xml_gen_core.txt, "Overview Panel — RIGHT-HAND
# SIDE") requires for the right-hand explanatory text box. It is deliberately
# excluded from the leaf-grid helpers below (pitch/row/overlap checks) since
# it's a prose annotation, not a service icon in the architecture grid.
_OVERVIEW_PANEL_ID = "overview_panel"
_OVERVIEW_PANEL_GAP = 40.0
_OVERVIEW_PANEL_MARGIN = 24.0


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


def _rect_contained(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float],
                     tol: float = 2.0) -> bool:
    """True if ``inner`` fits fully inside ``outer`` (with a small tolerance)."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return ix >= ox - tol and iy >= oy - tol and ix + iw <= ox + ow + tol and iy + ih <= oy + oh + tol


def _band_height(cell: ET.Element) -> float:
    """Under-icon label band height for a leaf whose style places the label below it."""
    return _LABEL_BAND_HEIGHT if "verticalLabelPosition=bottom" in (cell.get("style") or "") else 0.0


def _is_cross_cutting_group_cell(cell: ET.Element) -> bool:
    """True for a Security/Monitoring/Integration/Management container.

    Matched by the container's first label word (case-insensitive) so both the
    short form ("Security") and the container-style-table form ("Security
    Group") match — deliberately not a substring match, to avoid mistaking an
    ordinary leaf service (e.g. a "Security Hub" icon) for the group container.
    """
    if not _is_container_cell(cell):
        return False
    value = (cell.get("value") or "").strip().lower()
    first_word = value.split()[0] if value.split() else ""
    return first_word in _CROSS_CUTTING_KEYWORDS


def _is_overview_panel_cell(cell: ET.Element) -> bool:
    return cell.get("id") == _OVERVIEW_PANEL_ID


def _cross_cutting_cell_ids(cells: list[ET.Element]) -> set[str]:
    """All ids belonging to a cross-cutting group: each group container plus every descendant."""
    children_by_parent: dict[str, list[str]] = {}
    for c in cells:
        pid, cid = c.get("parent"), c.get("id")
        if pid and cid:
            children_by_parent.setdefault(pid, []).append(cid)

    out: set[str] = set()
    for c in cells:
        if not _is_cross_cutting_group_cell(c):
            continue
        root_id = c.get("id")
        if not root_id:
            continue
        stack = [root_id]
        out.add(root_id)
        while stack:
            cur = stack.pop()
            for child_id in children_by_parent.get(cur, ()):
                if child_id not in out:
                    out.add(child_id)
                    stack.append(child_id)
    return out


def _group_rows(kids: list[ET.Element],
                 geo_by_kid: dict[ET.Element, tuple[float, float, float, float] | None]) -> list[list[ET.Element]]:
    """Cluster same-parent leaf siblings into visual rows by top-y proximity, left-to-right within a row."""
    ordered = sorted((k for k in kids if geo_by_kid.get(k) is not None), key=lambda k: geo_by_kid[k][1])
    rows: list[list[ET.Element]] = []
    for kid in ordered:
        y = geo_by_kid[kid][1]
        if rows and abs(geo_by_kid[rows[-1][0]][1] - y) <= _ROW_TOL:
            rows[-1].append(kid)
        else:
            rows.append([kid])
    for row in rows:
        row.sort(key=lambda k: geo_by_kid[k][0])
    return rows


def _leaf_siblings_by_parent(cells: list[ET.Element], parent_ids: set[str | None],
                              page_w: float, page_h: float) -> dict[str, list[ET.Element]]:
    """Group same-parent leaf (non-container, non-edge-label, non-background) siblings by parent id."""
    by_parent: dict[str, list[ET.Element]] = {}
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h) or _is_overview_panel_cell(cell):
            continue
        if cell.get("id") in parent_ids:
            continue
        pid = cell.get("parent")
        if pid:
            by_parent.setdefault(pid, []).append(cell)
    return by_parent


def _top_level_containers(cells: list[ET.Element], page_w: float, page_h: float) -> list[ET.Element]:
    return [
        c for c in cells
        if _is_container_cell(c) and c.get("parent") == "1" and not _is_canvas_background_cell(c, page_w, page_h)
    ]


def _coordinate_nesting_violations(
    cells: list[ET.Element], page_w: float, page_h: float,
) -> list[tuple[ET.Element, ET.Element]]:
    """Pairs of (inner, outer) top-level containers where ``inner`` is fully contained in ``outer``.

    Per xml_gen_core.txt: only the Cloud boundary (plus background/actors) may
    sit at ``parent="1"`` — any other container visually nested inside another
    parent="1" container but not reparented to it is a coordinate-only-nesting
    violation ("Overlap != containment").
    """
    top = _top_level_containers(cells, page_w, page_h)
    if len(top) < 2:
        return []
    geo_by = {c: _cell_geometry(c) for c in top}
    violations: list[tuple[ET.Element, ET.Element]] = []
    for inner in top:
        ig = geo_by.get(inner)
        if ig is None:
            continue
        best_outer, best_area = None, None
        for outer in top:
            if outer is inner:
                continue
            og = geo_by.get(outer)
            if og is None:
                continue
            outer_area = og[2] * og[3]
            if outer_area <= ig[2] * ig[3]:
                continue  # candidate outer must be strictly larger
            if _rect_contained(ig, og) and (best_area is None or outer_area < best_area):
                best_outer, best_area = outer, outer_area
        if best_outer is not None:
            violations.append((inner, best_outer))
    return violations


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
    cross_cutting_ids = _cross_cutting_cell_ids(cells)

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

        if is_edge and cross_cutting_ids:
            src, tgt = cell.get("source"), cell.get("target")
            if (src and src in cross_cutting_ids) or (tgt and tgt in cross_cutting_ids):
                diag.errors.append(
                    f"edge {cid!r} touches a cross-cutting group (Security/Monitoring/"
                    "Integration/Management) — these groups must have zero edges"
                )

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

    # Coordinate-only nesting: a top-level container visually inside another
    # top-level container but not actually parented to it.
    for inner, outer in _coordinate_nesting_violations(cells, page_w, page_h):
        diag.errors.append(
            f"container {inner.get('id')!r} at parent=\"1\" is visually contained within "
            f"container {outer.get('id')!r} — must be nested via parent={outer.get('id')!r} "
            "instead of parent=\"1\" (coordinate-only containment is forbidden)"
        )

    # Sibling leaf overlap — only leaves (containers legitimately wrap children;
    # the full-page background rect is intentionally under everything). Uses a
    # label-band-extended rect so overlapping under-icon labels are caught too.
    boxes: list[tuple[str, str | None, tuple[float, float, float, float]]] = []
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h) or _is_overview_panel_cell(cell):
            continue
        if cell.get("id") in parent_ids:
            continue
        geo = _cell_geometry(cell)
        if geo:
            x, y, w, h = geo
            boxes.append((cell.get("id") or "", cell.get("parent"), (x, y, w, h + _band_height(cell))))
    for i in range(len(boxes)):
        ia, pa, ra = boxes[i]
        for j in range(i + 1, len(boxes)):
            ib, pb, rb = boxes[j]
            if pa == pb and _rects_overlap(ra, rb):
                diag.warnings.append(f"vertices {ia!r} and {ib!r} overlap")

    # Service-grid row count / pitch — max 4 icons per row, canonical pitch per layer.
    by_parent_leaves = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)
    for pid, kids in by_parent_leaves.items():
        parent_cell = ids.get(pid)
        is_group = parent_cell is not None and _is_cross_cutting_group_cell(parent_cell)
        lo, hi = _GROUP_PITCH_RANGE if is_group else _DATA_PITCH_RANGE
        geo_by_kid = {k: _cell_geometry(k) for k in kids}
        for row in _group_rows(kids, geo_by_kid):
            if len(row) > _LEAF_GRID_MAX_COLS:
                ids_in_row = ", ".join(repr(k.get("id")) for k in row)
                diag.warnings.append(
                    f"more than {_LEAF_GRID_MAX_COLS} icons in one row under parent {pid!r}: {ids_in_row}"
                )
            for a, b in zip(row, row[1:]):
                dx = geo_by_kid[b][0] - geo_by_kid[a][0]
                if dx > 0 and not (lo <= dx <= hi):
                    diag.warnings.append(
                        f"icon pitch {dx:g}px between {a.get('id')!r} and {b.get('id')!r} under "
                        f"parent {pid!r} is outside the {lo:g}-{hi:g}px range"
                    )

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


def remove_cross_cutting_edges(xml: str) -> str:
    """Drop any edge touching a Security/Monitoring/Integration/Management group.

    Deterministic enforcement of xml_gen_core.txt's "ABSOLUTE RULE — NO LINES":
    these groups are shown for architecture completeness only and must never
    be on the request/data path.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    cross_cutting_ids = _cross_cutting_cell_ids(cells)
    if not cross_cutting_ids:
        return xml
    to_remove = [
        c for c in cells
        if c.get("edge") == "1"
        and ((c.get("source") in cross_cutting_ids) or (c.get("target") in cross_cutting_ids))
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
    Detection rects include the under-icon label band (for leaves styled with
    ``verticalLabelPosition=bottom``) so overlapping labels are also resolved.
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
    by_parent = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)

    changed = False
    for kids in by_parent.values():
        geo_by_kid = {kid: _cell_geometry(kid) for kid in kids}
        band_by_kid = {kid: _band_height(kid) for kid in kids}
        ordered = sorted(
            (k for k in kids if geo_by_kid[k] is not None),
            key=lambda k: (geo_by_kid[k][1], geo_by_kid[k][0]),
        )
        if len(ordered) < 2:
            continue
        base_x = min(geo_by_kid[k][0] for k in ordered)
        base_y = min(geo_by_kid[k][1] for k in ordered)

        placed: list[tuple[float, float, float, float]] = []  # label-band-extended detection rects
        for kid in ordered:
            x, y, w, h = geo_by_kid[kid]
            band = band_by_kid[kid]
            detect_rect = (x, y, w, h + band)
            if any(_rects_overlap(detect_rect, p, pad=_LEAF_MIN_GAP) for p in placed):
                new_x, new_y, _, _ = _next_free_slot(base_x, base_y, w, h + band, placed)
                geom_el = kid.find("mxGeometry")
                if geom_el is not None:
                    geom_el.set("x", str(new_x))
                    geom_el.set("y", str(new_y))
                    changed = True
                detect_rect = (new_x, new_y, w, h + band)
            placed.append(detect_rect)

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_row_overflow(xml: str) -> str:
    """Wrap the 5th+ icon of any over-full visual row onto the next row.

    Deterministic safety net for xml_gen_core.txt's "Service Grid — MAX 4 PER
    ROW" rule ("When adding into a full row of 4, place on the next row —
    never squeeze a 5th into the first row"). Only moves the overflow icons of
    a row that already has more than 4 — properly-sized rows are untouched.
    Any residual overlap this introduces is cleaned up by the overlap-nudge
    fixer that runs immediately after in ``repair_xml``.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_w, page_h = _page_size(tree)

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    parent_ids = {c.get("parent") for c in cells}
    by_parent = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)

    changed = False
    for kids in by_parent.values():
        geo_by_kid = {k: _cell_geometry(k) for k in kids}
        for row in _group_rows(kids, geo_by_kid):
            if len(row) <= _LEAF_GRID_MAX_COLS:
                continue
            overflow = row[_LEAF_GRID_MAX_COLS:]
            row_y = geo_by_kid[row[0]][1]
            base_x = geo_by_kid[row[0]][0]
            for idx, kid in enumerate(overflow, start=1):
                geom_el = kid.find("mxGeometry")
                if geom_el is None:
                    continue
                geom_el.set("x", str(base_x))
                geom_el.set("y", str(row_y + idx * _LEAF_GRID_PITCH_Y))
                changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_coordinate_only_nesting(xml: str) -> str:
    """Reparent a top-level container that's visually nested inside another top-level container.

    Deterministic enforcement of xml_gen_core.txt's "Overlap != containment"
    rule — only the Cloud boundary (plus background/actors) may legitimately
    sit at ``parent="1"``. Converts the reparented cell's geometry to be
    relative to its new parent.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_w, page_h = _page_size(tree)

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    violations = _coordinate_nesting_violations(cells, page_w, page_h)
    if not violations:
        return xml

    changed = False
    for inner, outer in violations:
        inner_geo = _cell_geometry(inner)
        outer_geo = _cell_geometry(outer)
        if inner_geo is None or outer_geo is None:
            continue
        inner.set("parent", outer.get("id") or "1")
        geom_el = inner.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(inner_geo[0] - outer_geo[0]))
            geom_el.set("y", str(inner_geo[1] - outer_geo[1]))
            changed = True

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


def ensure_overview_panel_position(xml: str) -> str:
    """Keep the right-hand overview panel clear of the architecture and the page wide enough for it.

    config/xml_gen_core.txt requires exactly one explanatory text box with
    ``id="overview_panel"``, placed to the right of every other top-level
    element. The LLM places it itself; this only repositions it (never
    invents or edits its text) when its one-shot geometry overlaps the rest
    of the diagram or sits left of it, and grows ``pageWidth`` (and the
    full-page background rect) to fit — mirroring resolve_misplaced_actors'
    "reposition, don't fabricate" shape. No-ops if the panel is absent.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    panel = next((c for c in cells if _is_overview_panel_cell(c)), None)
    if panel is None:
        return xml
    panel_geo = _cell_geometry(panel)
    if panel_geo is None:
        return xml

    page_w, page_h = _page_size(tree)
    others = [
        c for c in cells
        if c is not panel and c.get("edge") != "1"
        and c.get("parent") == "1" and not _is_canvas_background_cell(c, page_w, page_h)
    ]
    other_geoms = [g for g in (_cell_geometry(c) for c in others) if g is not None]
    if not other_geoms:
        return xml

    content_max_x = max(g[0] + g[2] for g in other_geoms)
    content_min_y = min(g[1] for g in other_geoms)

    px, py, pw, ph = panel_geo
    needs_move = px < content_max_x or any(_rects_overlap((px, py, pw, ph), g) for g in other_geoms)

    changed = False
    if needs_move:
        px, py = content_max_x + _OVERVIEW_PANEL_GAP, content_min_y
        geom_el = panel.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(px))
            geom_el.set("y", str(py))
            changed = True

    required_w = px + pw + _OVERVIEW_PANEL_MARGIN
    if required_w > page_w:
        model = tree.find(".//mxGraphModel")
        if model is None and tree.tag == "mxGraphModel":
            model = tree
        if model is not None:
            model.set("pageWidth", str(required_w))
            changed = True
        for c in cells:
            if _is_canvas_background_cell(c, page_w, page_h):
                geom_el = c.find("mxGeometry")
                if geom_el is not None:
                    geom_el.set("width", str(required_w))
                    changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def repair_xml(xml: str) -> tuple[str, XmlDiagnostics]:
    """Run deterministic fixers in sequence, then re-diagnose what's left.

    Order matters: drop unfixable dangling/forbidden edges first (nothing
    downstream should route through cells about to disappear or that must
    never carry an edge), fill missing labels, fix structural placement
    (misplaced actors, coordinate-only nesting), resolve row overflow before
    overlaps (so overlap resolution sees the corrected row layout), resolve
    remaining overlaps, then shrink-wrap containers. The overview panel is
    positioned last of all, since it must clear the architecture's *final*
    extents (containers may still grow during the fit-to-children pass).
    """
    fixed = remove_dangling_edges(xml)
    fixed = remove_cross_cutting_edges(fixed)
    fixed = ensure_leaf_labels(fixed)
    fixed = resolve_misplaced_actors(fixed)
    fixed = resolve_coordinate_only_nesting(fixed)
    fixed = resolve_row_overflow(fixed)
    fixed = resolve_leaf_overlaps(fixed)
    fixed = fit_containers_to_children(fixed)
    fixed = ensure_overview_panel_position(fixed)
    return fixed, diagnose_xml(fixed)
