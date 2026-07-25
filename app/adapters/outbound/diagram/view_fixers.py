"""Deterministic viewpoint fixers for Logical / Security / Data flow pages."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    VIEW_PAGE_NAMES,
    _infer_service_tier,
    _plain_cell_label,
)
from app.adapters.outbound.diagram.view_conformance import _FORBIDDEN_BY_VIEW, _service_leaves
from app.adapters.outbound.diagram.xml_diagnostics import remove_dangling_edges
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document

_EDGE_DEFAULTS: dict[str, dict[str, str]] = {
    "logical": {
        "edge->app": "Calls API",
        "app->data": "Reads/Writes via API",
        "app->app": "Invokes",
        "default": "Relies on",
    },
    "security": {
        "edge->app": "TLS",
        "edge->edge": "inspect",
        "app->data": "private path",
        "identity->*": "AuthN",
        "default": "perimeter cross",
    },
    "data_flow": {
        "app->data": "SQL: CustomerRow",
        "app->cache": "Cache: SessionKey",
        "edge->app": "JSON: Request",
        "app->app": "Event: DomainEvent",
        "default": "JSON: Payload",
    },
}

_DB_BRANDS = (
    "aurora", "rds", "postgres", "postgresql", "mysql", "dynamodb", "mongodb",
    "redis", "elasticache", "cosmos", "spanner", "bigtable", "firestore",
)


def _serialize(tree: ET.Element) -> str:
    return ET.tostring(tree, encoding="unicode")


def _parse(page_xml: str, title: str) -> ET.Element | None:
    try:
        return ET.fromstring(ensure_diagram_document(page_xml or "", title))
    except ET.ParseError:
        return None


def drop_forbidden_nodes(page_xml: str, *, view: str) -> str:
    """Remove leaves whose labels match the view's forbidden vocabulary."""
    title = VIEW_PAGE_NAMES.get(view, view)
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    forbidden = _FORBIDDEN_BY_VIEW.get(view, ())
    if not forbidden:
        return page_xml
    drop_ids: set[str] = set()
    for cell in _service_leaves(tree):
        cid = cell.get("id") or ""
        if cell.get("gap") == "1" or cid.startswith("gap_"):
            continue
        label = _plain_cell_label(cell.get("value") or "").lower()
        if view == "logical" and "data store" in label:
            continue
        if any(tok in label for tok in forbidden):
            drop_ids.add(cid)
    if not drop_ids:
        return page_xml
    root = tree.find(".//root")
    if root is None:
        root = tree
    for cell in list(root.findall("mxCell")):
        cid = cell.get("id") or ""
        if cid in drop_ids:
            root.remove(cell)
            continue
        if cell.get("edge") == "1":
            if (cell.get("source") or "") in drop_ids or (cell.get("target") or "") in drop_ids:
                root.remove(cell)
    cleaned = _serialize(tree)
    return remove_dangling_edges(cleaned)


def relabel_generic_data_stores(page_xml: str, *, view: str) -> str:
    """On Logical, rename DB brand leaves to generic data-store labels; keep shape_type."""
    if view != "logical":
        return page_xml
    title = VIEW_PAGE_NAMES.get(view, view)
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    changed = False
    for cell in _service_leaves(tree):
        label = _plain_cell_label(cell.get("value") or "")
        low = label.lower()
        if "data store" in low:
            continue
        if any(b in low for b in _DB_BRANDS):
            # Preserve role hints
            role = "Customer"
            if "session" in low or "cache" in low or "redis" in low or "elasticache" in low:
                role = "Session"
            elif "order" in low:
                role = "Order"
            elif "user" in low:
                role = "User"
            cell.set("value", f"{role} data store")
            changed = True
    return _serialize(tree) if changed else page_xml


def _tier_pair(src_label: str, tgt_label: str, src_st: str, tgt_st: str) -> str:
    src_t = _infer_service_tier(src_label, src_st, "")
    tgt_t = _infer_service_tier(tgt_label, tgt_st, "")
    if "cache" in tgt_label.lower() or "redis" in tgt_label.lower():
        return "app->cache"
    if src_t == "edge" and tgt_t == "app":
        return "edge->app"
    if src_t == "app" and tgt_t == "data":
        return "app->data"
    if src_t == "app" and tgt_t == "app":
        return "app->app"
    if src_t == "edge" and tgt_t == "edge":
        return "edge->edge"
    if src_t == "security" or "cognito" in src_label.lower() or "iam" in src_label.lower():
        return "identity->*"
    return "default"


