"""Deterministic viewpoint fixers for Logical / Security / Data flow pages."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    VIEW_PAGE_NAMES,
    _infer_service_tier,
    _plain_cell_label,
    ensure_saas_labels_on_logical,
)
from app.adapters.outbound.diagram.view_conformance import _FORBIDDEN_BY_VIEW, _service_leaves
from app.adapters.outbound.diagram.xml_diagnostics import remove_dangling_edges
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document

_EDGE_DEFAULTS: dict[str, dict[str, str]] = {
    "logical": {
        "edge->app": "Calls API",
        "app->data": "Reads/Writes via API",
        "app->app": "Invokes",
        "actor->app": "Uses",
        "actor->identity": "Authenticates via",
        "app->identity": "Authenticates via",
        "identity->*": "Issues token",
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
        "app->data": "SQL: Record",
        "app->cache": "Cache: SessionKey",
        "edge->app": "JSON: Request",
        "app->app": "Event: DomainEvent",
        "default": "JSON: Payload",
    },
    # Network IR historically omitted labels ("prefer empty"); fill from hop tier
    # so Platform/SRE reviews still see protocol intent on every edge.
    "network": {
        "edge->edge": "HTTPS",
        "edge->app": "HTTPS",
        "app->data": "SQL",
        "app->app": "HTTPS",
        "app->cache": "cache",
        "actor->identity": "AuthN",
        "actor->app": "HTTPS",
        "identity->*": "token",
        "default": "HTTPS",
    },
}

_DB_BRANDS = (
    "aurora", "rds", "postgres", "postgresql", "mysql", "dynamodb", "mongodb",
    "redis", "elasticache", "cosmos", "spanner", "bigtable", "firestore",
)

_MESSAGING_BRANDS = (
    "kafka", "amazon sqs", "amazon sns", "sqs", "sns", "kinesis", "eventbridge",
    "pubsub", "service bus", "rabbitmq", "activemq",
)

# Short product tokens that must not use naive substring match (e.g. "alb" ⊂ "scalable").
_LOGICAL_EXACT_FORBIDDEN = frozenset({
    "alb", "nlb", "cdn", "waf", "vpc", "vnet", "nat", "igw", "tgw",
})


def _serialize(tree: ET.Element) -> str:
    return ET.tostring(tree, encoding="unicode")


def _parse(page_xml: str, title: str) -> ET.Element | None:
    try:
        return ET.fromstring(ensure_diagram_document(page_xml or "", title))
    except ET.ParseError:
        return None


def _splice_through_dropped_nodes(
    root: ET.Element, drop_ids: set[str],
) -> list[ET.Element]:
    """Rewire edges through a dropped pass-through node instead of deleting them.

    A node like a NAT Gateway relaying app→SaaS egress is forbidden in the
    Security view, but the *connection* it represents (app calls SaaS) is
    real and should survive as a direct edge — otherwise dropping the node
    silently erases the relationship from the diagram instead of just its
    (out-of-scope) mechanism.
    """
    edges = [c for c in root.findall("mxCell") if c.get("edge") == "1"]
    spliced: list[ET.Element] = []
    for did in drop_ids:
        incoming = [e for e in edges if e.get("target") == did and (e.get("source") or "") not in drop_ids]
        outgoing = [e for e in edges if e.get("source") == did and (e.get("target") or "") not in drop_ids]
        if not incoming or not outgoing:
            continue
        in_edge = incoming[0]
        for i, out_edge in enumerate(outgoing):
            new_edge = ET.Element("mxCell")
            new_edge.set("id", f"{did}_splice_{i}")
            new_edge.set("edge", "1")
            new_edge.set("parent", out_edge.get("parent") or in_edge.get("parent") or "1")
            new_edge.set("source", in_edge.get("source") or "")
            new_edge.set("target", out_edge.get("target") or "")
            label = out_edge.get("value") or in_edge.get("value") or ""
            if label:
                new_edge.set("value", label)
            style = out_edge.get("style") or in_edge.get("style") or ""
            if style:
                new_edge.set("style", style)
            geom = ET.SubElement(new_edge, "mxGeometry")
            geom.set("relative", "1")
            geom.set("as", "geometry")
            spliced.append(new_edge)
    return spliced


def drop_forbidden_nodes(page_xml: str, *, view: str) -> str:
    """Remove leaves whose labels match the view's forbidden vocabulary."""
    title = VIEW_PAGE_NAMES.get(view, view)
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    forbidden = _FORBIDDEN_BY_VIEW.get(view, ())
    if not forbidden and view != "logical":
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
            continue
        if view == "logical":
            tokens = {t.strip(".,;:()") for t in label.replace("/", " ").split() if t.strip(".,;:()")}
            if label.strip() in _LOGICAL_EXACT_FORBIDDEN or tokens & _LOGICAL_EXACT_FORBIDDEN:
                drop_ids.add(cid)
    if not drop_ids:
        return page_xml
    root = tree.find(".//root")
    if root is None:
        root = tree
    spliced = _splice_through_dropped_nodes(root, drop_ids)
    for cell in list(root.findall("mxCell")):
        cid = cell.get("id") or ""
        if cid in drop_ids:
            root.remove(cell)
            continue
        if cell.get("edge") == "1":
            if (cell.get("source") or "") in drop_ids or (cell.get("target") or "") in drop_ids:
                root.remove(cell)
    for edge in spliced:
        root.append(edge)
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
        st = (cell.get("shape_type") or "").strip().lower()
        if st.startswith(("aws_", "gcp_", "azure_")):
            continue
        label = _plain_cell_label(cell.get("value") or "")
        low = label.lower()
        if "data store" in low:
            continue
        if any(p in low for p in (
            "dynamodb", "cognito", "aurora", "rds", "cosmos", "firestore", "spanner",
        )):
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


