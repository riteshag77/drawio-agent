"""Normalize architecture JSON: bands, attached edges, flow renumbering."""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
    ATTACHED_LABELS,
    BAND_LABELS,
    BAND_ORDER,
    INLINE_SECURITY_HOPS,
    attached_label_for_band,
    band_for_node,
    find_node_by_suffix,
    is_attached_control,
    is_data_path_candidate,
    is_entry_actor,
    is_igw,
    matches_suffix,
    shape_suffix,
)
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.domain.entities import ArchitectureDiagram, Edge, FlowStep, Node


def _edge_key(edge: Edge) -> tuple[str, str, str]:
    return (edge.source_id, edge.target_id, (edge.label or "").strip().lower())


def _remove_inline_security_hops(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    kept: list[Edge] = []
    for edge in edges:
        drop = False
        for src_s, tgt_s in INLINE_SECURITY_HOPS:
            if matches_suffix(edge.source_id, src_s, nodes_by_id) and matches_suffix(edge.target_id, tgt_s, nodes_by_id):
                if edge.style != "dashed":
                    drop = True
                    break
        if not drop:
            kept.append(edge)
    return kept


def _ensure_cloudfront_to_api(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    api = find_node_by_suffix(nodes_by_id, "api_gateway")
    if not cf or not api:
        return edges
    has_direct = any(
        e.source_id == cf.id and e.target_id == api.id and e.style != "dashed"
        for e in edges
    )
    if has_direct:
        return edges
    return edges + [Edge(source_id=cf.id, target_id=api.id, label="HTTPS", style="solid")]


def _add_protects_edges(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    if not cf:
        return edges
    existing = {_edge_key(e) for e in edges}
    out = list(edges)
    for node in nodes_by_id.values():
        band = band_for_node(node)
        if band != "security":
            continue
        suffix = shape_suffix(node)
        if suffix not in ("waf", "shield", "shield_advanced"):
            continue
        candidate = Edge(source_id=node.id, target_id=cf.id, label="protects", style="dashed")
        if _edge_key(candidate) not in existing:
            out.append(candidate)
            existing.add(_edge_key(candidate))
    return out


def _move_band_nodes(nodes: list[Node], keep_empty: bool) -> tuple[list[Node], dict[str, list[str]]]:
    """Re-parent band services to band containers; return nodes + band membership."""
    nodes_by_id = {n.id: n for n in nodes}
    band_members: dict[str, list[str]] = {b: [] for b in BAND_ORDER}
    band_ids = {b: f"band_{b}" for b in BAND_ORDER}

    for node in nodes:
        band = band_for_node(node)
        if band:
            band_members[band].append(node.id)

    updated: list[Node] = []
    seen_band_containers: set[str] = set()

    for node in nodes:
        band = band_for_node(node)
        if band:
            updated.append(node.model_copy(update={"parent_id": band_ids[band], "step_number": None}))
            continue
        if is_igw(node):
            updated.append(node.model_copy(update={"step_number": None}))
            continue
        updated.append(node)

    for band in BAND_ORDER:
        members = band_members[band]
        if not members and not keep_empty:
            continue
        if band_ids[band] in seen_band_containers:
            continue
        seen_band_containers.add(band_ids[band])
        label = BAND_LABELS[band]
        if not members and keep_empty:
            label = f"{label}\n(not yet in scope)"
        updated.append(
            Node(
                id=band_ids[band],
                label=label,
                shape_type="aws_generic_group",
                is_container=True,
                parent_id=None,
            )
        )

    return updated, band_members


def _renumber_data_path(nodes: list[Node], edges: list[Edge]) -> tuple[list[Node], list[FlowStep]]:
    entry_nodes = sorted(
        [n for n in nodes if is_entry_actor(n)],
        key=lambda n: (n.step_number or 0, n.id),
    )
    candidates = sorted(
        [n for n in nodes if is_data_path_candidate(n) and n.step_number is not None],
        key=lambda n: (n.step_number or 9999, n.id),
    )
    if not candidates:
        candidates = [n for n in nodes if is_data_path_candidate(n)]

    seen_suffix: set[str] = set()
    path_nodes: list[Node] = list(entry_nodes)
    for node in candidates:
        suffix = shape_suffix(node)
        if suffix in seen_suffix and suffix in (
            "route53", "cloudfront", "api_gateway", "internet_gateway",
        ):
            continue
        seen_suffix.add(suffix)
        path_nodes.append(node)

    flow_steps: list[FlowStep] = []
    updated_nodes: list[Node] = []
    renumber_map: dict[str, int] = {}

    default_summaries = {
        "client": ("User Request", "Users initiate a request to the web application."),
        "user_client": ("User Request", "Users initiate a request to the web application."),
        "route53": ("DNS Resolution", "Route 53 resolves the domain name to CloudFront's distribution."),
        "cloudfront": (
            "Content Delivery",
            "CloudFront caches static content and routes dynamic requests to the origin. "
            "Shield Advanced and WAF protect the distribution (see Security Services band).",
        ),
        "api_gateway": (
            "API Entry Point",
            "API Gateway acts as the front door, handling routing, authentication, and throttling.",
        ),
        "alb": ("Load Balancing", "Application Load Balancer distributes traffic to Fargate tasks."),
        "application_load_balancer": ("Load Balancing", "Application Load Balancer distributes traffic to Fargate tasks."),
        "fargate": ("Stateless Compute", "Fargate containers process application logic, scaling on demand."),
        "elasticache": ("In-Memory Cache", "ElastiCache provides a high-speed cache to reduce database load."),
        "aurora": ("Managed Database", "Aurora stores and retrieves persistent application data."),
        "rds": ("Managed Database", "RDS stores and retrieves persistent application data."),
    }

    for idx, node in enumerate(path_nodes, start=1):
        renumber_map[node.id] = idx
        suffix = shape_suffix(node)
        title, summary = default_summaries.get(suffix, (node.label, node.description or ""))
        flow_steps.append(FlowStep(number=idx, title=title, summary=summary))

    for node in nodes:
        if node.id.startswith("band_"):
            updated_nodes.append(node)
            continue
        if is_attached_control(node) or is_igw(node):
            updated_nodes.append(node.model_copy(update={"step_number": None}))
            continue
        if node.id in renumber_map:
            updated_nodes.append(node.model_copy(update={"step_number": renumber_map[node.id]}))
        else:
            updated_nodes.append(node.model_copy(update={"step_number": None}))

    return updated_nodes, flow_steps


def _convert_attached_edges(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    out: list[Edge] = []
    for edge in edges:
        src = nodes_by_id.get(edge.source_id)
        tgt = nodes_by_id.get(edge.target_id)
        if not src or not tgt:
            out.append(edge)
            continue
        src_band = band_for_node(src)
        tgt_band = band_for_node(tgt)
        if src_band or tgt_band:
            band = src_band or tgt_band or "logging"
            label = (edge.label or "").strip() or attached_label_for_band(band, shape_suffix(src or tgt))
            out.append(edge.model_copy(update={"style": "dashed", "label": label}))
            continue
        out.append(edge)
    return out


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Edge] = []
    for edge in edges:
        key = _edge_key(edge)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return out


def _add_band_attached_edges(
    edges: list[Edge],
    nodes_by_id: dict[str, Node],
    band_members: dict[str, list[str]],
) -> list[Edge]:
    """Add dashed links from band services to their primary-path targets."""
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    fargate = find_node_by_suffix(nodes_by_id, "fargate")
    api = find_node_by_suffix(nodes_by_id, "api_gateway")
    existing = {_edge_key(e) for e in edges}
    out = list(edges)

    targets_by_band = {
        "security": cf,
        "logging": fargate or api,
        "management": fargate or api,
        "integration": api or fargate,
    }

    for band, member_ids in band_members.items():
        target = targets_by_band.get(band)
        if not target:
            continue
        for mid in member_ids:
            node = nodes_by_id.get(mid)
            if not node:
                continue
            suffix = shape_suffix(node)
            if band == "security" and suffix in ("waf", "shield", "shield_advanced"):
                continue  # handled by _add_protects_edges
            label = attached_label_for_band(band, suffix)
            candidate = Edge(source_id=mid, target_id=target.id, label=label, style="dashed")
            if _edge_key(candidate) not in existing:
                out.append(candidate)
                existing.add(_edge_key(candidate))
    return out


def normalize_architecture(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Apply band placement, edge fixes, and flow renumbering before layout."""
    settings = layout_settings()
    keep_empty = settings.keep_empty_bands_default
    if architecture.pattern_id:
        keep_empty = True

    nodes_by_id = {n.id: n for n in architecture.nodes}
    edges = _remove_inline_security_hops(list(architecture.edges), nodes_by_id)
    edges = _ensure_cloudfront_to_api(edges, nodes_by_id)
    edges = _add_protects_edges(edges, nodes_by_id)
    edges = _dedupe_edges(edges)

    nodes, band_members = _move_band_nodes(list(architecture.nodes), keep_empty)
    nodes_by_id = {n.id: n for n in nodes}
    edges = _convert_attached_edges(edges, nodes_by_id)
    edges = _add_band_attached_edges(edges, nodes_by_id, band_members)
    edges = _dedupe_edges(edges)

    nodes, flow_steps = _renumber_data_path(nodes, edges)

    return architecture.model_copy(update={"nodes": nodes, "edges": edges, "flow_steps": flow_steps})
