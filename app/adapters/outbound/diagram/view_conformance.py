"""Viewpoint conformance scoring for Logical / Security / Data flow pages."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from app.adapters.outbound.diagram.architecture_docs import (
    VIEW_PAGE_NAMES,
    _infer_service_tier,
    _is_inventory_noise_label,
    _plain_cell_label,
)
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document

_ERROR_WEIGHT = 12
_WARN_WEIGHT = 5

_FORBIDDEN_BY_VIEW: dict[str, tuple[str, ...]] = {
    "logical": (
        "waf", "shield", "firewall", "subnet", "vpc", "vnet", "availability zone",
        "az-", "nat gateway", "bastion", "private link",
        "cloudfront", "cdn", "application load balancer", "network load balancer",
        "route 53", "route53", "transit gateway",
        "kafka", "kinesis", "sqs queue", "sns topic", "amazon sqs", "amazon sns",
    ),
    "security": (
        "orders api", "orders capability", "catalog", "checkout", "wireframe",
        "kafka topic", "ui screen", "mobile app", "spa ",
        # Network-plumbing mechanisms belong to Network, not the trust/control story —
        # e.g. a NAT Gateway relaying app→SaaS egress is not itself a security control.
        "nat gateway", "route table", "internet gateway", "subnet",
        "availability zone", "az-",
    ),
    "data_flow": (
        "waf", "shield", "cognito", "entra", "login", "subnet", "vpc", "vnet",
        "availability zone", "authn", "authz", "mfa ", "bastion",
    ),
}

_EDGE_VOCAB: dict[str, tuple[str, ...]] = {
    # Substring match against lowercased labels — keep tokens aligned with
    # config/xml_gen/ir/{logical,security,data_flow}.txt allowed-edge lists.
    "logical": (
        "invokes", "calls", "relies", "reads", "writes", "uses", "api",
        "authenticates", "redirects", "check cache", "cache miss", "fetches",
        "forwards", "validate", "jwt", "login", "token", "return", "query",
        "subscription", "request", "initiates",
    ),
    "security": (
        "authn", "authz", "tls", "mtls", "inspect", "perimeter", "private path",
        "token", "encrypt", "fetches", "credentials", "logs", "telemetry",
        "partner", "federation", "scoped",
    ),
    "data_flow": (
        "json", "sql", "event", "cache", "object", "pub", "sub", "cdc", "batch",
        "avro", "payload", "snapshot", "archive", "schema", "retry", "replication",
    ),
}

_GROUP_HINTS: dict[str, tuple[str, ...]] = {
    "logical": ("domain", "bounded", "capability", "experience", "platform", "business"),
    "security": (
        "trust", "zone", "dmz", "public", "private", "restricted", "identity", "detect",
        "perimeter", "untrusted",
    ),
    "data_flow": (
        "ingest", "process", "storage", "store", "analytics", "hot path", "cold path",
        "lifecycle", "producer", "consumer",
    ),
}


@dataclass
class ViewViolation:
    code: str
    severity: str  # "error" | "warning"
    message: str
    cell_ids: list[str] = field(default_factory=list)


@dataclass
class ViewScore:
    view: str
    score: int
    violations: list[ViewViolation] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return self.score >= int(self.metrics.get("min_score", 75))


def _parse_root(xml: str, title: str = "Page") -> ET.Element | None:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", title))
    except ET.ParseError:
        return None
    return tree


def _iter_cells(root: ET.Element) -> list[ET.Element]:
    return [c for c in root.iter("mxCell") if c.get("id")]


def _is_ir_compiled(root: ET.Element) -> bool:
    """Whether this page came from the IR compiler (``layout="ir"`` stamped
    on the default layer by ``CellWriter.mark_layout_authoritative``), as
    opposed to the direct-XML fallback. Checks gated on this only fire for
    pages that actually stamp the ``classification``/``protection``/
    ``criticality`` attributes they read — the direct-XML path never sets
    them, so applying these checks there would misread "attribute absent"
    as "genuinely unprotected" on every single flow."""
    return any(c.get("id") == "1" and c.get("layout") == "ir" for c in _iter_cells(root))


def _is_container(cell: ET.Element) -> bool:
    style = cell.get("style") or ""
    return "container=1" in style or "swimlane" in style


def _is_annotation(cell: ET.Element) -> bool:
    cid = cell.get("id") or ""
    return cid in {"0", "1", "overview_panel", "legend_box", "cloud_boundary"} or cid.startswith(
        ("step_badge_", "note_", "ctrl_", "sec_", "gap_")
    ) or cell.get("gap") == "1"


def _service_leaves(root: ET.Element) -> list[ET.Element]:
    out: list[ET.Element] = []
    for cell in _iter_cells(root):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        if _is_annotation(cell) or _is_container(cell):
            continue
        label = _plain_cell_label(cell.get("value") or "")
        if _is_inventory_noise_label(label, cell.get("id") or ""):
            continue
        out.append(cell)
    return out


def _leaf_labels(root: ET.Element) -> set[str]:
    labels: set[str] = set()
    for cell in _service_leaves(root):
        lab = _plain_cell_label(cell.get("value") or "").strip().lower()
        if lab:
            labels.add(lab)
    return labels


def _edge_labels(root: ET.Element) -> list[tuple[str, str, str]]:
    """Return (edge_id, label, source_or_target_blob)."""
    by_id = {c.get("id"): c for c in _iter_cells(root)}
    edges: list[tuple[str, str, str]] = []
    for cell in _iter_cells(root):
        if cell.get("edge") != "1":
            continue
        src = by_id.get(cell.get("source") or "")
        tgt = by_id.get(cell.get("target") or "")
        if src is None or tgt is None:
            continue
        if _is_annotation(src) or _is_annotation(tgt):
            continue
        label = _plain_cell_label(cell.get("value") or "")
        edges.append((cell.get("id") or "", label, f"{src.get('value','')}|{tgt.get('value','')}"))
    return edges


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _geom_rect(cell: ET.Element) -> tuple[float, float, float, float] | None:
    geo = cell.find("mxGeometry")
    if geo is None:
        return None
    try:
        return (
            float(geo.get("x") or 0),
            float(geo.get("y") or 0),
            float(geo.get("width") or 0),
            float(geo.get("height") or 0),
        )
    except ValueError:
        return None


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 0.0,
) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + pad <= bx
        or bx + bw + pad <= ax
        or ay + ah + pad <= by
        or by + bh + pad <= ay
    )


def score_view(
    page_xml: str,
    *,
    view: str,
    primary_xml: str = "",
    request_text: str = "",
    density_target: int | None = None,
    min_score: int = 75,
) -> ViewScore:
    """Score a focused view page against viewpoint rules."""
    title = VIEW_PAGE_NAMES.get(view, view)
    root = _parse_root(page_xml, title)
    violations: list[ViewViolation] = []
    metrics: dict[str, Any] = {"min_score": min_score, "view": view}

    if root is None:
        return ViewScore(
            view=view,
            score=0,
            violations=[ViewViolation("parse_error", "error", "Page XML could not be parsed")],
            metrics=metrics,
        )

    leaves = _service_leaves(root)
    leaf_n = len(leaves)
    metrics["leaf_count"] = leaf_n
    target = density_target
    if target is None:
        target = max(5, min(14, 2 + round(leaf_n * 1.5))) if leaf_n else 5
    metrics["density_target"] = target

    # Forbidden nodes
    forbidden = _FORBIDDEN_BY_VIEW.get(view, ())
    for cell in leaves:
        label = _plain_cell_label(cell.get("value") or "").lower()
        cid = cell.get("id") or ""
        if cell.get("gap") == "1" or cid.startswith("gap_"):
            continue
        for token in forbidden:
            if token in label:
                # Logical: allow generic "data store" even if shape hints DB tech in shape_type
                if view == "logical" and "data store" in label:
                    continue
                violations.append(ViewViolation(
                    "forbidden_node",
                    "error",
                    f"Forbidden for {view}: {label!r} matches {token!r}",
                    [cid],
                ))
                break

    # Logical may keep solution-architecture cloud icons (Cognito/Lambda/…).
    # Only flag network-plumbing shape_types that belong on Network/Security.
    _LOGICAL_PLUMBING_SHAPES = (
        "aws_waf", "aws_shield", "aws_vpc", "aws_subnet", "aws_nat", "aws_nat_gateway",
        "aws_internet_gateway", "aws_transit_gateway", "aws_cloudfront", "aws_route53",
        "aws_alb", "aws_application_load_balancer", "aws_nlb", "aws_network_load_balancer",
        "gcp_vpc", "azure_vnet",
    )
    if view == "logical":
        for cell in leaves:
            st = (cell.get("shape_type") or "").strip().lower()
            cid = cell.get("id") or ""
            if st in _LOGICAL_PLUMBING_SHAPES or any(
                st.startswith(p) for p in (
                    "aws_waf", "aws_vpc", "aws_subnet", "aws_nat", "aws_cloudfront",
                    "aws_route53", "aws_alb", "aws_nlb", "aws_transit",
                )
            ):
                violations.append(ViewViolation(
                    "cloud_shape_on_logical",
                    "error",
                    f"Logical leaf {cid!r} has network-plumbing shape_type={st!r}",
                    [cid],
                ))

    # Unlabeled edges + vocab
    vocab = _EDGE_VOCAB.get(view, ())
    edges = _edge_labels(root)
    metrics["edge_count"] = len(edges)
    blank = 0
    vocab_miss = 0
    for eid, label, _ in edges:
        if not (label or "").strip():
            blank += 1
            violations.append(ViewViolation(
                "unlabeled_edge", "error", "Edge has no label", [eid],
            ))
            continue
        low = label.lower()
        if vocab and not any(v in low for v in vocab):
            vocab_miss += 1
            violations.append(ViewViolation(
                "edge_vocab_mismatch",
                "warning",
                f"Edge label {label!r} is outside {view} vocabulary",
                [eid],
            ))
    metrics["blank_edges"] = blank
    metrics["vocab_miss"] = vocab_miss

    # Data flow: request implies PII/PCI but diagram has no classification tags
    if view == "data_flow":
        req = (request_text or "").lower()
        needs_class = any(x in req for x in ("pii", "pci", "personal data", "gdpr", "hipaa"))
        if needs_class:
            # The typed IR path stamps `classification` directly on edges —
            # check that first, before falling back to the label-text search
            # the direct-XML path (which never stamps the attribute) needs.
            tagged = any(
                (c.get("classification") or "") in ("pii", "pci")
                for c in _iter_cells(root) if c.get("edge") == "1"
            )
            if not tagged:
                for _eid, label, _ in edges:
                    low = (label or "").lower()
                    if "[pii]" in low or "[pci]" in low or "[non-pii]" in low or "[confidential]" in low:
                        tagged = True
                        break
            if not tagged:
                for card in _iter_cells(root):
                    cid = card.get("id") or ""
                    if not cid.startswith(("note_", "ctrl_", "overview")):
                        continue
                    low = _plain_cell_label(card.get("value") or "").lower()
                    if any(t in low for t in ("[pii]", "[pci]", "personal data", "non-pii", "non personal")):
                        tagged = True
                        break
            if not tagged:
                violations.append(ViewViolation(
                    "missing_classification",
                    "warning",
                    "Request implies PII/PCI but no [PII]/[PCI]/[Non-PII] tags on edges or cards",
                ))

    # Density
    if leaf_n < max(3, target // 2):
        violations.append(ViewViolation(
            "low_density",
            "warning",
            f"Only {leaf_n} service nodes (target ~{target})",
        ))

    # Group taxonomy (Security / Data-flow keep zone/phase boxes; Logical is flat)
    if view != "logical":
        containers = [
            _plain_cell_label(c.get("value") or "").lower()
            for c in _iter_cells(root)
            if _is_container(c)
        ]
        hints = _GROUP_HINTS.get(view, ())
        if hints and not any(any(h in title for h in hints) for title in containers):
            violations.append(ViewViolation(
                "group_taxonomy",
                "warning",
                f"No {view} grouping containers found (expected domain/zone/phase style titles)",
            ))

    # Detail cards removed — Overview + badges own the narrative
    cards = [
        c for c in _iter_cells(root)
        if (c.get("id") or "").startswith(("note_", "ctrl_"))
    ]
    metrics["card_count"] = len(cards)
    if cards:
        violations.append(ViewViolation(
            "detail_cards_present",
            "warning",
            f"Found {len(cards)} note_/ctrl_ cards — strip; use Overview + badges",
            [c.get("id") or "" for c in cards[:6]],
        ))

    # Logical must not use Security trust-zone wallpaper
    if view == "logical":
        zone_needles = (
            "zone_public", "zone_dmz", "zone_private", "zone_restricted",
            "zone_identity", "zone_detect", "dmz", "highly restricted",
            "trust zone", "untrusted",
        )
        for cell in _iter_cells(root):
            if not _is_container(cell):
                continue
            label = _plain_cell_label(cell.get("value") or "").lower()
            cid = (cell.get("id") or "").lower()
            if any(z in label or z in cid for z in zone_needles):
                violations.append(ViewViolation(
                    "logical_trust_zone",
                    "error",
                    f"Logical page must not use trust zones: {label or cid!r}",
                    [cell.get("id") or ""],
                ))

    # Soft: overview without step badges
    has_overview = any((c.get("id") or "") == "overview_panel" for c in _iter_cells(root))
    badges = [c for c in _iter_cells(root) if (c.get("id") or "").startswith("step_badge_")]
    metrics["badge_count"] = len(badges)
    if has_overview and not badges:
        violations.append(ViewViolation(
            "missing_step_badges",
            "warning",
            "Overview present but no step_badge_* markers — prefer step_markers",
        ))

    # Security: boundary-crossing flow with no stated transport protection.
    # IR-only — `protection` is never stamped on the direct-XML fallback, so
    # applying this there would misread "attribute absent" as "genuinely
    # unprotected" on every single flow rather than just the real gaps.
    if view == "security" and _is_ir_compiled(root):
        by_id = {c.get("id"): c for c in _iter_cells(root)}
        boundary_prefixes = ("zone_public", "zone_partner")
        for cell in _iter_cells(root):
            if cell.get("edge") != "1":
                continue
            src = by_id.get(cell.get("source") or "")
            tgt = by_id.get(cell.get("target") or "")
            src_zone = src.get("parent") or "" if src is not None else ""
            tgt_zone = tgt.get("parent") or "" if tgt is not None else ""
            touches_boundary = src_zone.startswith(boundary_prefixes) or tgt_zone.startswith(boundary_prefixes)
            if touches_boundary and (cell.get("protection") or "none") == "none":
                violations.append(ViewViolation(
                    "unprotected_boundary_flow",
                    "warning",
                    f"Flow {cell.get('id')} crosses a public/partner boundary zone with no "
                    "stated TLS/mTLS protection",
                    [cell.get("id") or ""],
                ))

    # Corridor views: a legend is expected once enough visual conventions are
    # in play — mirrors compiler/legend.py's own row threshold. IR-only, since
    # the direct-XML fallback never emits legend_box at all and this would
    # otherwise fire on every one of its pages.
    if view in ("logical", "security", "data_flow") and _is_ir_compiled(root):
        has_legend = any((c.get("id") or "") == "legend_box" for c in _iter_cells(root))
        if not has_legend:
            edge_styles = [c.get("style") or "" for c in _iter_cells(root) if c.get("edge") == "1"]
            has_primary = any("dashed=1" not in s for s in edge_styles)
            has_secondary = any("dashed=1" in s for s in edge_styles)
            has_shelf = any(
                _is_container(c) and "dashed=1" in (c.get("style") or "") for c in _iter_cells(root)
            )
            expected_rows = sum([has_primary and has_secondary, has_shelf, view == "security"])
            if expected_rows >= 2:
                violations.append(ViewViolation(
                    "missing_legend",
                    "warning",
                    "Page has enough visual conventions in play (primary/secondary path, "
                    "shelf, trust axis) to warrant a legend, but no legend_box cell is present",
                ))

    # Escaped HTML leak (double-escaped): after XML parse, `&amp;lt;` becomes `&lt;`
    for cell in _iter_cells(root):
        raw = cell.get("value") or ""
        if "&lt;" in raw or "&gt;" in raw or "&quot;" in raw:
            # Legitimate overview HTML is stored decoded as real <div> after parse,
            # or as already-escaped entities only in the *file* text. Seeing literal
            # &lt; in the attribute value means it was double-escaped in the source.
            if cell.get("id") == "overview_panel" or (cell.get("id") or "").startswith(("note_", "ctrl_")):
                violations.append(ViewViolation(
                    "escaped_html_leak",
                    "error",
                    "Double-escaped HTML entities in cell value",
                    [cell.get("id") or ""],
                ))

    # Card / overview overlap (coarse, parent="1" absolute-ish)
    overview = next((c for c in _iter_cells(root) if c.get("id") == "overview_panel"), None)
    ov_rect = _geom_rect(overview) if overview is not None else None
    for card in cards:
        cr = _geom_rect(card)
        if cr is None:
            continue
        if ov_rect and _rects_overlap(cr, ov_rect, pad=4):
            violations.append(ViewViolation(
                "overview_overlap",
                "warning",
                "Detail card overlaps overview_panel",
                [card.get("id") or ""],
            ))
        for other in leaves:
            lr = _geom_rect(other)
            if lr and _rects_overlap(cr, lr, pad=2):
                violations.append(ViewViolation(
                    "card_overlap",
                    "warning",
                    "Detail card overlaps a service leaf",
                    [card.get("id") or "", other.get("id") or ""],
                ))
                break

    # Network clone (label Jaccard)
    if primary_xml:
        primary_root = _parse_root(primary_xml, "Network")
        if primary_root is not None:
            page_labs = _leaf_labels(root)
            prim_labs = _leaf_labels(primary_root)
            sim = _jaccard(page_labs, prim_labs)
            metrics["label_jaccard"] = round(sim, 3)
            page_edge_labs = {lab.lower() for _, lab, _ in edges if lab.strip()}
            prim_edges = _edge_labels(primary_root)
            prim_edge_labs = {lab.lower() for _, lab, _ in prim_edges if lab.strip()}
            edge_sim = _jaccard(page_edge_labs, prim_edge_labs) if page_edge_labs or prim_edge_labs else 0.0
            metrics["edge_jaccard"] = round(edge_sim, 3)
            if sim >= 0.75 and edge_sim >= 0.5 and leaf_n >= 3:
                violations.append(ViewViolation(
                    "network_clone",
                    "error",
                    f"Page looks like a Network clone (label Jaccard={sim:.2f})",
                ))

    # MCP miss count (grey placeholders still ok)
    mcp_miss = 0
    for cell in leaves:
        if cell.get("gap") == "1":
            continue
        st = cell.get("shape_type") or ""
        style = cell.get("style") or ""
        if not st and "shape=ellipse" in style:
            mcp_miss += 1
    metrics["mcp_miss"] = mcp_miss

    # Score
    score = 100
    for v in violations:
        score -= _ERROR_WEIGHT if v.severity == "error" else _WARN_WEIGHT
    score = max(0, min(100, score))
    _ = request_text, _infer_service_tier, html  # kept for future brief-aware scoring
    return ViewScore(view=view, score=score, violations=violations, metrics=metrics)


def format_violations_for_repair(score: ViewScore) -> str:
    """Turn violations into reviewer instructions for the repair LLM."""
    if not score.violations:
        return "No violations."
    lines = [
        f"REVIEWER ({score.view}): score={score.score}. Fix ONLY the issues below.",
        "Do not invent products absent from inventory. Preserve shape_type on real services.",
    ]
    for i, v in enumerate(score.violations, 1):
        ids = f" (cells: {', '.join(v.cell_ids)})" if v.cell_ids else ""
        lines.append(f"{i}. [{v.severity}/{v.code}] {v.message}{ids}")
    return "\n".join(lines)