def relabel_messaging_as_capabilities(page_xml: str, *, view: str) -> str:
    """On Logical, rename broker/queue product leaves to async capability labels."""
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
        if "async" in low and "intake" in low:
            continue
        if "capability" in low:
            continue
        if any(b in low for b in _MESSAGING_BRANDS):
            cell.set("value", "Async messaging capability")
            changed = True
    return _serialize(tree) if changed else page_xml


def strip_cloud_shape_types(page_xml: str, *, view: str) -> str:
    """Formerly stripped all cloud shape_types on Logical.

    Logical now keeps intentional ``aws_*`` / ``gcp_*`` / ``azure_*`` keys so
    serverless / solution-architecture briefs get MCP icons. Network plumbing
    is still removed via ``drop_forbidden_nodes``. No-op for call-site compatibility.
    """
    return page_xml


def _is_data_store_label(label: str) -> bool:
    low = (label or "").lower()
    return "data store" in low or low.endswith(" store") or any(
        x in low for x in ("datastore", "database", "persistence", "warehouse")
    )


def _is_identity_saas_label(label: str, shape_type: str = "") -> bool:
    st = (shape_type or "").strip().lower()
    if st in {"saas_okta"} or st.endswith("_okta"):
        return True
    low = (label or "").lower()
    return any(
        x in low
        for x in ("okta", "auth0", "idp", "identity provider", "sso", "entra id", "login")
    ) and "store" not in low


def _is_actor_label(label: str, shape_type: str = "") -> bool:
    st = (shape_type or "").strip().lower()
    if st.startswith("logical_actor") or st in {"aws_users", "aws_client", "gcp_users"}:
        return True
    low = (label or "").lower().strip()
    return low in {
        "user", "users", "customer", "customers", "employee", "employees",
        "persona", "actor", "end user", "end users", "partner",
    } or low.startswith("persona")


