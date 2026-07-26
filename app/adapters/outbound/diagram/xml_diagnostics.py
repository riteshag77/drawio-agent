"""Deterministic structural diagnostics and repair for draw.io XML.

Catches the class of defects the LLM's one-shot geometry can get wrong:
overlapping leaf icons, dangling edge endpoints, duplicate ids, empty
labels, edges into cross-cutting groups, coordinate-only nesting,
parent cycles, bad geometry, service-grid row overflow, and right-hand
overview-panel placement. Ported (stdlib-only, no new dependency) from the
drawio-skill's ``validate.py`` linter and adapted to work on an in-memory
XML string, reusing this app's own cell-parsing helpers from ``xml_tools``
so the notion of "container" and "geometry" stays consistent across the
pipeline.

Each deterministic check here mirrors an "ABSOLUTE RULE"/"FORBIDDEN"
clause in ``config/xml_gen/core.txt``. Format and structural repair is
auto-only — the pipeline never spends an LLM call to fix XML shape.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

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

# Canonical pitch ranges from core.txt's "Density tiers" table —
# data-path subnets prefer 200, allowed 180-220 (widened slightly here to
# avoid flagging legitimate edge-of-range layouts); supporting (cross-cutting)
# groups allow a tighter 160-180.
_DATA_PITCH_RANGE = (170.0, 230.0)
_GROUP_PITCH_RANGE = (150.0, 190.0)
# Canonical snap-to pitch when a fixer corrects a bad gap — _LEAF_GRID_PITCH_X
# (200.0) is used for ordinary data-path rows; cross-cutting groups need
# their own canonical value since 200 sits outside _GROUP_PITCH_RANGE (a bug
# found while auditing this: resolve_pitch_outliers used to snap every row
# to 200 unconditionally, which "fixed" an extreme outlier under a group
# container into a new moderate-range violation). 170 sits inside both the
# code's _GROUP_PITCH_RANGE and the prompt's documented 160-180.
_GROUP_CANONICAL_PITCH = 170.0

# Default geometry handed to a vertex missing <mxGeometry> entirely (see
# ensure_valid_vertex_geometry) — 78x78 matches the canonical leaf icon size
# documented in config/xml_gen/core.txt; the container default is safely
# above xml_tools._FIT_MIN_W/_FIT_MIN_H (120.0/80.0) so fit_containers_to_
# children never has to grow it from a degenerate starting size.
_DEFAULT_LEAF_SIZE = (78.0, 78.0)
_DEFAULT_CONTAINER_SIZE = (160.0, 120.0)

# Under-icon label band height (px) added to a leaf's detection rect when its
# style places the label below the icon — mirrors xml_tools._FIT_LABEL_BAND
# so overlap detection accounts for the label, not just the icon box.
_LABEL_BAND_HEIGHT = 28.0

# Cross-cutting group containers (config/xml_gen/views/network.txt "Cross-Cutting
# Groups — NO EDGES"): matched by the container's first label word, mirroring
# the short-form naming already used in this app's fixtures/tests ("Security",
# "Monitoring", ...) and the longer "Security Group" form from the container-
# style table — both start with the same keyword.
_CROSS_CUTTING_KEYWORDS = {"security", "monitoring", "integration", "management"}

# Stable id the prompt (config/xml_gen/core.txt, "Overview Panel — RIGHT-HAND
# SIDE") requires for the right-hand explanatory text box. It is deliberately
# excluded from the leaf-grid helpers below (pitch/row/overlap checks) since
# it's a prose annotation, not a service icon in the architecture grid.
_OVERVIEW_PANEL_ID = "overview_panel"
_OVERVIEW_PANEL_GAP = 40.0
_OVERVIEW_PANEL_MARGIN = 24.0

# Stable id the prompt requires for the edge-style legend box (same
# load-bearing-id pattern as the Overview Panel) — lets ensure_legend_position
# find and place it deterministically under the panel.
_LEGEND_ID = "legend_box"
_LEGEND_GAP = 20.0
# Rough characters-per-line at the panel's chosen font/width, matching the
# estimate config/xml_gen/core.txt already documents to the LLM — used to
# compute the panel's actual needed height from its real text content instead
# of trusting whatever height the LLM emitted.
_PANEL_CHARS_PER_LINE = 50
_PANEL_LINE_HEIGHT = 22.0
_PANEL_TITLE_BAND = 48.0
_PANEL_BLOCK_GAP = 10.0
_PANEL_BOTTOM_PAD = 14.0
_PANEL_MIN_HEIGHT = 140.0
_PANEL_MAX_HEIGHT = 420.0
_PANEL_MAX_STEPS = 6
_PANEL_MAX_STEP_CHARS = 120


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


def _is_legend_cell(cell: ET.Element) -> bool:
    return cell.get("id") == _LEGEND_ID


def _is_step_badge_cell(cell: ET.Element) -> bool:
    """True for a backend-inserted step-marker badge (see apply_step_markers).

    Excluded from the leaf-grid helpers below for the same reason as the
    Overview Panel: an annotation cell, never a service icon, must stay
    invisible to pitch/row/overlap checks by construction — not merely
    because today's pipeline happens to insert badges after those checks run.
    """
    cell_id = cell.get("id") or ""
    return cell_id.startswith(_STEP_BADGE_ID_PREFIX)


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
        if _is_legend_cell(cell) or _is_step_badge_cell(cell):
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

    Per network.txt: only the Cloud boundary (plus background/actors) may
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
        if (xml or "").strip():
            diag.errors.append("unparseable XML — document could not be parsed")
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
        if parent == cid:
            diag.errors.append(f"cell {cid!r} parents itself (cycle)")
        elif parent and parent in ids:
            seen: set[str] = {cid}
            walk = parent
            while walk and walk in ids and walk not in _RESERVED_IDS:
                if walk in seen:
                    diag.errors.append(f"cell {cid!r} has a parent cycle via {walk!r}")
                    break
                seen.add(walk)
                walk = ids[walk].get("parent") or ""
                if walk in _RESERVED_IDS:
                    break

        for end in ("source", "target"):
            ref = cell.get(end)
            if is_edge and not ref:
                diag.errors.append(f"edge {cid!r} missing {end}")
            elif ref and ref not in ids:
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

        for child in list(cell):
            if child.tag == "mxCell":
                diag.errors.append(
                    f"nested mxCell under {cid!r} — all mxCells must be siblings under <root>"
                )

    # Coordinate-only nesting: a top-level container visually inside another
    # top-level container but not actually parented to it.
    for inner, outer in _coordinate_nesting_violations(cells, page_w, page_h):
        diag.errors.append(
            f"container {inner.get('id')!r} at parent=\"1\" is visually contained within "
            f"container {outer.get('id')!r} — must be nested via parent={outer.get('id')!r} "
            "instead of parent=\"1\" (coordinate-only containment is forbidden)"
        )

    # Grouped by parent once, reused by both checks below — a leaf only ever
    # needs comparing against its true siblings, never every leaf in the whole
    # diagram regardless of container.
    by_parent_leaves = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)

    # Sibling leaf overlap — only leaves (containers legitimately wrap children;
    # the full-page background rect is intentionally under everything). Uses a
    # label-band-extended rect so overlapping under-icon labels are caught too.
    for kids in by_parent_leaves.values():
        boxes: list[tuple[str, tuple[float, float, float, float]]] = []
        for kid in kids:
            geo = _cell_geometry(kid)
            if geo:
                x, y, w, h = geo
                boxes.append((kid.get("id") or "", (x, y, w, h + _band_height(kid))))
        for i in range(len(boxes)):
            ia, ra = boxes[i]
            for j in range(i + 1, len(boxes)):
                ib, rb = boxes[j]
                if _rects_overlap(ra, rb):
                    diag.warnings.append(f"vertices {ia!r} and {ib!r} overlap")

    # Service-grid row count / pitch — max 4 icons per row, canonical pitch per layer.
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


def _fresh_id(base: str, existing_ids: set[str]) -> str:
    """Return a new id derived from ``base``, guaranteed not in ``existing_ids``.

    Reserves the returned id into ``existing_ids`` so a caller renaming
    several cells in one pass never hands out the same fresh id twice.
    Always returns something different from ``base`` — callers only use
    this when ``base`` itself must change (duplicate/reserved id), never
    when the original id would do.
    """
    suffix = 0
    while True:
        candidate = f"{base}_dup{suffix}"
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate
        suffix += 1


def resolve_reserved_id_reuse(xml: str) -> str:
    """Rename any vertex/edge that squats on the reserved id "0" or "1".

    Those ids are structurally reserved for the document's true root and
    default layer (mirrors ``diagnose_xml``'s own predicate for this check,
    ``cid in _RESERVED_IDS`` combined with ``vertex="1"``/``edge="1"``) — a
    cell claiming one of them anyway is renamed to a fresh id. The two
    canonical cells (neither a vertex nor an edge) are never touched. Must
    run before ``resolve_duplicate_ids`` so a rogue offender never gets to
    "win" a duplicate-id decision against the true canonical cell.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    existing_ids = {c.get("id") for c in cells}
    offenders = [
        c for c in cells
        if c.get("id") in _RESERVED_IDS and (c.get("vertex") == "1" or c.get("edge") == "1")
    ]
    if not offenders:
        return xml
    for cell in offenders:
        cell.set("id", _fresh_id(cell.get("id") or "cell", existing_ids))
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_duplicate_ids(xml: str) -> str:
    """Rename earlier occurrences of a duplicate id, keeping the last one.

    Every other id-keyed lookup in this file already treats the *last*
    occurrence in document order as canonical whenever a duplicate id
    exists: ``diagnose_xml``'s own ``ids[cid] = cell`` (unconditional
    overwrite every iteration), and the id-keyed dict comprehensions in
    ``resolve_pitch_outliers``, ``resolve_misplaced_actors``, and
    ``apply_step_markers``. Keeping the last and renaming earlier ones
    agrees with what those helpers already silently assume, rather than
    introducing a second, inconsistent convention. Never rewrites
    ``parent=``/``source=``/``target=`` references anywhere: if one was
    meant for an earlier (now renamed) occurrence, it silently binds to the
    kept cell instead — inherent to duplicate-id ambiguity, not fully
    resolvable without semantic judgment, but this never produces a
    dangling reference or drops content. Reserved ids are
    ``resolve_reserved_id_reuse``'s job, not this fixer's — must run after it.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    existing_ids = {c.get("id") for c in cells}
    seen_from_end: set[str] = set()
    changed = False
    for cell in reversed(cells):
        cid = cell.get("id") or ""
        if cid in _RESERVED_IDS:
            continue
        if cid in seen_from_end:
            cell.set("id", _fresh_id(cid, existing_ids))
            changed = True
        else:
            seen_from_end.add(cid)

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_orphaned_parents(xml: str) -> str:
    """Reparent any cell whose ``parent=`` id doesn't exist to ``parent="1"``.

    Mirrors ``resolve_misplaced_actors``'s "reposition, never delete or
    fabricate" philosophy — a cell referencing a parent that was never
    emitted (or was dropped by an earlier fixer) still renders, just at the
    root layer, instead of vanishing or leaving ``diagnose_xml``'s "parent
    does not exist" error unresolved forever. Existing x/y is left as-is (no
    longer meaningful relative to a real parent) — later fixers in the
    ``repair_xml`` chain (row/pitch/overlap) declutter it same as any other
    leaf. Must run after ``resolve_reserved_id_reuse``/``resolve_duplicate_ids``
    so parent resolution sees the final id set.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids = {c.get("id") for c in cells}
    orphans = [
        c for c in cells
        if c.get("parent") is not None
        and c.get("parent") not in ids
        and c.get("parent") not in _RESERVED_IDS
    ]
    if not orphans:
        return xml
    for cell in orphans:
        cell.set("parent", "1")
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def ensure_valid_vertex_geometry(xml: str) -> str:
    """Give every vertex a valid, non-degenerate ``mxGeometry``.

    Combines three related geometry defects into one pass (same precedent as
    ``ensure_overview_panel_position`` combining resize+reposition+page-grow
    into one function): missing geometry gets a default box inserted,
    non-positive width/height is clamped to the canonical leaf size, negative
    x/y is clamped to 0. Containers are exempt from the size/position clamp
    (same exemption ``diagnose_xml`` itself uses) since they're shrink-wrapped
    to their children later by ``fit_containers_to_children`` anyway — only a
    missing element is filled in for them here.

    Must run before every downstream fixer that consumes geometry (row
    overflow, pitch, overlap, fit-to-children all filter out cells whose
    ``_cell_geometry()`` is ``None``) — a vertex with missing geometry is
    invisible to all of them today and would otherwise stay an unfixable
    error forever.
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
        if _is_edge_label_cell(cell) or _is_canvas_background_cell(cell, page_w, page_h):
            continue

        geom = cell.find("mxGeometry")
        if geom is None:
            default_w, default_h = (
                _DEFAULT_CONTAINER_SIZE if _is_container_cell(cell) else _DEFAULT_LEAF_SIZE
            )
            geom = ET.SubElement(cell, "mxGeometry")
            geom.set("x", "0")
            geom.set("y", "0")
            geom.set("width", str(default_w))
            geom.set("height", str(default_h))
            geom.set("as", "geometry")
            changed = True
            continue

        if _is_container_cell(cell):
            continue

        try:
            x = float(geom.get("x") or 0)
            y = float(geom.get("y") or 0)
            w = float(geom.get("width") or 0)
            h = float(geom.get("height") or 0)
        except ValueError:
            x = y = w = h = float("nan")

        if x != x or y != y or w != w or h != h:  # NaN / non-finite from bad attrs
            default_w, default_h = (
                _DEFAULT_CONTAINER_SIZE if _is_container_cell(cell) else _DEFAULT_LEAF_SIZE
            )
            geom.set("x", "0")
            geom.set("y", "0")
            geom.set("width", str(default_w))
            geom.set("height", str(default_h))
            geom.set("as", "geometry")
            changed = True
            continue

        if w <= 0:
            geom.set("width", str(_DEFAULT_LEAF_SIZE[0]))
            changed = True
        if h <= 0:
            geom.set("height", str(_DEFAULT_LEAF_SIZE[1]))
            changed = True
        if x < 0:
            geom.set("x", "0")
            changed = True
        if y < 0:
            geom.set("y", "0")
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_parent_cycles(xml: str) -> str:
    """Break self-parent and ancestor cycles by reparenting cycle members to ``"1"``.

    A cycle makes depth walks and containment math unreliable; reparenting every
    cell that participates in a cycle to the root is the same safe fallback used
    for orphaned parents.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id") or "": c for c in cells}
    cycle_members: set[str] = set()
    for cell in cells:
        cid = cell.get("id") or ""
        parent = cell.get("parent")
        if not parent or parent in _RESERVED_IDS:
            continue
        if parent == cid:
            cycle_members.add(cid)
            continue
        seen: list[str] = [cid]
        walk = parent
        while walk and walk in by_id and walk not in _RESERVED_IDS:
            if walk in seen:
                # Entire path from the repeated node onward is in the cycle.
                idx = seen.index(walk)
                cycle_members.update(seen[idx:])
                cycle_members.add(walk)
                break
            seen.append(walk)
            walk = by_id[walk].get("parent") or ""
    if not cycle_members:
        return xml
    for cid in cycle_members:
        by_id[cid].set("parent", "1")
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def remove_dangling_edges(xml: str) -> str:
    """Drop edges with missing or unresolved source/target endpoints.

    Left alone, these render as broken floating arrows in the browser.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids = {c.get("id") for c in cells}
    to_remove = []
    for c in cells:
        if c.get("edge") != "1":
            continue
        src, tgt = c.get("source"), c.get("target")
        if not src or not tgt or src not in ids or tgt not in ids:
            to_remove.append(c)
    if not to_remove:
        return xml
    for cell in to_remove:
        root.remove(cell)
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def remove_cross_cutting_edges(xml: str) -> str:
    """Drop any edge touching a Security/Monitoring/Integration/Management group.

    Deterministic enforcement of network.txt's "ABSOLUTE RULE — NO LINES":
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

    Deterministic safety net for core.txt's "Service Grid — MAX 4 PER
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

    Deterministic enforcement of network.txt's "Overlap != containment"
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
    "aws_internet", "internet", "aws_iot", "iot", "aws_corporate_data_center",
    "corporate_data_center", "on_premises", "onprem", "on_prem",
}
_ACTOR_EXACT_LABELS = {
    "users", "user", "client", "mobile client", "internet", "end users",
    "external users", "end user", "customer", "customers",
    "on-prem", "on-premises", "on premises", "iot", "iot device", "corporate dc",
    "corporate data center", "data center",
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
    # Strip trivial HTML wrappers before exact match
    if "<" in value:
        value = re.sub(r"<[^>]+>", " ", value).strip()
    if value in _ACTOR_EXACT_LABELS:
        return True
    # Avoid Cognito User Pool / API Client false positives via exact set only
    return False


def absolute_position(cell: ET.Element, by_id: dict[str, ET.Element]) -> tuple[float, float]:
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
        abs_x, abs_y = absolute_position(cell, by_id)
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


# A gap this many times the canonical max pitch is a clear outlier (one child
# sitting far away from its row, not just a minor deviation) — generic to any
# leaf/service, not a specific named list of shape_types.
_PITCH_OUTLIER_MULTIPLIER = 3.0


def resolve_pitch_outliers(xml: str) -> str:
    """Snap any leaf sitting anomalously far from its row-mates back to a normal pitch.

    Generic geometric safety net, deliberately shape_type-agnostic — applies
    to *any* service, not a hardcoded list of "connectivity hub" types.
    Whatever the reason an LLM places one icon far outside the normal pitch
    range from its previous same-row sibling (e.g. trying to visually reach
    toward something in a different container), that single outlier forces
    every ancestor container to balloon in width to contain it — this
    detects a clear outlier (gap beyond ``_PITCH_OUTLIER_MULTIPLIER`` times
    the canonical max for that layer) and snaps it back to a normal pitch
    from its predecessor. Never touches gaps already within or moderately
    outside the normal range — that's ``diagnose_xml``'s warning +
    corrective-LLM-pass territory, not this fixer's.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids = {c.get("id"): c for c in cells if c.get("id")}
    parent_ids = {c.get("parent") for c in cells}
    page_w, page_h = _page_size(tree)
    by_parent_leaves = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)

    from app.adapters.outbound.diagram.placement_fixers import is_connectivity_hub_cell

    changed = False
    for pid, kids in by_parent_leaves.items():
        kids = [k for k in kids if not is_connectivity_hub_cell(k)]
        if len(kids) < 2:
            continue
        parent_cell = ids.get(pid)
        is_group = parent_cell is not None and _is_cross_cutting_group_cell(parent_cell)
        _, hi = _GROUP_PITCH_RANGE if is_group else _DATA_PITCH_RANGE
        outlier_threshold = hi * _PITCH_OUTLIER_MULTIPLIER
        # Groups snap to their own canonical pitch — snapping every row to the
        # data-path value (200) would land outside _GROUP_PITCH_RANGE
        # (150-190) and "fix" an extreme outlier into a new moderate-range
        # violation. See _GROUP_CANONICAL_PITCH's comment.
        canonical_pitch = _GROUP_CANONICAL_PITCH if is_group else _LEAF_GRID_PITCH_X
        geo_by_kid = {k: _cell_geometry(k) for k in kids}
        for row in _group_rows(kids, geo_by_kid):
            for a, b in zip(row, row[1:]):
                ga, gb = geo_by_kid.get(a), geo_by_kid.get(b)
                if ga is None or gb is None:
                    continue
                dx = gb[0] - ga[0]
                if dx <= outlier_threshold:
                    continue
                new_x = ga[0] + canonical_pitch
                geom_el = b.find("mxGeometry")
                if geom_el is not None:
                    geom_el.set("x", str(new_x))
                    geo_by_kid[b] = (new_x, gb[1], gb[2], gb[3])
                    changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_moderate_pitch_deviations(xml: str) -> str:
    """Snap any remaining out-of-canonical-range adjacent pitch, either direction.

    ``resolve_pitch_outliers`` only clears extreme outliers (beyond
    ``_PITCH_OUTLIER_MULTIPLIER`` times the canonical max) by design,
    deliberately leaving moderate deviations — too tight, or moderately too
    wide — for what used to be the LLM repair pass. The fix is the same
    mechanical "snap to canonical pitch from predecessor" regardless of how
    far off the gap is, so there's no reason to pay for an LLM call here
    either. Runs immediately after ``resolve_pitch_outliers`` in the
    ``repair_xml`` chain — by the time this runs, any gap still outside the
    canonical range is by definition moderate (extreme ones were already
    snapped), so the two fixers compose without double-handling anything.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    ids = {c.get("id"): c for c in cells if c.get("id")}
    parent_ids = {c.get("parent") for c in cells}
    page_w, page_h = _page_size(tree)
    by_parent_leaves = _leaf_siblings_by_parent(cells, parent_ids, page_w, page_h)

    from app.adapters.outbound.diagram.placement_fixers import is_connectivity_hub_cell

    changed = False
    for pid, kids in by_parent_leaves.items():
        kids = [k for k in kids if not is_connectivity_hub_cell(k)]
        if len(kids) < 2:
            continue
        parent_cell = ids.get(pid)
        is_group = parent_cell is not None and _is_cross_cutting_group_cell(parent_cell)
        lo, hi = _GROUP_PITCH_RANGE if is_group else _DATA_PITCH_RANGE
        canonical_pitch = _GROUP_CANONICAL_PITCH if is_group else _LEAF_GRID_PITCH_X
        geo_by_kid = {k: _cell_geometry(k) for k in kids}
        for row in _group_rows(kids, geo_by_kid):
            for a, b in zip(row, row[1:]):
                ga, gb = geo_by_kid.get(a), geo_by_kid.get(b)
                if ga is None or gb is None:
                    continue
                dx = gb[0] - ga[0]
                if dx <= 0 or lo <= dx <= hi:
                    continue
                new_x = ga[0] + canonical_pitch
                geom_el = b.find("mxGeometry")
                if geom_el is not None:
                    geom_el.set("x", str(new_x))
                    geo_by_kid[b] = (new_x, gb[1], gb[2], gb[3])
                    changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


_DIV_BLOCK = re.compile(r"<div[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
_DIV_FULL = re.compile(r"<div[^>]*>.*?</div>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP = re.compile(r"<[^>]+>")
_GAP_DIV_MARK = re.compile(
    r'data-section\s*=\s*["\']gaps["\']|color:\s*#B85450',
    re.IGNORECASE,
)


def _trim_overview_panel_value(value: str) -> str:
    """Keep title + up to ``_PANEL_MAX_STEPS`` short step divs; truncate long steps.

    Preserves a trailing Recommended-gaps section (red ``#B85450`` / data-section=gaps)
    without rewriting its colour. Existing step styling stays the amber overview recipe.

    Returns **decoded** HTML suitable for ``Element.set("value", …)``. ElementTree
    escapes on serialize — pre-escaped ``&lt;div&gt;`` here double-escapes to
    ``&amp;lt;div`` and the UI shows raw tags.
    """
    decoded = html.unescape(value or "")
    full_divs = _DIV_FULL.findall(decoded)
    if not full_divs:
        plain = _TAG_STRIP.sub("", decoded).strip()
        if len(plain) > 400:
            plain = plain[:397] + "..."
        return plain if plain else (value or "")

    main_divs: list[str] = []
    gap_divs: list[str] = []
    for div in full_divs:
        if _GAP_DIV_MARK.search(div):
            gap_divs.append(div)
        else:
            main_divs.append(div)

    if not main_divs:
        return "".join(gap_divs) if gap_divs else (value or "")

    # Rebuild main steps with standard overview styling (unchanged recipe).
    kept = main_divs[: 1 + _PANEL_MAX_STEPS]
    out_parts: list[str] = []
    for i, div in enumerate(kept):
        inner = _DIV_BLOCK.search(div)
        text = _TAG_STRIP.sub("", inner.group(1) if inner else div).strip()
        if i > 0 and len(text) > _PANEL_MAX_STEP_CHARS:
            text = text[: _PANEL_MAX_STEP_CHARS - 1].rstrip() + "…"
        safe = html.escape(text)
        if i == 0:
            out_parts.append(
                f'<div style="font-size:22px;font-weight:bold;">{safe}</div>'
            )
        else:
            out_parts.append(f'<div style="font-size:15px;">{safe}</div>')
    # Re-append gaps section verbatim (keeps red colour / markers).
    out_parts.extend(gap_divs)
    return "".join(out_parts)


def _estimate_panel_content_height(value: str) -> float:
    """Estimate the overview panel's actual needed height from its real text.

    Cap at ``_PANEL_MAX_HEIGHT`` so oversized essays cannot balloon the page.
    Gap-section lines are included and may raise the cap slightly.
    """
    decoded = html.unescape(value or "")
    full_divs = _DIV_FULL.findall(decoded) or [decoded]

    total = _PANEL_TITLE_BAND + _PANEL_BOTTOM_PAD
    step_count = 0
    gap_lines = 0
    seen_title = False
    for div in full_divs:
        is_gap = bool(
            isinstance(div, str) and div.startswith("<") and _GAP_DIV_MARK.search(div)
        )
        text = _TAG_STRIP.sub("", div).strip()
        if not text:
            continue
        if is_gap:
            gap_lines += 1
            total += _PANEL_LINE_HEIGHT + _PANEL_BLOCK_GAP
            continue
        if not seen_title:
            seen_title = True
            continue
        step_count += 1
        if step_count > _PANEL_MAX_STEPS:
            continue
        text = text[:_PANEL_MAX_STEP_CHARS]
        lines = max(1, -(-len(text) // _PANEL_CHARS_PER_LINE))
        total += lines * _PANEL_LINE_HEIGHT + _PANEL_BLOCK_GAP
    max_h = _PANEL_MAX_HEIGHT + (gap_lines * _PANEL_LINE_HEIGHT if gap_lines else 0)
    max_h = min(560.0, max_h)
    return max(_PANEL_MIN_HEIGHT, min(max_h, total))


def _grow_page(tree: ET.Element, cells: list[ET.Element], page_w: float, page_h: float,
                required_w: float | None = None, required_h: float | None = None) -> bool:
    """Grow pageWidth/pageHeight (and the background rect) to fit, if needed."""
    grow_w = required_w is not None and required_w > page_w
    grow_h = required_h is not None and required_h > page_h
    if not grow_w and not grow_h:
        return False
    model = tree.find(".//mxGraphModel")
    if model is None and tree.tag == "mxGraphModel":
        model = tree
    if model is not None:
        if grow_w:
            model.set("pageWidth", str(required_w))
        if grow_h:
            model.set("pageHeight", str(required_h))
    for c in cells:
        if _is_canvas_background_cell(c, page_w, page_h):
            geom_el = c.find("mxGeometry")
            if geom_el is not None:
                if grow_w:
                    geom_el.set("width", str(required_w))
                if grow_h:
                    geom_el.set("height", str(required_h))
    return True


def ensure_overview_panel_position(xml: str) -> str:
    """Keep the right-hand overview panel clear of the architecture, sized to its real text, and the page big enough for it.

    config/xml_gen/core.txt requires exactly one explanatory text box with
    ``id="overview_panel"``, placed to the right of every other top-level
    element. The LLM places and sizes it itself; this repositions it (never
    invents or edits its text) when its one-shot geometry overlaps the rest
    of the diagram or sits left of it, shrinks/grows its height to what its
    actual text content needs (never trusting the LLM's own height estimate —
    the same "don't trust the LLM's size" reasoning as the container
    shrink-fit in xml_tools.py), and grows ``pageWidth``/``pageHeight`` (and
    the full-page background rect) to fit — mirroring resolve_misplaced_actors'
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

    # Cap verbose overview text before sizing/positioning.
    raw_value = panel.get("value") or ""
    trimmed_value = _trim_overview_panel_value(raw_value)
    changed = False
    if trimmed_value != raw_value:
        panel.set("value", trimmed_value)
        changed = True

    panel_geo = _cell_geometry(panel)
    if panel_geo is None:
        return xml if not changed else _serialize_docroot(tree, root, is_mxfile, trimmed)

    page_w, page_h = _page_size(tree)
    others = [
        c for c in cells
        if c is not panel and c.get("edge") != "1" and c.get("parent") == "1"
        and not _is_canvas_background_cell(c, page_w, page_h) and not _is_legend_cell(c)
    ]
    other_geoms = [g for g in (_cell_geometry(c) for c in others) if g is not None]

    px, py, pw, ph = panel_geo

    # Shrink/grow height to the panel's actual text content — independent of
    # whether the panel needs to move, so a stale LLM-emitted height never
    # survives regardless of position.
    needed_h = _estimate_panel_content_height(panel.get("value") or "")
    if abs(needed_h - ph) > 0.5:
        ph = needed_h
        geom_el = panel.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("height", str(ph))
            changed = True

    if other_geoms:
        content_max_x = max(g[0] + g[2] for g in other_geoms)
        content_min_y = min(g[1] for g in other_geoms)
        needs_move = px < content_max_x or any(_rects_overlap((px, py, pw, ph), g) for g in other_geoms)
        if needs_move:
            px, py = content_max_x + _OVERVIEW_PANEL_GAP, content_min_y
            geom_el = panel.find("mxGeometry")
            if geom_el is not None:
                geom_el.set("x", str(px))
                geom_el.set("y", str(py))
                changed = True

    if _grow_page(tree, cells, page_w, page_h,
                  required_w=px + pw + _OVERVIEW_PANEL_MARGIN,
                  required_h=py + ph + _OVERVIEW_PANEL_MARGIN):
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def ensure_legend_position(xml: str) -> str:
    """Force the edge-style legend (if present) to sit directly below the Overview Panel.

    config/xml_gen/core.txt asks the LLM for "outside the flow" placement,
    which is too vague for a wide multi-account diagram — the model has been
    observed placing the legend at an arbitrary interior point that overlaps
    real architecture content. This always pins it to the same x as the
    panel, immediately below the panel's (already-finalized) bottom edge —
    call this *after* ``ensure_overview_panel_position`` so the panel's
    position/height are final. No-ops if either the legend or the panel is
    absent (nothing well-defined to anchor to).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    legend = next((c for c in cells if _is_legend_cell(c)), None)
    panel = next((c for c in cells if _is_overview_panel_cell(c)), None)
    if legend is None or panel is None:
        return xml
    legend_geo = _cell_geometry(legend)
    panel_geo = _cell_geometry(panel)
    if legend_geo is None or panel_geo is None:
        return xml

    px, py, pw, ph = panel_geo
    lx, ly, lw, lh = legend_geo
    target_x, target_y = px, py + ph + _LEGEND_GAP

    changed = False
    if abs(target_x - lx) > 0.5 or abs(target_y - ly) > 0.5:
        geom_el = legend.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(target_x))
            geom_el.set("y", str(target_y))
            changed = True
        lx, ly = target_x, target_y

    page_w, page_h = _page_size(tree)
    if _grow_page(tree, cells, page_w, page_h,
                  required_h=ly + lh + _OVERVIEW_PANEL_MARGIN):
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


_STEP_BADGE_ID_PREFIX = "step_badge_"
_STEP_BADGE_SIZE = 24.0
_STEP_BADGE_OFFSET = 11.0  # px up/left from the target cell's top-left corner
_STEP_BADGE_COLLISION_OFFSET = 14.0  # px diagonal nudge per extra marker on the same cell
_STEP_BADGE_STYLE = (
    "ellipse;whiteSpace=wrap;html=1;fillColor=#F5A623;strokeColor=#FFFFFF;strokeWidth=2;"
    "fontColor=#FFFFFF;fontSize=12;fontStyle=1;align=center;verticalAlign=middle;"
)


def apply_step_markers(xml: str, step_markers: list[Any], logger: Any = None) -> str:
    """Deterministically place numbered badge cells for Overview Panel steps.

    Each ``step_markers`` entry (e.g. the LLM's structured ``StepMarker``
    output) carries a ``step_number`` and a ``cell_id`` referencing an id the
    LLM already assigned to some leaf or container in the same response — no
    LLM geometry math involved. This looks up that cell's real canvas
    position via ``absolute_position`` (already sums ancestor container
    offsets) and appends a small numbered badge at ``parent="1"`` positioned
    at the cell's top-left corner, last in document order so it always
    renders on top regardless of nesting depth. Entries with a missing/blank
    ``cell_id``, or a ``cell_id`` not found in the document (typo, or a
    fixer removed it), are skipped and logged — never fabricates a position,
    never raises.
    """
    if not step_markers:
        return xml
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    existing_ids = set(by_id)

    collisions: dict[str, int] = {}
    added = False
    for marker in step_markers:
        step_number = getattr(marker, "step_number", None)
        cell_id = getattr(marker, "cell_id", None)
        if step_number is None or not cell_id:
            continue
        target = by_id.get(cell_id)
        if target is None:
            if logger:
                logger.log(
                    "step_markers",
                    f"step {step_number}: cell_id {cell_id!r} not found in final XML, skipping badge",
                    level="warning",
                )
            continue

        abs_x, abs_y = absolute_position(target, by_id)
        collision_index = collisions.get(cell_id, 0)
        collisions[cell_id] = collision_index + 1
        nudge = collision_index * _STEP_BADGE_COLLISION_OFFSET
        badge_x = abs_x - _STEP_BADGE_OFFSET + nudge
        badge_y = abs_y - _STEP_BADGE_OFFSET + nudge

        badge_id = f"{_STEP_BADGE_ID_PREFIX}{step_number}"
        suffix = 0
        while badge_id in existing_ids:
            suffix += 1
            badge_id = f"{_STEP_BADGE_ID_PREFIX}{step_number}_{suffix}"
        existing_ids.add(badge_id)

        badge = ET.SubElement(root, "mxCell")
        badge.set("id", badge_id)
        badge.set("value", str(step_number))
        badge.set("style", _STEP_BADGE_STYLE)
        badge.set("vertex", "1")
        badge.set("parent", "1")
        geom = ET.SubElement(badge, "mxGeometry")
        geom.set("x", str(badge_x))
        geom.set("y", str(badge_y))
        geom.set("width", str(_STEP_BADGE_SIZE))
        geom.set("height", str(_STEP_BADGE_SIZE))
        geom.set("as", "geometry")
        added = True

    if not added:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def repair_xml(xml: str) -> tuple[str, XmlDiagnostics]:
    """Run deterministic fixers in sequence, then re-diagnose what's left.

    Order matters. A leading "structural sanity" phase runs first — every
    later fixer assumes valid ids/parents/geometry, so these must be settled
    before anything else touches the document: resolve reserved-id (0/1)
    reuse before duplicate ids (so a rogue offender can never "win" a
    duplicate-id decision against the true canonical cell), then duplicate
    ids, then orphaned parents (needs the final id set from the previous
    two), then missing/degenerate vertex geometry (a vertex with no geometry
    is invisible to every geometry-consuming fixer below, so this must
    precede all of them).

    Then the original chain, unchanged: drop unfixable dangling/forbidden
    edges first (nothing downstream should route through cells about to
    disappear or that must never carry an edge), fill missing labels, fix
    structural placement (misplaced actors, coordinate-only nesting),
    resolve row overflow before pitch outliers/overlaps (so later passes see
    the corrected row layout), snap extreme pitch outliers back to a normal
    gap, then moderate pitch deviations (both directions, using the same
    canonical-pitch snap — extreme ones are already gone by this point so
    the two never double-handle the same gap), resolve remaining overlaps,
    then shrink-wrap containers — this also absorbs any container-width
    relief from no longer having to stretch around an outlier. The overview
    panel is positioned last of all, since it must clear the architecture's
    *final* extents (containers may still grow during the fit-to-children
    pass); the legend runs immediately after, since it depends on the
    panel's now-final position and height.
    """
    fixed = resolve_reserved_id_reuse(xml)
    fixed = resolve_duplicate_ids(fixed)
    fixed = resolve_orphaned_parents(fixed)
    fixed = resolve_parent_cycles(fixed)
    fixed = ensure_valid_vertex_geometry(fixed)
    fixed = remove_dangling_edges(fixed)
    fixed = remove_cross_cutting_edges(fixed)
    fixed = ensure_leaf_labels(fixed)
    fixed = resolve_misplaced_actors(fixed)
    from app.adapters.outbound.diagram.placement_fixers import (
        fix_edge_anchors_from_geometry,
        resolve_misplaced_connectivity_hubs,
        resolve_misplaced_edge_services,
        resolve_misplaced_load_balancers,
    )
    fixed = resolve_misplaced_connectivity_hubs(fixed)
    fixed = resolve_misplaced_edge_services(fixed)
    fixed = resolve_misplaced_load_balancers(fixed)
    fixed = resolve_coordinate_only_nesting(fixed)
    fixed = resolve_row_overflow(fixed)
    fixed = resolve_pitch_outliers(fixed)
    fixed = resolve_moderate_pitch_deviations(fixed)
    fixed = resolve_leaf_overlaps(fixed)
    fixed = fit_containers_to_children(fixed)
    fixed = fix_edge_anchors_from_geometry(fixed)
    fixed = ensure_overview_panel_position(fixed)
    fixed = ensure_legend_position(fixed)
    return fixed, diagnose_xml(fixed)