def apply_edge_label_defaults(page_xml: str, *, view: str) -> str:
    """Fill blank edge labels from source/target tiers using the view lexicon."""
    title = VIEW_PAGE_NAMES.get(view, view)
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    defaults = _EDGE_DEFAULTS.get(view)
    if not defaults:
        return page_xml
    by_id = {c.get("id"): c for c in tree.iter("mxCell") if c.get("id")}
    changed = False
    for cell in tree.iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        if (_plain_cell_label(cell.get("value") or "") or "").strip():
            continue
        src = by_id.get(cell.get("source") or "")
        tgt = by_id.get(cell.get("target") or "")
        if src is None or tgt is None:
            continue
        src_l = _plain_cell_label(src.get("value") or "")
        tgt_l = _plain_cell_label(tgt.get("value") or "")
        key = _tier_pair(src_l, tgt_l, src.get("shape_type") or "", tgt.get("shape_type") or "")
        label = defaults.get(key) or defaults["default"]
        cell.set("value", label)
        style = cell.get("style") or ""
        if "labelBackgroundColor=" not in style:
            style = (
                style.rstrip(";") + ";"
                if style and not style.endswith(";")
                else style
            )
            style += "labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;"
            cell.set("style", style)
        changed = True
    return _serialize(tree) if changed else page_xml