def _tier_pair(src_label: str, tgt_label: str, src_st: str, tgt_st: str) -> str:
    src_t = _infer_service_tier(src_label, src_st, "")
    tgt_t = _infer_service_tier(tgt_label, tgt_st, "")
    if _is_data_store_label(src_label) or _is_data_store_label(tgt_label):
        if _is_data_store_label(tgt_label):
            return "app->data"
        # data → anything else is not a normal Logical hop
        return "default"
    if _is_identity_saas_label(tgt_label, tgt_st) or tgt_t == "security":
        if _is_actor_label(src_label, src_st):
            return "actor->identity"
        return "app->identity"
    if _is_actor_label(src_label, src_st):
        return "actor->app"
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
    if _is_identity_saas_label(src_label, src_st):
        return "identity->*"
    return "default"


def drop_nonsensical_logical_edges(page_xml: str, *, view: str = "") -> str:
    """Remove Logical edges that invent impossible journeys (e.g. data store → Okta).

    Also rewrite leftover ``Invokes`` on identity hops to ``Authenticates via``.
    """
    if view and view != "logical":
        return page_xml
    title = VIEW_PAGE_NAMES.get(view or "logical", "Logical")
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    root = tree.find(".//root")
    if root is None:
        root = tree
    by_id = {c.get("id"): c for c in tree.iter("mxCell") if c.get("id")}
    drop: list[ET.Element] = []
    changed = False
    for cell in list(root.findall("mxCell")):
        if cell.get("edge") != "1":
            continue
        src = by_id.get(cell.get("source") or "")
        tgt = by_id.get(cell.get("target") or "")
        if src is None or tgt is None:
            continue
        src_l = _plain_cell_label(src.get("value") or "")
        tgt_l = _plain_cell_label(tgt.get("value") or "")
        src_st = src.get("shape_type") or ""
        tgt_st = tgt.get("shape_type") or ""
        # Data store must not call IdP / SaaS identity — drop the edge.
        if _is_data_store_label(src_l) and (
            _is_identity_saas_label(tgt_l, tgt_st) or "okta" in tgt_l.lower()
        ):
            drop.append(cell)
            continue
        if _is_identity_saas_label(src_l, src_st) and _is_data_store_label(tgt_l):
            drop.append(cell)
            continue
        # Fix bad verb on identity edges the LLM still emitted.
        plain = (_plain_cell_label(cell.get("value") or "") or "").strip().lower()
        if _is_identity_saas_label(tgt_l, tgt_st) and plain in {
            "invokes", "calls api", "reads/writes via api",
        }:
            cell.set("value", "Authenticates via")
            changed = True
        if _is_data_store_label(tgt_l) and plain == "invokes":
            cell.set("value", "Reads/Writes via API")
            changed = True
        # Cache-miss verb belongs on profile/directory SaaS only — never Stripe/Twilio/FHIR.
        if plain.startswith("cache miss") and not _is_identity_saas_label(tgt_l, tgt_st):
            tgt_low = tgt_l.lower()
            if any(tok in tgt_low for tok in (
                "stripe", "twilio", "fhir", "hl7", "billing", "sms", "payment",
            )):
                cell.set("value", "Calls API")
                changed = True
    for cell in drop:
        try:
            root.remove(cell)
            changed = True
        except ValueError:
            pass
    if not changed:
        return page_xml
    return remove_dangling_edges(_serialize(tree))


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


_EDGE_LABEL_READABILITY = (
    "labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;"
)


