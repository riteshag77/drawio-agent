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

Each deterministic check here mirrors a structural layout contract from
``geometry_spec`` / the IR compilers (formerly taught in deleted XML prompt
packs). Format and structural repair is auto-only — the pipeline never spends
an LLM call to fix XML shape.
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
    _format_style,
    _is_canvas_background_cell,
    _is_container_cell,
    _page_size,
    _parse_style,
)

_RESERVED_IDS = {"0", "1"}

# Geometry constants live in geometry_spec so the IR compiler (which produces
# geometry) and this module (which validates it) cannot drift apart. Aliased to
# the historical private names to keep the rest of this file untouched.
from app.adapters.outbound.diagram import geometry_spec as _geo


_LEAF_GRID_MAX_COLS = _geo.LEAF_GRID_MAX_COLS

# Rows are clustered by top-y proximity within this tolerance (px).
_ROW_TOL = _geo.ROW_TOL

# Canonical pitch ranges from geometry_spec's "Density tiers" table —
# data-path subnets prefer 200, allowed 180-220 (widened slightly here to
# avoid flagging legitimate edge-of-range layouts); supporting (cross-cutting)
# groups allow a tighter 160-180.
_DATA_PITCH_RANGE = _geo.DATA_PITCH_RANGE
_GROUP_PITCH_RANGE = _geo.GROUP_PITCH_RANGE
# Default geometry handed to a vertex missing <mxGeometry> entirely (see
# ensure_valid_vertex_geometry) — 78x78 matches the canonical leaf icon size
# documented in geometry_spec; the container default is safely above
# xml_tools._FIT_MIN_W/_FIT_MIN_H (120.0/80.0).
_DEFAULT_LEAF_SIZE = _geo.DEFAULT_LEAF_SIZE
_DEFAULT_CONTAINER_SIZE = _geo.DEFAULT_CONTAINER_SIZE

# Under-icon label band height (px) added to a leaf's detection rect when its
# style places the label below the icon — mirrors xml_tools._FIT_LABEL_BAND
# so overlap detection accounts for the label, not just the icon box.
_LABEL_BAND_HEIGHT = _geo.LABEL_BAND_H

# Cross-cutting group containers (config/xml_gen/ir/network.txt "Cross-Cutting
# Groups — NO EDGES"): matched by the container's first label word, mirroring
# the short-form naming already used in this app's fixtures/tests ("Security",
# "Monitoring", ...) and the longer "Security Group" form from the container-
# style table — both start with the same keyword.
_CROSS_CUTTING_KEYWORDS = {"security", "monitoring", "integration", "management"}

# Stable id the prompt (geometry_spec, "Overview Panel — RIGHT-HAND
# SIDE") requires for the right-hand explanatory text box. It is deliberately
# excluded from the leaf-grid helpers below (pitch/row/overlap checks) since
# it's a prose annotation, not a service icon in the architecture grid.
_OVERVIEW_PANEL_ID = "overview_panel"
_OVERVIEW_PANEL_GAP = 40.0
_OVERVIEW_PANEL_MARGIN = 24.0
# Documented panel width range (geometry_spec) — Gap B (Fix 3):
# ensure_overview_panel_position previously read width once and never
# corrected it, unlike height, which is always recomputed from content.
_OVERVIEW_PANEL_MIN_W = 400.0
_OVERVIEW_PANEL_MAX_W = 640.0

# Stable id the prompt requires for the edge-style legend box (same
# load-bearing-id pattern as the Overview Panel) — lets ensure_legend_position
# find and place it deterministically under the panel.
_LEGEND_ID = "legend_box"
_LEGEND_GAP = 20.0
# Rough characters-per-line at the panel's chosen font/width, matching the
# estimate geometry_spec already documents to the LLM — used to
# compute the panel's actual needed height from its real text content instead
# of trusting whatever height the LLM emitted.
_PANEL_CHARS_PER_LINE = 50
_PANEL_LINE_HEIGHT = 22.0
_PANEL_TITLE_BAND = 48.0
_PANEL_BLOCK_GAP = 10.0
_PANEL_BOTTOM_PAD = 14.0
_PANEL_MIN_HEIGHT = 140.0
# Raised from 520 (was already tight for Logical's own pre-existing worst
# case: 6 tech steps + 3 business bullets could already brush this ceiling)
# to fit the richer, LLM-authored business_notes bullets on both Logical and
# the new Network business section — see synthesize_ir_overview_body. Real
# content must never be silently clipped by a box too short to show it.
_PANEL_MAX_HEIGHT = 820.0
_PANEL_MAX_STEPS = 8
_PANEL_MAX_STEP_CHARS = 140
#: Shared by _trim_overview_panel_value (what actually survives trimming) and
#: _estimate_panel_content_height (how tall the box gets sized) — one
#: constant so the two can never silently drift apart. Raised from 3: Logical/
#: Network's own prompts still only ask for 2-3 narrative bullets each, but
#: Security's overview has up to 9 named fields (Purpose, Scope, Security
#: zones, Threat model, Data classification, Compliance frameworks, Control
#: types, Key assumptions) — see compiler/corridor.py's _overview_notes.
#: Raised again to 12 for ArchiMate, whose panel is 11 bullets and whose last
#: one is deliberately the confidence tally (see compiler/archimate.py's
#: _overview_notes) — at 10 the trim silently ate exactly the bullet that
#: tells a reader how much of the diagram was inferred rather than stated.
#: This is a cap, not a target: no other view emits near it.
_PANEL_MAX_BIZ = 12

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
    "employees", "employee", "staff", "admin", "admins", "administrator",
    "administrators", "internal users",
}

_DIV_BLOCK = re.compile(r"<div[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
_DIV_FULL = re.compile(r"<div[^>]*>.*?</div>", re.IGNORECASE | re.DOTALL)
_TAG_STRIP = re.compile(r"<[^>]+>")
_GAP_DIV_MARK = re.compile(
    r'data-section\s*=\s*["\'](?:gaps|facts)["\']|color:\s*#B85450',
    re.IGNORECASE,
)
# Structural overview sections that must survive trim verbatim (styles + text).
# Numbered canvas badges correlate to data-section="tech" / plain numbered steps.
_PRESERVE_SECTION_MARK = re.compile(
    r'data-section\s*=\s*["\'](?:lens|howto|purpose|biz-header|biz|tech-header|tech|facts|footer|gaps)["\']',
    re.IGNORECASE,
)
_TECH_STEP_MARK = re.compile(
    r'data-section\s*=\s*["\']tech["\']|^\s*\d+\.\s+',
    re.IGNORECASE,
)


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
    """All ids belonging to a cross-cutting group: each group container plus every descendant.

    Enterprise ``saas_*`` leaves are excluded even if nested under Integration —
    they are vendor products (edges allowed); a separate fixer reparents them out.
    """
    children_by_parent: dict[str, list[str]] = {}
    by_id = {c.get("id"): c for c in cells if c.get("id")}
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
                    child = by_id.get(child_id)
                    st = ((child.get("shape_type") if child is not None else None) or "").strip().lower()
                    if st.startswith("saas_"):
                        continue
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
    from app.adapters.outbound.diagram.edge_routing import _is_saas_leaf_cell

    by_parent: dict[str, list[ET.Element]] = {}
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h) or _is_overview_panel_cell(cell):
            continue
        if _is_legend_cell(cell) or _is_step_badge_cell(cell):
            continue
        if cell.get("spine") == "1" or cell.get("layout") == "1":
            continue
        if cell.get("id") in parent_ids:
            continue
        pid = cell.get("parent")
        # External actors + SaaS on the canvas root use a reserved lane — keep them
        # out of subnet-style pitch/overlap grids that scramble Network layout.
        if pid == "1" and (_is_external_actor_cell(cell) or _is_saas_leaf_cell(cell)):
            continue
        if pid:
            by_parent.setdefault(pid, []).append(cell)
    return by_parent