def space_detail_cards(page_xml: str, *, view: str = "") -> str:
    """Re-layout note_/ctrl_ cards so they never overlap each other or the overview.

    Places cards on a dedicated shelf **below** architecture content (parent=\"1\"),
    left-to-right with wrapping, keeping a clear gap from the overview panel.
    """
    from app.adapters.outbound.diagram.xml_diagnostics import absolute_position

    title = VIEW_PAGE_NAMES.get(view, view) if view else "Page"
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    root = tree.find(".//root")
    if root is None:
        # mxGraphModel root may be the tree itself after ensure_diagram_document
        root = tree.find("root") if tree.tag == "mxGraphModel" else None
        if root is None and tree.tag == "root":
            root = tree
    if root is None:
        return page_xml

    by_id = {c.get("id"): c for c in tree.iter("mxCell") if c.get("id")}
    cards = [
        c for c in tree.iter("mxCell")
        if (c.get("id") or "").startswith(("note_", "ctrl_")) and c.get("vertex") == "1"
    ]
    if not cards:
        return page_xml

    overview = by_id.get("overview_panel")
    ov_x = ov_y = ov_w = None
    if overview is not None:
        ox, oy = absolute_position(overview, by_id)
        ogeo = overview.find("mxGeometry")
        try:
            ov_x = ox
            ov_y = oy
            ov_w = float(ogeo.get("width") or 400) if ogeo is not None else 400.0
        except ValueError:
            ov_x, ov_y, ov_w = ox, oy, 400.0

    # Content bounds (architecture — exclude cards, overview, badges, gaps)
    content_max_y = 40.0
    content_min_x = 40.0
    content_max_x = 400.0
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        if cid in {"0", "1", "overview_panel", "legend_box"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_")
        ):
            continue
        style = cell.get("style") or ""
        if "fillColor=#F5F5F5" in style and "strokeColor=none" in style and cid in {"2", "bg", "background"}:
            continue
        ax, ay = absolute_position(cell, by_id)
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        try:
            w = float(geo.get("width") or 78)
            h = float(geo.get("height") or 78)
        except ValueError:
            continue
        # Under-icon label band for leaves
        if "container=1" not in style and "swimlane" not in style:
            h += 36
        content_max_y = max(content_max_y, ay + h)
        content_min_x = min(content_min_x, ax)
        content_max_x = max(content_max_x, ax + w)

    card_gap = 24.0
    shelf_gap = 48.0
    card_w_default = 280.0
    shelf_y = content_max_y + shelf_gap
    # Right limit: left of overview, or content extent + margin
    right_limit = (ov_x - 40.0) if ov_x is not None else max(content_max_x + 40.0, 1600.0)
    left = max(40.0, content_min_x)
    usable = max(card_w_default, right_limit - left)
    # How many cards per row
    per_row = max(1, int((usable + card_gap) // (card_w_default + card_gap)))

    # Stable order by id so layout is deterministic
    cards_sorted = sorted(cards, key=lambda c: c.get("id") or "")
    changed = False
    row_heights: dict[int, float] = {}
    # First pass: measure heights
    heights: list[float] = []
    for card in cards_sorted:
        geo = card.find("mxGeometry")
        try:
            h = float(geo.get("height") or 120) if geo is not None else 120.0
            # Grow height to fit HTML-ish bullet count when tiny
            val = card.get("value") or ""
            bullets = val.count("•") + val.count("&lt;div&gt;") // 2
            h = max(h, 56 + 18 * max(3, min(bullets, 6)))
        except ValueError:
            h = 120.0
        heights.append(h)

    for i, card in enumerate(cards_sorted):
        row = i // per_row
        col = i % per_row
        geo = card.find("mxGeometry")
        if geo is None:
            geo = ET.SubElement(card, "mxGeometry")
            geo.set("as", "geometry")
        w = card_w_default
        h = heights[i]
        row_heights[row] = max(row_heights.get(row, 0.0), h)
        # y = shelf + sum of prior row heights
        y = shelf_y
        for r in range(row):
            y += row_heights.get(r, h) + card_gap
        x = left + col * (card_w_default + card_gap)
        # Clamp so card stays left of overview
        if ov_x is not None and x + w > ov_x - 20:
            x = max(40.0, ov_x - w - 24)

        # Reparent to page root so relative container offsets don't stack overlaps
        if card.get("parent") != "1":
            card.set("parent", "1")
            changed = True

        try:
            old_x, old_y = float(geo.get("x") or 0), float(geo.get("y") or 0)
            old_w, old_h = float(geo.get("width") or 0), float(geo.get("height") or 0)
        except ValueError:
            old_x = old_y = old_w = old_h = -1
        nx, ny = float(int(x // 20) * 20), float(int(y // 20) * 20)
        nw, nh = float(int(w)), float(int(h))
        if (old_x, old_y, old_w, old_h) != (nx, ny, nw, nh):
            geo.set("x", str(int(nx)))
            geo.set("y", str(int(ny)))
            geo.set("width", str(int(nw)))
            geo.set("height", str(int(nh)))
            geo.set("as", "geometry")
            changed = True

    # Grow page if cards extend past pageHeight
    if changed:
        max_bottom = shelf_y
        for r, rh in row_heights.items():
            max_bottom = max(max_bottom, shelf_y + sum(
                row_heights.get(rr, 0) + card_gap for rr in range(r)
            ) + rh + 40)
        for model in tree.iter("mxGraphModel"):
            try:
                ph = float(model.get("pageHeight") or 1400)
            except ValueError:
                ph = 1400.0
            if max_bottom + 40 > ph:
                model.set("pageHeight", str(int(max(ph, max_bottom + 80))))
                # Stretch background cell if present
                for cell in tree.iter("mxCell"):
                    style = cell.get("style") or ""
                    if "strokeColor=none" in style and cell.get("parent") == "1":
                        g = cell.find("mxGeometry")
                        if g is not None:
                            try:
                                if float(g.get("height") or 0) >= ph - 1:
                                    g.set("height", model.get("pageHeight"))
                            except ValueError:
                                pass

    return _serialize(tree) if changed else page_xml


def apply_view_fixers(page_xml: str, *, view: str) -> str:
    """Run the deterministic fixer chain for a focused view page."""
    xml = drop_forbidden_nodes(page_xml, view=view)
    xml = relabel_generic_data_stores(xml, view=view)
    xml = apply_edge_label_defaults(xml, view=view)
    xml = space_detail_cards(xml, view=view)
    return xml


def ensure_gap_nodes(
    page_xml: str,
    *,
    view: str,
    gap_labels: list[str],
) -> str:
    """Append recommended gaps as an Overview Panel section (not on-canvas boxes).

    Existing overview steps/title styling are preserved. Gap bullets use the
    recommended-gap red (#B85450). Any legacy ``gap_*`` canvas nodes are removed.
    """
    return append_gaps_to_overview_panel(page_xml, view=view, gap_labels=gap_labels)


_GAP_SECTION_TITLE = "Recommended gaps (not in current design)"
_GAP_COLOR = "#B85450"


def _strip_gap_section_html(decoded_html: str) -> str:
    """Remove a previously injected gaps block from overview HTML (decoded)."""
    if not decoded_html:
        return ""
    # Drop any div that is part of the gaps section (marker attr or gap colour).
    pattern = re.compile(
        r'<div[^>]*(?:data-section=["\']gaps["\']|color:\s*#B85450)[^>]*>.*?</div>\s*',
        re.IGNORECASE | re.DOTALL,
    )
    cleaned = pattern.sub("", decoded_html)
    # Also drop bare title line if left behind
    cleaned = re.sub(
        re.escape(_GAP_SECTION_TITLE),
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _gaps_section_html(gap_labels: list[str]) -> str:
    """Decoded HTML fragment for the gaps section (for Element.set / trim)."""
    import html as html_mod

    parts = [
        f'<div data-section="gaps" style="font-size:14px;font-weight:bold;color:{_GAP_COLOR};'
        f'margin-top:10px;">{_GAP_SECTION_TITLE}</div>'
    ]
    for label in gap_labels:
        text = (label or "").strip()
        if not text:
            continue
        low = text.lower()
        if "(recommended" in low:
            text = text[: low.index("(recommended")].strip(" -–—")
        safe = html_mod.escape(text)
        parts.append(
            f'<div data-section="gaps" style="font-size:13px;color:{_GAP_COLOR};">'
            f"• {safe}</div>"
        )
    return "".join(parts)


def append_gaps_to_overview_panel(
    page_xml: str,
    *,
    view: str,
    gap_labels: list[str],
) -> str:
    """Write gap recommendations into overview_panel; remove on-canvas gap boxes."""
    import html as html_mod

    if view not in {"security", "data_flow"}:
        # Still strip any stray canvas gap nodes on other views
        gap_labels = []
    title = VIEW_PAGE_NAMES.get(view, view)
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml

    root = tree.find(".//root")
    if root is None:
        return page_xml

    changed = False
    # Remove legacy on-canvas gap boxes
    for cell in list(root.findall("mxCell")):
        cid = cell.get("id") or ""
        if cell.get("gap") == "1" or cid.startswith("gap_"):
            root.remove(cell)
            changed = True

    # Dedupe / clean labels
    clean: list[str] = []
    seen: set[str] = set()
    for label in gap_labels or []:
        text = (label or "").strip()
        if not text:
            continue
        key = text.lower().split("(recommended")[0].strip()
        if key in seen:
            continue
        seen.add(key)
        clean.append(text)
    clean = clean[:6]

    panel = next((c for c in tree.iter("mxCell") if c.get("id") == "overview_panel"), None)
    if panel is None:
        return _serialize(tree) if changed else page_xml

    raw = panel.get("value") or ""
    decoded = html_mod.unescape(raw)
    base = _strip_gap_section_html(decoded)
    if clean:
        # Append decoded gaps HTML; ElementTree escapes on serialize.
        # If base was already entity-escaped content that unescape restored, good.
        new_val = base + _gaps_section_html(clean)
        # Prefer storing decoded HTML for Element.set (matches overview trim contract)
        if new_val != decoded or "&lt;div" in raw:
            panel.set("value", new_val)
            changed = True
        # Grow panel height for the extra section
        geo = panel.find("mxGeometry")
        if geo is not None:
            try:
                h = float(geo.get("height") or 200)
            except ValueError:
                h = 200.0
            needed = h + 28 + 18 * len(clean)
            if needed > h:
                geo.set("height", str(int(min(needed, 560))))
                changed = True
    elif _GAP_SECTION_TITLE.lower() in decoded.lower() or 'data-section="gaps"' in decoded:
        panel.set("value", base)
        changed = True

    return _serialize(tree) if changed else page_xml