def ensure_edge_label_readability(page_xml: str, *, view: str = "") -> str:
    """Opaque label backgrounds + orthogonal style so line text stays readable.

    Applies to Logical / Security / Data flow (and any page passed through view fixers).
    Does not invent labels — only styles edges that already have (or will have) text.
    """
    title = VIEW_PAGE_NAMES.get(view, view) if view else "Page"
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    changed = False
    for cell in tree.iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        style = cell.get("style") or ""
        parts = [p for p in style.split(";") if p]
        keys = {p.split("=", 1)[0] for p in parts if "=" in p}
        new_parts = list(parts)
        dirty = False
        if "edgeStyle" not in keys:
            new_parts.insert(0, "edgeStyle=orthogonalEdgeStyle")
            dirty = True
        if "rounded" not in keys:
            new_parts.append("rounded=1")
            dirty = True
        if "html" not in keys:
            new_parts.append("html=1")
            dirty = True
        has_label = bool((_plain_cell_label(cell.get("value") or "") or "").strip())
        if has_label:
            if "labelBackgroundColor" not in keys:
                new_parts.append("labelBackgroundColor=#F5F5F5")
                dirty = True
            if "fontSize" not in keys:
                new_parts.append("fontSize=11")
                dirty = True
            if "fontColor" not in keys:
                new_parts.append("fontColor=#333333")
                dirty = True
            # Soft-cap very long labels that paint over icons.
            plain = _plain_cell_label(cell.get("value") or "")
            if len(plain) > 36:
                cell.set("value", plain[:33].rstrip() + "…")
                dirty = True
        if dirty:
            cell.set("style", ";".join(new_parts) + ";")
            changed = True
    return _serialize(tree) if changed else page_xml


def strip_detail_cards(page_xml: str, *, view: str = "") -> str:
    """Remove note_*/ctrl_* prose cards — Overview + badges own the story."""
    title = VIEW_PAGE_NAMES.get(view, view or "Page")
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    root = tree.find(".//root")
    if root is None:
        root = tree
    drop_ids: set[str] = set()
    for cell in list(root.findall("mxCell")):
        cid = cell.get("id") or ""
        if cid.startswith(("note_", "ctrl_")):
            drop_ids.add(cid)
            root.remove(cell)
    if not drop_ids:
        return page_xml
    for cell in list(root.findall("mxCell")):
        if cell.get("edge") == "1":
            if (cell.get("source") or "") in drop_ids or (cell.get("target") or "") in drop_ids:
                root.remove(cell)
    return remove_dangling_edges(_serialize(tree))


def sanitize_nested_leaf_annotations(page_xml: str, *, view: str = "") -> str:
    """Flatten ``Label [Multi-AZ [PHI]]`` → ``Label [Multi-AZ PHI]`` on vertices."""
    title = VIEW_PAGE_NAMES.get(view, view or "Page")
    tree = _parse(page_xml, title)
    if tree is None:
        return page_xml
    changed = False
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        val = cell.get("value") or ""
        if "[" not in val or val.count("[") < 2:
            continue
        # Only touch plain-text labels (no HTML overview panels).
        if "<" in val or "&lt;" in val:
            continue
        flat = re.sub(r"\[([^\[\]]*)\[([^\[\]]*)\]\]", r"[\1 \2]", val)
        flat = re.sub(r"\s+", " ", flat).strip()
        if flat != val:
            cell.set("value", flat)
            changed = True
    return _serialize(tree) if changed else page_xml


def apply_view_fixers(page_xml: str, *, view: str) -> str:
    """Run the deterministic fixer chain for a focused view page."""
    # Relabel before drop so Kafka/SQS become capabilities instead of vanishing.
    xml = relabel_generic_data_stores(page_xml, view=view)
    xml = relabel_messaging_as_capabilities(xml, view=view)
    xml = drop_forbidden_nodes(xml, view=view)
    xml = strip_cloud_shape_types(xml, view=view)
    if view == "logical":
        xml = ensure_saas_labels_on_logical(xml)
        xml = drop_nonsensical_logical_edges(xml, view=view)
    xml = strip_detail_cards(xml, view=view)
    xml = apply_edge_label_defaults(xml, view=view)
    xml = ensure_edge_label_readability(xml, view=view)
    xml = sanitize_nested_leaf_annotations(xml, view=view)
    from app.adapters.outbound.diagram.xml_diagnostics import sanitize_overview_panel_style
    xml = sanitize_overview_panel_style(xml)
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

    if view not in {"security", "data_flow", "logical"}:
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