def _top_level_containers(cells: list[ET.Element], page_w: float, page_h: float) -> list[ET.Element]:
    return [
        c for c in cells
        if _is_container_cell(c) and c.get("parent") == "1"
        and not _is_canvas_background_cell(c, page_w, page_h)
        and (c.get("id") or "") not in {"overview_panel", "legend_box"}
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

    # Edge-box overlap (an edge's path crossing a node it doesn't connect to)
    # and edge-edge overlap (near-duplicate parallel segments) — reuses
    # _validate_edges (via the routed-polyline approximation, not the raw
    # anchor-to-anchor line — see _edge_routed_polyline's docstring), promoted
    # from validate_layout()'s observational-only output to real, countable
    # warnings here. Best-effort like the pitch/overlap checks above: routing
    # is a candidate search (_route_logical_edge), not a solver guaranteeing
    # zero collisions, so a tightly-packed layout can still leave a residual
    # — see test_edge_overlap_checks.py's documented Network sibling-occlusion
    # case. Filtered to just these two issue kinds (dropping the waypoint-
    # bend-count and anchor-agreement checks, a different, pre-existing
    # concern this pass isn't scoped to).
    for issue in _validate_edges(cells, ids, page_w, page_h):
        is_edge_box = "passes through non-endpoint node" in issue
        is_edge_edge = " overlap " in issue and "segment" in issue
        if is_edge_box or is_edge_edge:
            diag.warnings.append(issue)

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


def resolve_duplicate_ids(xml: str, warnings: list[str] | None = None) -> str:
    """Rename earlier occurrences of a duplicate id, keeping the last one.

    Every other id-keyed lookup in this file already treats the *last*
    occurrence in document order as canonical whenever a duplicate id
    exists: ``diagnose_xml``'s own ``ids[cid] = cell`` (unconditional
    overwrite every iteration), and the id-keyed dict comprehensions in
    ``apply_step_markers`` / overview helpers. Keeping the last and renaming
    earlier ones agrees with what those helpers already silently assume,
    rather than introducing a second, inconsistent convention. Never rewrites
    ``parent=``/``source=``/``target=`` references anywhere: if one was
    meant for an earlier (now renamed) occurrence, it silently binds to the
    kept cell instead — inherent to duplicate-id ambiguity, not fully
    resolvable without semantic judgment, but this never produces a
    dangling reference or drops content. Reserved ids are
    ``resolve_reserved_id_reuse``'s job, not this fixer's — must run after it.

    ``warnings``, if given, gets one entry per rebind that a surviving edge
    could plausibly be affected by — defense in depth only (see above: which
    occurrence an edge "meant" is inherently ambiguous, so this can't
    distinguish a correct reference from a silently-rebound one, it can only
    flag that duplicates existed and an edge touches the surviving id).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    existing_ids = {c.get("id") for c in cells}
    seen_from_end: set[str] = set()
    renamed_original_ids: set[str] = set()
    changed = False
    for cell in reversed(cells):
        cid = cell.get("id") or ""
        if cid in _RESERVED_IDS:
            continue
        if cid in seen_from_end:
            cell.set("id", _fresh_id(cid, existing_ids))
            changed = True
            renamed_original_ids.add(cid)
        else:
            seen_from_end.add(cid)

    if changed and warnings is not None:
        referencing = {
            cid for cid in renamed_original_ids
            if any(
                c.get("edge") == "1" and (c.get("source") == cid or c.get("target") == cid)
                for c in cells
            )
        }
        if referencing:
            warnings.append(
                f"duplicate id(s) {', '.join(sorted(referencing))} were resolved by keeping the "
                "last occurrence in document order — any edge referencing that id now silently "
                "binds to the kept cell, which may not be the element originally intended "
                "(verify edges near these ids point at the expected shapes)"
            )

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def resolve_orphaned_parents(xml: str) -> str:
    """Reparent any cell whose ``parent=`` id doesn't exist to ``parent="1"``.

    Mirrors the overview panel's "reposition, never delete or
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


def _edge_anchor_num(style: str, key: str, default: float) -> float:
    for part in (style or "").split(";"):
        if part.startswith(key + "="):
            try:
                return float(part.split("=", 1)[1])
            except (ValueError, IndexError):
                return default
    return default


def _edge_polyline(
    edge: ET.Element, by_id: dict[str, ET.Element],
) -> list[tuple[float, float]] | None:
    """Absolute polyline (source anchor → waypoints → target anchor) for an edge."""
    src_id, tgt_id = edge.get("source"), edge.get("target")
    if not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
        return None
    src, tgt = by_id[src_id], by_id[tgt_id]
    sgeo, tgeo = _cell_geometry(src), _cell_geometry(tgt)
    if sgeo is None or tgeo is None:
        return None
    sx, sy = absolute_position(src, by_id)
    tx, ty = absolute_position(tgt, by_id)
    style = edge.get("style") or ""
    start = (
        sx + sgeo[2] * _edge_anchor_num(style, "exitX", 0.5) + _edge_anchor_num(style, "exitDx", 0.0),
        sy + sgeo[3] * _edge_anchor_num(style, "exitY", 0.5) + _edge_anchor_num(style, "exitDy", 0.0),
    )
    end = (
        tx + tgeo[2] * _edge_anchor_num(style, "entryX", 0.5) + _edge_anchor_num(style, "entryDx", 0.0),
        ty + tgeo[3] * _edge_anchor_num(style, "entryY", 0.5) + _edge_anchor_num(style, "entryDy", 0.0),
    )
    waypoints: list[tuple[float, float]] = []
    geom = edge.find("mxGeometry")
    if geom is not None:
        for arr in geom.findall("Array"):
            if arr.get("as") != "points":
                continue
            for pt in arr.findall("mxPoint"):
                try:
                    waypoints.append((float(pt.get("x") or 0), float(pt.get("y") or 0)))
                except ValueError:
                    pass
    return [start, *waypoints, end]


def _expected_exit_entry_points(
    sgeo: tuple[float, float, float, float], sx: float, sy: float,
    tgeo: tuple[float, float, float, float], tx: float, ty: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Exit/entry face points ``orthogonalEdgeStyle`` would pick with no
    explicit anchor set — same dominant-axis convention this module's own
    anchor-agreement check and ``geometry_spec``'s "Edge anchors from placement"
    table already use, so this doesn't invent a second rule."""
    sw, sh = sgeo[2], sgeo[3]
    tw, th = tgeo[2], tgeo[3]
    dx = (tx + tw / 2) - (sx + sw / 2)
    dy = (ty + th / 2) - (sy + sh / 2)
    if abs(dx) >= abs(dy):
        exit_pt = (sx + sw if dx >= 0 else sx, sy + sh / 2)
        entry_pt = (tx if dx >= 0 else tx + tw, ty + th / 2)
    else:
        exit_pt = (sx + sw / 2, sy + sh if dy >= 0 else sy)
        entry_pt = (tx + tw / 2, ty if dy >= 0 else ty + th)
    return exit_pt, entry_pt


def _edge_routed_polyline(
    edge: ET.Element, by_id: dict[str, ET.Element],
) -> list[tuple[float, float]] | None:
    """Approximate the path ``orthogonalEdgeStyle`` actually renders — used
    only by the box-collision and parallel-overlap checks below, which need
    something closer to the real rendered route than a straight line.

    ``_edge_polyline`` (used by the waypoint-bend-count and anchor-agreement
    checks elsewhere in this module) connects exit/entry anchors with a
    straight line when the edge has no explicit waypoints — accurate for
    those two checks (they're about the *declared* anchors/waypoints, not the
    rendered path), but wrong here: compiler-emitted edges
    (``compiler/theme.py``'s ``flow_style()``) never set ``exitX``/``exitY``,
    so that straight line runs center-to-center and can cross any box sitting
    geometrically between two connected, correctly-placed nodes — a false
    "edge passes through node" on diagrams that are already correct (found via
    real compiler fixtures previously verified to have zero diagnostics).

    When the edge has explicit waypoints, they're trusted as-is (someone —
    the LLM or a repair pass — already routed this edge deliberately). When it
    doesn't, this synthesizes the same single-bend "Z" jog orthogonalEdgeStyle
    computes by default: exit/entry faces picked by dominant-axis (or the
    style's own explicit exitX/exitY when a repair pass already set them),
    joined by a perpendicular bend at their midpoint — collapses to a straight
    line automatically when source and target are already aligned.
    """
    src_id, tgt_id = edge.get("source"), edge.get("target")
    if not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
        return None
    src, tgt = by_id[src_id], by_id[tgt_id]
    sgeo, tgeo = _cell_geometry(src), _cell_geometry(tgt)
    if sgeo is None or tgeo is None:
        return None
    sx, sy = absolute_position(src, by_id)
    tx, ty = absolute_position(tgt, by_id)

    waypoints: list[tuple[float, float]] = []
    geom = edge.find("mxGeometry")
    if geom is not None:
        for arr in geom.findall("Array"):
            if arr.get("as") != "points":
                continue
            for pt in arr.findall("mxPoint"):
                try:
                    waypoints.append((float(pt.get("x") or 0), float(pt.get("y") or 0)))
                except ValueError:
                    pass

    style = edge.get("style") or ""
    has_explicit_exit = any(
        part.strip().startswith(("exitX=", "exitY=")) for part in style.split(";")
    )
    has_explicit_entry = any(
        part.strip().startswith(("entryX=", "entryY=")) for part in style.split(";")
    )
    if has_explicit_exit or has_explicit_entry or waypoints:
        # A repair pass or the LLM already chose real anchors/a real route —
        # trust it, same as _edge_polyline.
        start = (
            sx + sgeo[2] * _edge_anchor_num(style, "exitX", 0.5) + _edge_anchor_num(style, "exitDx", 0.0),
            sy + sgeo[3] * _edge_anchor_num(style, "exitY", 0.5) + _edge_anchor_num(style, "exitDy", 0.0),
        )
        end = (
            tx + tgeo[2] * _edge_anchor_num(style, "entryX", 0.5) + _edge_anchor_num(style, "entryDx", 0.0),
            ty + tgeo[3] * _edge_anchor_num(style, "entryY", 0.5) + _edge_anchor_num(style, "entryDy", 0.0),
        )
        return [start, *waypoints, end]

    start, end = _expected_exit_entry_points(sgeo, sx, sy, tgeo, tx, ty)
    dx = (tx + tgeo[2] / 2) - (sx + sgeo[2] / 2)
    dy = (ty + tgeo[3] / 2) - (sy + sgeo[3] / 2)
    if abs(dx) >= abs(dy):
        mid_x = (start[0] + end[0]) / 2
        bend = [(mid_x, start[1]), (mid_x, end[1])]
    else:
        mid_y = (start[1] + end[1]) / 2
        bend = [(start[0], mid_y), (end[0], mid_y)]
    return [start, *bend, end]


def _ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
) -> bool:
    """Proper line-segment crossing test — a shared endpoint is not a crossing."""
    if p1 in (p3, p4) or p2 in (p3, p4):
        return False
    return _ccw(p1, p3, p4) != _ccw(p2, p3, p4) and _ccw(p1, p2, p3) != _ccw(p1, p2, p4)


def count_edge_crossings(xml: str, page: str | None = None) -> int:
    """Count geometric line-crossings among a page's edges.

    A cheap proxy for "does this diagram look tangled" — nothing else in the
    pipeline measures edge crossings directly, so this is the quality signal
    the Logical/Security/Data-flow layout work (``logical_layout.py``,
    ``layout_corridor_ir`` / ELK) is scored against. Counts every pair of
    segments (from different edges) that properly cross; segments that only
    touch at a shared endpoint (e.g. two edges landing on the same icon) are
    not counted. Never raises — returns 0 on anything unparseable.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return 0
    tree, root, _is_mxfile, _trimmed = parsed
    if page is not None and tree.tag == "mxfile":
        for diag in tree.findall("diagram"):
            if (diag.get("name") or "").strip().lower() == page.strip().lower():
                page_root = diag.find("mxGraphModel/root")
                if page_root is not None:
                    root = page_root
                break

    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    edges = [c for c in cells if c.get("edge") == "1"]

    polylines: list[list[tuple[float, float]]] = []
    for edge in edges:
        line = _edge_polyline(edge, by_id)
        if line and len(line) >= 2:
            polylines.append(line)

    crossings = 0
    for i in range(len(polylines)):
        segs_i = list(zip(polylines[i], polylines[i][1:]))
        for j in range(i + 1, len(polylines)):
            segs_j = list(zip(polylines[j], polylines[j][1:]))
            for a1, a2 in segs_i:
                for b1, b2 in segs_j:
                    if _segments_cross(a1, a2, b1, b2):
                        crossings += 1
    return crossings





def _trim_overview_panel_value(value: str) -> str:
    """Keep overview structure readable without destroying badge-correlated steps.

    Preserves ``data-section`` blocks (lens, howto, business/technical headers,
    numbered tech steps, facts, footer, gaps) with their styling. Caps only
    anonymous step divs and ``data-section=tech`` lines to ``_PANEL_MAX_STEPS``.
    Business bullets (``data-section=biz``) are capped to 3 so the panel stays
    scannable. Older trim flattened every div and erased Logical's "Technical
    flow" section — badges still numbered the canvas while the panel only
    showed business bullets.
    """
    decoded = html.unescape(value or "")
    full_divs = _DIV_FULL.findall(decoded)
    if not full_divs:
        plain = _TAG_STRIP.sub("", decoded).strip()
        if len(plain) > 400:
            plain = plain[:397] + "..."
        return plain if plain else (value or "")

    title_div: str | None = None
    preserved: list[str] = []
    steps: list[str] = []
    gap_divs: list[str] = []
    tech_count = 0
    biz_count = 0
    seen_title = False

    for div in full_divs:
        low = div.lower()
        is_gaps = bool(re.search(r'data-section\s*=\s*["\']gaps["\']', low)) or (
            "#b85450" in low and "data-section" not in low
        )
        if is_gaps:
            gap_divs.append(div)
            continue

        if _PRESERVE_SECTION_MARK.search(div):
            is_tech = bool(re.search(r'data-section\s*=\s*["\']tech["\']', low))
            is_biz = bool(re.search(r'data-section\s*=\s*["\']biz["\']', low))
            if is_tech:
                tech_count += 1
                if tech_count > _PANEL_MAX_STEPS:
                    continue
                inner = _DIV_BLOCK.search(div)
                text = _TAG_STRIP.sub("", inner.group(1) if inner else div).strip()
                if len(text) > _PANEL_MAX_STEP_CHARS:
                    text = text[: _PANEL_MAX_STEP_CHARS - 1].rstrip() + "…"
                    open_m = re.match(r"(<div[^>]*>)", div, re.I)
                    open_tag = open_m.group(1) if open_m else '<div data-section="tech">'
                    preserved.append(f"{open_tag}{html.escape(text)}</div>")
                else:
                    preserved.append(div)
            elif is_biz:
                biz_count += 1
                if biz_count > _PANEL_MAX_BIZ:
                    continue
                preserved.append(div)
            else:
                preserved.append(div)
            continue

        inner = _DIV_BLOCK.search(div)
        text = _TAG_STRIP.sub("", inner.group(1) if inner else div).strip()
        if not seen_title:
            seen_title = True
            title_div = f'<div style="font-size:22px;font-weight:bold;">{html.escape(text)}</div>'
            continue
        if len(text) > _PANEL_MAX_STEP_CHARS:
            text = text[: _PANEL_MAX_STEP_CHARS - 1].rstrip() + "…"
        steps.append(f'<div style="font-size:15px;">{html.escape(text)}</div>')

    if title_div is None and not preserved and not steps:
        return "".join(gap_divs) if gap_divs else (value or "")

    out_parts: list[str] = []
    if title_div:
        out_parts.append(title_div)
    out_parts.extend(preserved)
    out_parts.extend(steps[:_PANEL_MAX_STEPS])
    out_parts.extend(gap_divs)
    return "".join(out_parts)


def _estimate_panel_content_width(value: str) -> float:
    """Pick a panel width from real content volume, between the documented
    min/max (see geometry_spec).

    Only height used to respond to content — width was clamped into the
    valid range if the LLM emitted something outside it, but never *grown*
    from whatever it started at. A wall of short one-line tech steps reads
    fine at the minimum width, but several full-sentence business_notes
    bullets wrap into far more lines than necessary in a narrow box, which is
    what actually drove a "text grew, box didn't expand" report — the box
    did grow taller, just not wider, so it read as an ever-taller narrow
    column instead of a comfortably wide, shorter block.
    """
    decoded = html.unescape(value or "")
    full_divs = _DIV_FULL.findall(decoded) or [decoded]
    total_chars = 0
    seen_title = False
    for div in full_divs:
        text = _TAG_STRIP.sub("", div).strip()
        if not text:
            continue
        if not seen_title:
            seen_title = True
            continue
        total_chars += len(text)
    # Interpolate: content-light panels stay compact; a few hundred
    # characters of real body text (roughly 3 business bullets and a handful
    # of tech steps) grows the panel toward the documented max.
    lo, hi = 300, 900
    if total_chars <= lo:
        return _OVERVIEW_PANEL_MIN_W
    if total_chars >= hi:
        return _OVERVIEW_PANEL_MAX_W
    frac = (total_chars - lo) / (hi - lo)
    return _OVERVIEW_PANEL_MIN_W + frac * (_OVERVIEW_PANEL_MAX_W - _OVERVIEW_PANEL_MIN_W)


def _estimate_panel_content_height(value: str, panel_width: float = _OVERVIEW_PANEL_MIN_W) -> float:
    """Estimate the overview panel's actual needed height from its real text.

    Mirrors ``_trim_overview_panel_value``'s own per-section caps — "biz"
    capped at ``_PANEL_MAX_BIZ``, "tech" at ``_PANEL_MAX_STEPS``, every other
    section (lens/howto/purpose/biz-header/tech-header/facts/footer) counted
    unconditionally — instead of one flat "8 items total, regardless of
    section" counter. That flat counter predates business_notes: once the
    panel started adding a biz-header + up to 3 biz divs, real content (a
    live capture showed all 4 tech steps AND the footer) silently fell past
    the shared budget and contributed zero height, even though
    ``_trim_overview_panel_value`` kept every one of them — the box was
    being sized as if the panel ended right after "Business journey". Cap at
    ``_PANEL_MAX_HEIGHT`` so oversized essays cannot balloon the page.
    Gap-section lines are included and may raise the cap slightly.

    ``panel_width`` scales the assumed characters-per-line — a panel grown
    wide by ``_estimate_panel_content_width`` wraps text into noticeably
    fewer lines than one left at the minimum width, so a fixed line-length
    assumption regardless of actual width either wastes space (box far
    taller than the wide panel needs) or clips it (box too short for a
    narrow one). ``_PANEL_CHARS_PER_LINE`` is calibrated for the minimum
    width; scale proportionally from there.
    """
    decoded = html.unescape(value or "")
    full_divs = _DIV_FULL.findall(decoded) or [decoded]
    chars_per_line = max(
        _PANEL_CHARS_PER_LINE,
        int(_PANEL_CHARS_PER_LINE * (panel_width / _OVERVIEW_PANEL_MIN_W)),
    )

    total = _PANEL_TITLE_BAND + _PANEL_BOTTOM_PAD
    tech_count = 0
    biz_count = 0
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
        is_tech = bool(isinstance(div, str) and re.search(r'data-section\s*=\s*["\']tech["\']', div, re.IGNORECASE))
        is_biz = bool(isinstance(div, str) and re.search(r'data-section\s*=\s*["\']biz["\']', div, re.IGNORECASE))
        if is_tech:
            tech_count += 1
            if tech_count > _PANEL_MAX_STEPS:
                continue
            text = text[:_PANEL_MAX_STEP_CHARS]
        elif is_biz:
            biz_count += 1
            if biz_count > _PANEL_MAX_BIZ:
                continue
            # No char cap here — _trim_overview_panel_value preserves biz
            # bullets in full regardless of length; capping the estimate
            # would under-count a long one and undersize the box for it.
        else:
            text = text[:_PANEL_MAX_STEP_CHARS]
        lines = max(1, -(-len(text) // chars_per_line))
        total += lines * _PANEL_LINE_HEIGHT + _PANEL_BLOCK_GAP
    max_h = _PANEL_MAX_HEIGHT + (gap_lines * _PANEL_LINE_HEIGHT if gap_lines else 0)
    # Absolute ceiling even with gap lines added — same +40 headroom over the
    # baseline cap this always had (520/560 before the _PANEL_MAX_HEIGHT raise).
    max_h = min(_PANEL_MAX_HEIGHT + 40.0, max_h)
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


def clear_overview_panel_children(xml: str) -> str:
    """Hoist any leaf wrongly nested under overview_panel / legend_box to parent=1.

    Prevents SaaS icons (e.g. SuccessFactors) from rendering on top of the
    right-hand briefing panel when the LLM parents them to the panel cell.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    forbidden_parents = {_OVERVIEW_PANEL_ID, _LEGEND_ID}
    changed = False
    for cell in cells:
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        pid = cell.get("parent") or ""
        if pid not in forbidden_parents:
            # Also hoist if any ancestor is the panel/legend.
            cur = by_id.get(pid)
            under = False
            seen: set[str] = set()
            while cur is not None:
                cid = cur.get("id") or ""
                if not cid or cid in seen:
                    break
                seen.add(cid)
                if cid in forbidden_parents:
                    under = True
                    break
                cur = by_id.get(cur.get("parent") or "")
            if not under:
                continue
        abs_x, abs_y = absolute_position(cell, by_id)
        cell.set("parent", "1")
        geom = cell.find("mxGeometry")
        if geom is not None:
            geom.set("x", str(abs_x))
            geom.set("y", str(abs_y))
        changed = True
    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


_OVERVIEW_TEXT_STYLE = (
    "rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
    "spacingLeft=16;spacingTop=14;fillColor=#FFF8E1;strokeColor=#F5A623;strokeWidth=2;fontSize=15;"
)


def sanitize_overview_panel_style(xml: str) -> str:
    """Force overview_panel / legend back to text-card chrome — never a SaaS image.

    Strips ``shape=image``, ``image=data:…``, and ``saas_*`` shape_type that the
    LLM (or MCP) may have applied because the panel HTML mentions a vendor.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    changed = False
    for cell in root.findall("mxCell"):
        cid = cell.get("id") or ""
        if cid not in {_OVERVIEW_PANEL_ID, _LEGEND_ID}:
            continue
        st = (cell.get("shape_type") or "").strip().lower()
        if st.startswith("saas_"):
            if "shape_type" in cell.attrib:
                del cell.attrib["shape_type"]
                changed = True
        style = cell.get("style") or ""
        dirty = (
            "shape=image" in style
            or "image=" in style
            or "data:image" in style
            or "container=1" in style
            or "swimlane" in style
            or "group" in style
            or "mxgraph.aws4.group" in style
        )
        if dirty or not style:
            cell.set("style", _OVERVIEW_TEXT_STYLE if cid == _OVERVIEW_PANEL_ID else (
                "rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
                "spacingLeft=12;spacingTop=8;fillColor=#F5F5F5;strokeColor=#666666;fontSize=12;"
            ))
            changed = True
    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def ensure_overview_panel_parent_root(xml: str) -> str:
    """Force the overview panel and legend back to ``parent="1"``, absolute geometry.

    Found via live-generation review (a Security page had ``overview_panel``
    emitted with ``parent="zone_dmz"``): the focused views tell the LLM every
    leaf's ``parent`` must be its owning zone/domain/phase, and the model can
    apply that rule too literally to these page-level UI cells too. Every
    downstream consumer (``ensure_overview_panel_position``, container
    shrink-fit) assumes the panel's geometry is already absolute page space —
    if its real parent is a zone container, that assumption silently breaks:
    the "absolute" x/y is actually relative to the zone, and
    ``fit_containers_to_children`` then balloons that zone to engulf the
    panel's width. Must run before any of those fixers.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    changed = False
    for cell in cells:
        if not (_is_overview_panel_cell(cell) or _is_legend_cell(cell)):
            continue
        if (cell.get("parent") or "1") == "1":
            continue
        abs_x, abs_y = absolute_position(cell, by_id)
        cell.set("parent", "1")
        geom = cell.find("mxGeometry")
        if geom is not None:
            geom.set("x", str(abs_x))
            geom.set("y", str(abs_y))
        changed = True
    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


#: A leaf still carrying this flat grey fill, with no mxgraph stencil/shape
#: reference at all, never got a real icon — see diagram_edits._PLACEHOLDER_STYLE
#: and compiler/theme.py's leaf_style base (both use this exact fill for a
#: leaf pending — or, if MCP never resolves it, permanently lacking — an icon).
_UNRESOLVED_LEAF_FILLS = ("#f5f5f5", "#F5F5F5")


def center_unresolved_leaf_labels(xml: str) -> str:
    """A leaf with no resolved icon reads far more like intentional content,
    not a broken/tiny icon, with its name centered inside the box instead of
    left in the "caption below an icon" position that convention uses for
    every leaf that DOES have one.

    Runs after MCP resolution finishes (repair_xml's own pass runs before
    it — see xml_pipeline.py) so it only ever touches what's STILL
    unresolved at that point, never something MCP simply hadn't gotten to
    yet. No-ops on anything with a real stencil/shape reference.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    changed = False
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        style = cell.get("style") or ""
        if not style or "mxgraph." in style or "resIcon=" in style or "prIcon=" in style:
            continue  # has a real stencil — not an unresolved placeholder
        if not any(f"fillColor={c}" in style for c in _UNRESOLVED_LEAF_FILLS):
            continue
        if (
            _is_container_cell(cell) or _is_overview_panel_cell(cell)
            or _is_legend_cell(cell) or _is_step_badge_cell(cell)
        ):
            continue
        style_map = _parse_style(style)
        if style_map.get("verticalAlign") == "middle" and "verticalLabelPosition" not in style_map:
            continue  # already centered — idempotent
        style_map.pop("verticalLabelPosition", None)
        style_map.pop("labelPosition", None)
        style_map["verticalAlign"] = "middle"
        style_map["align"] = "center"
        style_map.setdefault("fontColor", "#333333")  # dark, readable on the light grey fill
        cell.set("style", _format_style(style_map))
        changed = True
    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def ensure_overview_panel_position(xml: str) -> str:
    """Keep the right-hand overview panel clear of the architecture, sized to its real text, and the page big enough for it.

    geometry_spec requires exactly one explanatory text box with
    ``id="overview_panel"``, placed to the right of every other top-level
    element. The LLM places and sizes it itself; this repositions it (never
    invents or edits its text) when its one-shot geometry overlaps the rest
    of the diagram or sits left of it, shrinks/grows its height to what its
    actual text content needs (never trusting the LLM's own height estimate —
    the same "don't trust the LLM's size" reasoning as the container
    shrink-fit in xml_tools.py), and grows ``pageWidth``/``pageHeight`` (and
    the full-page background rect) to fit — mirroring actor-band placement's
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
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    # Structure hull: top-level containers + actors only — nested leaves must
    # not push the panel hundreds of px to the right of the architecture.
    other_geoms: list[tuple[float, float, float, float]] = []
    for c in cells:
        if c is panel or c.get("edge") == "1":
            continue
        if _is_canvas_background_cell(c, page_w, page_h) or _is_legend_cell(c):
            continue
        if _is_step_badge_cell(c):
            continue
        if c.get("vertex") != "1":
            continue
        if (c.get("parent") or "") != "1":
            continue
        geo = _cell_geometry(c)
        if geo is None:
            continue
        ax, ay = absolute_position(c, by_id)
        other_geoms.append((ax, ay, geo[2], geo[3]))

    px, py, pw, ph = panel_geo

    # Width first, height second — height's line-wrap assumption scales off
    # the *final* width, so a wide panel (more chars per line) is credited
    # with the shorter box it actually needs instead of being sized as if it
    # were still at the minimum width. Gap B (Fix 3): width used to only be
    # clamped into the documented ~400-640px range if the LLM emitted
    # something outside it, never *grown* from whatever it started at — see
    # _estimate_panel_content_width's own docstring for why that under-
    # served the richer business_notes content specifically.
    target_w = _estimate_panel_content_width(panel.get("value") or "")
    clamped_w = min(max(target_w, _OVERVIEW_PANEL_MIN_W), _OVERVIEW_PANEL_MAX_W)
    if abs(clamped_w - pw) > 0.5:
        pw = clamped_w
        geom_el = panel.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("width", str(pw))
            changed = True

    # Shrink/grow height to the panel's actual text content — independent of
    # whether the panel needs to move, so a stale LLM-emitted height never
    # survives regardless of position.
    needed_h = _estimate_panel_content_height(panel.get("value") or "", panel_width=pw)
    if abs(needed_h - ph) > 0.5:
        ph = needed_h
        geom_el = panel.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("height", str(ph))
            changed = True

    if other_geoms:
        content_max_x = max(g[0] + g[2] for g in other_geoms)
        content_min_y = min(g[1] for g in other_geoms)
        # Move when the panel overlaps/sits left of the content, AND when it
        # sits implausibly far to the right of it — the LLM's one-shot x can
        # land hundreds of px past the real content, which previously never
        # got pulled back in since this only guarded against "too close".
        gap = px - content_max_x
        needs_move = (
            px < content_max_x
            or any(_rects_overlap((px, py, pw, ph), g) for g in other_geoms)
            or gap > _OVERVIEW_PANEL_GAP * 4
        )
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
    """Pin the edge-style legend below the overview panel, or under the hull.

    When the panel is taller than the architecture hull, place the legend under
    the hull instead so narrative and legend do not form one tall right column.
    Call after ``ensure_overview_panel_position``. No-ops if either cell is absent.
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

    # Prefer legend under the architecture hull when the panel is taller than
    # the hull — avoids one tall narrative column swallowing vertical reading.
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    page_w, page_h = _page_size(tree)
    hull_bottom = 0.0
    hull_left = px
    for c in cells:
        if c is panel or c is legend or c.get("edge") == "1":
            continue
        if _is_canvas_background_cell(c, page_w, page_h) or _is_step_badge_cell(c):
            continue
        if c.get("vertex") != "1" or (c.get("parent") or "") != "1":
            continue
        geo = _cell_geometry(c)
        if geo is None:
            continue
        ax, ay = absolute_position(c, by_id)
        hull_bottom = max(hull_bottom, ay + geo[3])
        hull_left = min(hull_left, ax)

    if hull_bottom > 0 and ph > (hull_bottom - py) + 40:
        target_x = hull_left
        target_y = hull_bottom + _LEGEND_GAP
    else:
        target_x, target_y = px, py + ph + _LEGEND_GAP

    changed = False
    if abs(target_x - lx) > 0.5 or abs(target_y - ly) > 0.5:
        geom_el = legend.find("mxGeometry")
        if geom_el is not None:
            geom_el.set("x", str(target_x))
            geom_el.set("y", str(target_y))
            changed = True
        lx, ly = target_x, target_y

    if _grow_page(tree, cells, page_w, page_h,
                  required_h=ly + lh + _OVERVIEW_PANEL_MARGIN):
        changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def move_overview_and_legend_to_end(xml: str) -> str:
    """Guarantee the overview panel and legend are always the last children under
    ``<root>``, so they always render on top (draw.io/mxGraph z-orders by document
    order). Whatever their position in the LLM's output, or wherever a later fixer
    reparents/creates a container (which can otherwise land after them in document
    order and visually obscure them if it overlaps), this pushes both to the end.
    Must run last, after every other repair step has finished moving things.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = list(root.findall("mxCell"))
    tail = [c for c in cells if _is_overview_panel_cell(c) or _is_legend_cell(c)]
    if not tail:
        return xml
    # Already last (in this relative order)? No-op — avoids a pointless rewrite.
    if [c.get("id") for c in cells[-len(tail):]] == [c.get("id") for c in tail]:
        return xml
    for cell in tail:
        root.remove(cell)
        root.append(cell)
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

    Network: never park badges on enterprise SaaS leaves (icons look "badged");
    remount to Edge layer, Cloud, or Users when the LLM pointed at SaaS.
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

    page_name = ""
    if tree.tag == "mxfile":
        diag = tree.find("diagram")
        if diag is not None:
            page_name = (diag.get("name") or "").strip().lower()

    def _remap_saas_badge_target(target: ET.Element) -> ET.Element | None:
        """Remount badges off SaaS icons; on Logical prefer portal/domain anchors."""
        from app.adapters.outbound.diagram.edge_routing import _is_saas_leaf_cell

        def _is_logical_channel(cell: ET.Element) -> bool:
            if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
                return False
            st = (cell.get("shape_type") or "").strip().lower()
            return st == "logical_channel"

        def _is_edge_layer(cell: ET.Element) -> bool:
            if not _is_container_cell(cell):
                return False
            value = re.sub(r"<[^>]+>", " ", (cell.get("value") or "").strip().lower()).strip()
            return value.startswith("edge") or "global layer" in value or value == "global"

        def _is_cloud(cell: ET.Element) -> bool:
            if not _is_container_cell(cell):
                return False
            value = re.sub(r"<[^>]+>", " ", (cell.get("value") or "").strip().lower()).strip()
            return any(k in value for k in ("cloud", "aws", "azure", "gcp", "google cloud"))

        if page_name == "logical":
            # Prefer spine anchors (persona → portal → domain) — not SaaS / random leaves.
            if _is_saas_leaf_cell(target):
                for c in cells:
                    if _is_logical_channel(c):
                        return c
                for c in cells:
                    cid = (c.get("id") or "")
                    if cid.startswith("domain_") and "saas" not in cid:
                        return c
                return None
            return target

        if page_name != "network":
            return target
        if not _is_saas_leaf_cell(target):
            return target
        for c in cells:
            if _is_edge_layer(c):
                return c
        for c in cells:
            if _is_cloud(c) and c.get("parent") == "1":
                return c
        for c in cells:
            if _is_external_actor_cell(c):
                return c
        if logger:
            logger.log(
                "step_markers",
                f"skipping badge on SaaS leaf {target.get('id')!r} (no Edge/Cloud remount)",
                level="info",
            )
        return None

    collisions: dict[str, int] = {}
    added = False
    badge_count = 0
    from app.adapters.outbound.diagram.architecture_docs import story_step_cap
    max_badges = story_step_cap(page_name)
    for marker in step_markers:
        if badge_count >= max_badges:
            break
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

        remapped = _remap_saas_badge_target(target)
        if remapped is None:
            continue
        target = remapped
        anchor_id = target.get("id") or cell_id

        abs_x, abs_y = absolute_position(target, by_id)
        collision_index = collisions.get(anchor_id, 0)
        collisions[anchor_id] = collision_index + 1
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
        # Persist anchor so repair/spine can re-snap badges after layout moves.
        badge.set("anchor", anchor_id)
        geom = ET.SubElement(badge, "mxGeometry")
        geom.set("x", str(badge_x))
        geom.set("y", str(badge_y))
        geom.set("width", str(_STEP_BADGE_SIZE))
        geom.set("height", str(_STEP_BADGE_SIZE))
        geom.set("as", "geometry")
        added = True
        badge_count += 1

    if not added:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def reposition_step_badges(xml: str) -> str:
    """Re-snap existing step badges to their ``anchor`` cells after layout moves.

    Badges are often inserted before journey-spine / SaaS placement; without this
    pass they float at stale coordinates ("badges all over the place").
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    badges = [
        c for c in cells
        if (c.get("id") or "").startswith(_STEP_BADGE_ID_PREFIX) and c.get("anchor")
    ]
    if not badges:
        return xml

    collisions: dict[str, int] = {}
    changed = False
    for badge in badges:
        anchor_id = badge.get("anchor") or ""
        target = by_id.get(anchor_id)
        if target is None:
            continue
        abs_x, abs_y = absolute_position(target, by_id)
        collision_index = collisions.get(anchor_id, 0)
        collisions[anchor_id] = collision_index + 1
        nudge = collision_index * _STEP_BADGE_COLLISION_OFFSET
        badge_x = abs_x - _STEP_BADGE_OFFSET + nudge
        badge_y = abs_y - _STEP_BADGE_OFFSET + nudge
        geom = badge.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_x = float(geom.get("x") or 0)
            cur_y = float(geom.get("y") or 0)
        except ValueError:
            cur_x, cur_y = 0.0, 0.0
        if abs(cur_x - badge_x) > 0.5 or abs(cur_y - badge_y) > 0.5:
            geom.set("x", str(badge_x))
            geom.set("y", str(badge_y))
            changed = True
        if badge.get("parent") != "1":
            badge.set("parent", "1")
            changed = True
    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)


def _validate_container_bounds(cells: list[ET.Element], by_id: dict[str, ET.Element]) -> list[str]:
    """No child's box may exceed its parent container's bounds (relative coords)."""
    issues: list[str] = []
    for cell in cells:
        pid = cell.get("parent")
        if not pid or pid in _RESERVED_IDS:
            continue
        parent = by_id.get(pid)
        if parent is None or not _is_container_cell(parent):
            continue
        cgeo, pgeo = _cell_geometry(cell), _cell_geometry(parent)
        if cgeo is None or pgeo is None:
            continue
        if not _rect_contained((cgeo[0], cgeo[1], cgeo[2], cgeo[3]), (0.0, 0.0, pgeo[2], pgeo[3]), tol=4.0):
            issues.append(
                f"layout: {cell.get('id')!r} exceeds parent {pid!r}'s bounds"
            )
    return issues


def _validate_sibling_container_overlaps(cells: list[ET.Element]) -> list[str]:
    """No two sibling (same-parent) containers may overlap."""
    issues: list[str] = []
    by_parent: dict[str, list[ET.Element]] = {}
    for cell in cells:
        if not _is_container_cell(cell):
            continue
        pid = cell.get("parent")
        if not pid:
            continue
        by_parent.setdefault(pid, []).append(cell)
    for siblings in by_parent.values():
        geo_by = {c: _cell_geometry(c) for c in siblings}
        for i in range(len(siblings)):
            gi = geo_by.get(siblings[i])
            if gi is None:
                continue
            for j in range(i + 1, len(siblings)):
                gj = geo_by.get(siblings[j])
                if gj is None:
                    continue
                if _rects_overlap(gi, gj):
                    issues.append(
                        f"layout: sibling containers {siblings[i].get('id')!r} and "
                        f"{siblings[j].get('id')!r} overlap"
                    )
    return issues


def _validate_tier_heights(cells: list[ET.Element], by_id: dict[str, ET.Element], page_name: str) -> list[str]:
    """Within a tier — the main L->R row, and separately the stack of shelves
    anchored below it — heights should differ by at most one icon-row.

    Rows and shelves are deliberately two separate tiers, not one group by id
    prefix: a shelf anchors *below* the row by design (the IR layout shelf anchor)
    and is expected to differ in height from the row it hangs off of.

    Flags only — the row-band-uniformity pass already normalizes the main row;
    a residual difference here signals something upstream went wrong, not
    something this check should silently paper over.
    """
    if page_name not in {"logical", "security", "data flow", "data_flow"}:
        return []
    from app.adapters.outbound.diagram.logical_layout import ICON_SPACING
    from app.adapters.outbound.diagram.edge_routing import _is_anchored_shelf

    tolerance = _geo.LEAF_H + ICON_SPACING
    prefix = {"logical": "domain_", "security": "zone_", "data flow": "phase_", "data_flow": "phase_"}[page_name]
    top_level = [
        c for c in cells
        if _is_container_cell(c) and c.get("parent") == "1"
        and (c.get("id") or "").lower().startswith(prefix)
        and (c.get("id") or "") not in {_OVERVIEW_PANEL_ID, _LEGEND_ID}
    ]
    row = [c for c in top_level if not _is_anchored_shelf(page_name, c)]
    shelves = [c for c in top_level if _is_anchored_shelf(page_name, c)]

    issues: list[str] = []
    for tier_name, members in (("row", row), ("shelf", shelves)):
        heights = [(c, g[3]) for c in members if (g := _cell_geometry(c)) is not None]
        if len(heights) < 2:
            continue
        max_h = max(h for _c, h in heights)
        min_h = min(h for _c, h in heights)
        if max_h - min_h > tolerance:
            issues.append(
                f"layout: {tier_name} tier height spread {max_h - min_h:.0f}px exceeds "
                f"{tolerance:.0f}px among {[c.get('id') for c, _h in heights]}"
            )
    return issues


def _validate_edges(
    cells: list[ET.Element], by_id: dict[str, ET.Element], page_w: float, page_h: float,
) -> list[str]:
    """Edge path sanity — non-endpoint collisions, anchor/position agreement,
    excess waypoint bends, and near-duplicate parallel segments (Fix 2e, edge
    requirement #7)."""
    issues: list[str] = []
    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return issues

    leaf_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell):
            continue
        if (cell.get("id") or "") in {_OVERVIEW_PANEL_ID, _LEGEND_ID}:
            continue
        if _is_step_badge_cell(cell):
            # Badges are stuck onto an anchor cell's corner (reposition_step_
            # badges), not routed around — the edge router deliberately
            # excludes them from its own obstacle list too
            # (_logical_obstacle_rects), so flagging a "collision" here would
            # just be re-litigating a design choice, not catching a real bug.
            continue
        # Full-page background rects (found via real-capture replay: id "2",
        # "bg", "bg_fill", "bg_rect") trivially "contain" every edge's path —
        # not a real non-endpoint collision, just canvas backdrop.
        if _is_canvas_background_cell(cell, page_w, page_h):
            continue
        cgeo = _cell_geometry(cell)
        if cgeo is None:
            continue
        ax, ay = absolute_position(cell, by_id)
        leaf_boxes.append((cell.get("id") or "", (ax, ay, cgeo[2], cgeo[3])))

    polylines: dict[str, list[tuple[float, float]]] = {}
    routed_polylines: dict[str, list[tuple[float, float]]] = {}
    for edge in edges:
        eid = edge.get("id") or ""
        line = _edge_polyline(edge, by_id)
        if line and len(line) >= 2:
            polylines[eid] = line
        routed = _edge_routed_polyline(edge, by_id)
        if routed and len(routed) >= 2:
            routed_polylines[eid] = routed

    for edge in edges:
        eid = edge.get("id") or ""
        line = polylines.get(eid)
        if line is None:
            continue
        src_id, tgt_id = edge.get("source"), edge.get("target")

        # Waypoint bend count.
        if len(line) - 2 > 2:
            issues.append(f"layout: edge {eid!r} has {len(line) - 2} waypoint bends (>2)")

        # Non-endpoint node collisions (±5px tolerance), skipping the edge's own
        # endpoints. Uses the *routed* approximation (_edge_routed_polyline),
        # not the raw anchor-to-anchor line above — a straight center-to-center
        # line falsely "crosses" any box geometrically between two correctly
        # placed, connected nodes (see that function's docstring).
        routed_line = routed_polylines.get(eid)
        if routed_line is not None:
            for leaf_id, box in leaf_boxes:
                if leaf_id in (src_id, tgt_id):
                    continue
                padded = (box[0] - 5.0, box[1] - 5.0, box[2] + 10.0, box[3] + 10.0)
                for p1, p2 in zip(routed_line, routed_line[1:]):
                    seg_box = (
                        min(p1[0], p2[0]), min(p1[1], p2[1]),
                        abs(p2[0] - p1[0]) or 1.0, abs(p2[1] - p1[1]) or 1.0,
                    )
                    if _rects_overlap(seg_box, padded):
                        issues.append(f"layout: edge {eid!r} passes through non-endpoint node {leaf_id!r}")
                        break
                else:
                    continue
                break

        # Anchor agreement: exitX/entryX should match the actual dominant-axis
        # direction between source and target centers (same rule
        # fix_edge_anchors_from_geometry itself applies when it sets anchors).
        if src_id in by_id and tgt_id in by_id:
            sgeo, tgeo = _cell_geometry(by_id[src_id]), _cell_geometry(by_id[tgt_id])
            if sgeo is not None and tgeo is not None:
                sx, sy = absolute_position(by_id[src_id], by_id)
                tx, ty = absolute_position(by_id[tgt_id], by_id)
                dx = (tx + tgeo[2] / 2) - (sx + sgeo[2] / 2)
                dy = (ty + tgeo[3] / 2) - (sy + sgeo[3] / 2)
                style = edge.get("style") or ""
                exit_x = _edge_anchor_num(style, "exitX", 0.5)
                exit_y = _edge_anchor_num(style, "exitY", 0.5)
                if abs(dx) >= abs(dy):
                    expected_exit_x = 1.0 if dx >= 0 else 0.0
                    if abs(exit_x - expected_exit_x) > 0.01:
                        issues.append(f"layout: edge {eid!r} exitX anchor disagrees with actual source/target position")
                else:
                    expected_exit_y = 1.0 if dy >= 0 else 0.0
                    if abs(exit_y - expected_exit_y) > 0.01:
                        issues.append(f"layout: edge {eid!r} exitY anchor disagrees with actual source/target position")

    # Near-duplicate parallel segments: two edges sharing >50% overlap of
    # either one's length on an axis-aligned collinear segment should share a
    # routing lane instead of running independently. Uses the routed
    # approximation too, for the same reason as the box-collision check above.
    edge_ids = list(routed_polylines)
    for i in range(len(edge_ids)):
        segs_i = list(zip(routed_polylines[edge_ids[i]], routed_polylines[edge_ids[i]][1:]))
        for j in range(i + 1, len(edge_ids)):
            segs_j = list(zip(routed_polylines[edge_ids[j]], routed_polylines[edge_ids[j]][1:]))
            for a1, a2 in segs_i:
                for b1, b2 in segs_j:
                    overlap = _collinear_overlap_ratio(a1, a2, b1, b2)
                    if overlap > 0.5:
                        issues.append(
                            f"layout: edges {edge_ids[i]!r} and {edge_ids[j]!r} overlap "
                            f"{overlap:.0%} of a segment — consider a shared routing lane"
                        )
    return issues


def _collinear_overlap_ratio(
    a1: tuple[float, float], a2: tuple[float, float],
    b1: tuple[float, float], b2: tuple[float, float],
) -> float:
    """Fraction of the shorter axis-aligned segment covered by the longer one,
    when both are collinear (same horizontal or vertical line); 0 otherwise."""
    a_horiz = abs(a1[1] - a2[1]) < 1.0
    b_horiz = abs(b1[1] - b2[1]) < 1.0
    if a_horiz != b_horiz:
        return 0.0
    if a_horiz:
        if abs(a1[1] - b1[1]) > 1.0:
            return 0.0
        a_lo, a_hi = sorted((a1[0], a2[0]))
        b_lo, b_hi = sorted((b1[0], b2[0]))
    else:
        if abs(a1[0] - b1[0]) > 1.0:
            return 0.0
        a_lo, a_hi = sorted((a1[1], a2[1]))
        b_lo, b_hi = sorted((b1[1], b2[1]))
    overlap = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
    if overlap <= 0.0:
        return 0.0
    shorter = min(a_hi - a_lo, b_hi - b_lo)
    return overlap / shorter if shorter > 0 else 0.0


def _validate_orphan_leaves(cells: list[ET.Element], page_name: str) -> list[str]:
    """Flags leaves/actors with zero edges touching them — Logical only.

    Logical is flat with no grouping box (see ``CorridorIR._orphan_errors``),
    so an unconnected leaf/actor there is genuinely broken, not just
    under-annotated. Security/Data-flow are deliberately excluded: their
    zone/phase boxes are real containers, and a passive leaf (CloudWatch
    sitting in the Detect zone with no drawn edge) is a normal, valid diagram
    there — see ``_orphan_errors`` for why the same rule rejected legitimate
    fixtures when tried on those two views.

    ``CorridorIR.semantic_errors()`` already rejects this at fresh-generation
    time, but surgical edits patch raw XML directly (``diagram_edits.py``) with
    no equivalent IR-level check, so a ``delete_node``/``disconnect`` op can
    silently strand a node that generation would have refused to ship. This is
    the same "informational only, logged for visibility" pass as the rest of
    ``validate_layout`` — it does not attempt to invent a repair.
    """
    if page_name != "logical":
        return []
    touched: set[str] = set()
    for c in cells:
        if c.get("edge") == "1":
            src, tgt = c.get("source"), c.get("target")
            if src:
                touched.add(src)
            if tgt:
                touched.add(tgt)

    issues: list[str] = []
    for cell in cells:
        if cell.get("vertex") != "1" or _is_container_cell(cell) or _is_edge_label_cell(cell):
            continue
        if _is_overview_panel_cell(cell) or _is_legend_cell(cell) or _is_step_badge_cell(cell):
            continue
        cid = cell.get("id")
        if not cid or cid in touched:
            continue
        label = cell.get("value") or cid
        issues.append(
            f"layout: {label!r} has no flow connecting it to the diagram — looks disconnected"
        )
    return issues


def validate_layout(xml: str) -> list[str]:
    """Informational, best-effort layout/edge-routing validation pass (Fix 2e).

    Not wired into ``diagnose_xml``'s errors/warnings (those gate repair
    behavior) — this runs *after* ``repair_xml`` as a separate, purely
    observational check, logged for visibility rather than acted on. Never
    raises; returns an empty list for anything unparseable or clean.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return []
    tree, root, _is_mxfile, _trimmed = parsed
    from app.adapters.outbound.diagram.edge_routing import _diagram_page_name

    page_name = _diagram_page_name(tree).lower()
    page_w, page_h = _page_size(tree)
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    issues: list[str] = []
    issues.extend(_validate_container_bounds(cells, by_id))
    issues.extend(_validate_sibling_container_overlaps(cells))
    issues.extend(_validate_tier_heights(cells, by_id, page_name))
    issues.extend(_validate_edges(cells, by_id, page_w, page_h))
    issues.extend(_validate_orphan_leaves(cells, page_name))
    return issues


def repair_xml(xml: str) -> tuple[str, XmlDiagnostics]:
    """Structural hygiene + edge ports + overview/legend.

    Geometry for IR-compiled pages stays with the compiler; this path never
    re-lays out corridors/leaves. The same thin chain runs for IR and for
    rare non-IR (3D / surgical) pages — LLM-era pitch/journey mutators are gone.
    """
    dup_id_warnings: list[str] = []
    fixed = resolve_reserved_id_reuse(xml)
    fixed = resolve_duplicate_ids(fixed, dup_id_warnings)
    fixed = resolve_orphaned_parents(fixed)
    fixed = resolve_parent_cycles(fixed)
    fixed = ensure_valid_vertex_geometry(fixed)
    fixed = ensure_overview_panel_parent_root(fixed)
    fixed = clear_overview_panel_children(fixed)
    fixed = sanitize_overview_panel_style(fixed)
    fixed = ensure_leaf_labels(fixed)
    fixed = remove_dangling_edges(fixed)
    fixed = remove_cross_cutting_edges(fixed)

    from app.adapters.outbound.diagram.edge_routing import (
        fix_edge_anchors_from_geometry,
    )
    fixed = fix_edge_anchors_from_geometry(fixed)

    page_name = ""
    try:
        parsed_name = _parse_docroot(fixed)
        if parsed_name is not None:
            tree_n, _root_n, _, _ = parsed_name
            if tree_n.tag == "mxfile":
                diag = tree_n.find("diagram")
                if diag is not None:
                    page_name = (diag.get("name") or "").strip().lower()
    except Exception:
        page_name = ""

    if page_name in {"logical", "security", "data flow"}:
        from app.adapters.outbound.diagram.view_fixers import ensure_edge_label_readability
        view_key = {"logical": "logical", "security": "security", "data flow": "data_flow"}[page_name]
        fixed = ensure_edge_label_readability(fixed, view=view_key)

    fixed = reposition_step_badges(fixed)
    fixed = ensure_overview_panel_position(fixed)
    fixed = sanitize_overview_panel_style(fixed)
    fixed = ensure_legend_position(fixed)
    fixed = ensure_overview_panel_parent_root(fixed)
    fixed = move_overview_and_legend_to_end(fixed)
    diagnostics = diagnose_xml(fixed)
    diagnostics.warnings.extend(dup_id_warnings)
    return fixed, diagnostics
