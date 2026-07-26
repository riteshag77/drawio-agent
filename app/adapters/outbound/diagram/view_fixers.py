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
    """Place note_/ctrl_ cards just under their related section boxes.

    Matches each card to a container by id/label keywords, parks it ~16px below
    that box (left-aligned), then nudges to clear overlaps and the overview panel.
    """
    from app.adapters.outbound.diagram.xml_diagnostics import absolute_position
    from app.adapters.outbound.diagram.xml_tools import _is_container_cell

    title = VIEW_PAGE_NAMES.get(view, view) if view else "Page"
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    root = tree.find(".//root")
    if root is None:
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
    ov_x = ov_y = ov_w = ov_h = None
    if overview is not None:
        ox, oy = absolute_position(overview, by_id)
        ogeo = overview.find("mxGeometry")
        try:
            ov_x, ov_y = ox, oy
            ov_w = float(ogeo.get("width") or 400) if ogeo is not None else 400.0
            ov_h = float(ogeo.get("height") or 200) if ogeo is not None else 200.0
        except ValueError:
            ov_x, ov_y, ov_w, ov_h = ox, oy, 400.0, 200.0

    # Prefer page-level / major containers for anchoring
    containers: list[tuple[ET.Element, float, float, float, float, str]] = []
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        if cid in {"0", "1", "2", "overview_panel", "legend_box"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_", "degraded_")
        ):
            continue
        if not _is_container_cell(cell):
            continue
        ax, ay = absolute_position(cell, by_id)
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        try:
            w = float(geo.get("width") or 200)
            h = float(geo.get("height") or 120)
        except ValueError:
            continue
        label = ((cell.get("value") or "") + " " + cid).lower()
        containers.append((cell, ax, ay, w, h, label))

    # Fallback content bounds when no containers exist
    content_max_y = 40.0
    content_min_x = 40.0
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
        if "container=1" not in style and "swimlane" not in style:
            h += 36
        content_max_y = max(content_max_y, ay + h)
        content_min_x = min(content_min_x, ax)

    def _card_needles(cid: str) -> tuple[str, ...]:
        key = (cid or "").lower()
        mapping = {
            "note_experience": ("experience", "channel", "persona", "frontend", "client", "user", "portal"),
            "note_core": ("core", "commerce", "order", "checkout", "business", "capability", "domain"),
            "note_data_cap": ("data", "store", "persistence", "datastore"),
            "note_shared": ("shared", "platform", "observ", "notification", "monitor"),
            "ctrl_public": ("public", "internet", "untrusted", "external"),
            "ctrl_dmz": ("dmz", "perimeter", "edge", "waf"),
            "ctrl_private": ("private", "application", "app zone", "trusted app"),
            "ctrl_restricted": ("restricted", "sensitive", "data zone", "highly"),
            "ctrl_detect": ("detect", "respond", "monitor", "observ", "security ops"),
            "note_ingest": ("ingest", "producer", "ingress", "source"),
            "note_process": ("process", "processing", "transform", "stream"),
            "note_store": ("store", "storage", "persist", "database", "warehouse"),
            "note_consume": ("consum", "analytic", "read model", "downstream"),
        }
        if key in mapping:
            return mapping[key]
        # Derive from id suffix: note_foo / ctrl_bar
        suffix = key.split("_", 1)[-1] if "_" in key else key
        return (suffix, suffix.replace("_", " "))

    def _match_container(cid: str) -> tuple[float, float, float, float] | None:
        needles = _card_needles(cid)
        best: tuple[float, float, float, float, float] | None = None
        best_score = 0
        for _cell, ax, ay, w, h, label in containers:
            score = sum(1 for n in needles if n in label)
            if score == 0:
                continue
            score_tie = score * 1000 + (w * h) / 10000.0
            if best is None or score > best_score or (score == best_score and score_tie > best[0]):
                best_score = score
                best = (score_tie, ax, ay, w, h)
        if best is None:
            return None
        return best[1], best[2], best[3], best[4]

    card_gap = 16.0
    under_gap = 16.0
    card_w_default = 260.0
    cards_sorted = sorted(cards, key=lambda c: c.get("id") or "")
    placements: list[tuple[ET.Element, float, float, float, float]] = []

    for card in cards_sorted:
        geo = card.find("mxGeometry")
        try:
            h = float(geo.get("height") or 120) if geo is not None else 120.0
            val = card.get("value") or ""
            bullets = val.count("•") + val.count("&lt;div&gt;") // 2
            h = max(h, 56 + 18 * max(3, min(bullets, 6)))
        except ValueError:
            h = 120.0
        w = card_w_default
        cid = card.get("id") or ""
        matched = _match_container(cid)
        if matched is not None:
            cx, cy, cw, ch = matched
            x = cx
            y = cy + ch + under_gap
            # If container is wide, keep card under left side (not stretched)
            if cw > w + 40:
                x = cx
        else:
            # No section match — park tightly under content, not a distant shelf
            x = content_min_x
            y = content_max_y + under_gap
            # Spread unmatched cards horizontally
            idx = len(placements)
            x = content_min_x + (idx % 3) * (card_w_default + card_gap)
            if idx >= 3:
                y = content_max_y + under_gap + (h + card_gap) * (idx // 3)

        # Keep left of overview
        if ov_x is not None and x + w > ov_x - 20:
            x = max(40.0, ov_x - w - 20)
        placements.append((card, x, y, w, h))

    # Resolve pairwise overlaps (push down)
    for _ in range(8):
        moved = False
        for i in range(len(placements)):
            for j in range(i + 1, len(placements)):
                ai, ax, ay, aw, ah = placements[i]
                bj, bx, by, bw, bh = placements[j]
                if ax + aw + 4 <= bx or bx + bw + 4 <= ax or ay + ah + 4 <= by or by + bh + 4 <= ay:
                    continue
                # Push the lower/later card down
                new_y = max(ay, by) + (ah if ay <= by else bh) + card_gap
                if by <= ay:
                    placements[i] = (ai, ax, new_y, aw, ah)
                else:
                    placements[j] = (bj, bx, new_y, bw, bh)
                moved = True
        if not moved:
            break

    # Avoid overlapping the overview panel itself
    if ov_x is not None and ov_y is not None and ov_w is not None and ov_h is not None:
        for i, (card, x, y, w, h) in enumerate(placements):
            if not (x + w > ov_x and x < ov_x + ov_w and y + h > ov_y and y < ov_y + ov_h):
                continue
            # Prefer slide left; else drop below overview
            left_x = max(40.0, ov_x - w - 20)
            if left_x + w <= ov_x - 8:
                placements[i] = (card, left_x, y, w, h)
            else:
                placements[i] = (card, x, ov_y + ov_h + card_gap, w, h)

    changed = False
    max_bottom = 0.0
    for card, x, y, w, h in placements:
        geo = card.find("mxGeometry")
        if geo is None:
            geo = ET.SubElement(card, "mxGeometry")
            geo.set("as", "geometry")
        if card.get("parent") != "1":
            card.set("parent", "1")
            changed = True
        try:
            old_x, old_y = float(geo.get("x") or 0), float(geo.get("y") or 0)
            old_w, old_h = float(geo.get("width") or 0), float(geo.get("height") or 0)
        except ValueError:
            old_x = old_y = old_w = old_h = -1
        nx, ny = float(int(x // 10) * 10), float(int(y // 10) * 10)
        nw, nh = float(int(w)), float(int(h))
        max_bottom = max(max_bottom, ny + nh)
        if (old_x, old_y, old_w, old_h) != (nx, ny, nw, nh):
            geo.set("x", str(int(nx)))
            geo.set("y", str(int(ny)))
            geo.set("width", str(int(nw)))
            geo.set("height", str(int(nh)))
            geo.set("as", "geometry")
            changed = True

    if changed:
        for model in tree.iter("mxGraphModel"):
            try:
                ph = float(model.get("pageHeight") or 1400)
            except ValueError:
                ph = 1400.0
            if max_bottom + 40 > ph:
                model.set("pageHeight", str(int(max(ph, max_bottom + 80))))
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
