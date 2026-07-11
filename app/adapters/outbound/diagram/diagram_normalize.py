"""Normalize architecture JSON: bands, attached edges, flow renumbering."""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
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
from app.domain.entities import ArchitectureDiagram, Edge, FlowStep, Node

_FLOW_SUMMARIES = {
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

_SINGLETON_SUFFIXES = frozenset({"route53", "cloudfront", "api_gateway", "internet_gateway"})


def _edge_key(edge: Edge) -> tuple[str, str, str]:
    return (edge.source_id, edge.target_id, (edge.label or "").strip().lower())


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


def _remove_inline_security_hops(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    kept: list[Edge] = []
    for edge in edges:
        drop = any(
            matches_suffix(edge.source_id, src, nodes_by_id)
            and matches_suffix(edge.target_id, tgt, nodes_by_id)
            and edge.style != "dashed"
            for src, tgt in INLINE_SECURITY_HOPS
        )
        if not drop:
            kept.append(edge)
    return kept


def _ensure_cloudfront_to_api(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    api = find_node_by_suffix(nodes_by_id, "api_gateway")
    if not cf or not api:
        return edges
    if any(e.source_id == cf.id and e.target_id == api.id and e.style != "dashed" for e in edges):
        return edges
    return edges + [Edge(source_id=cf.id, target_id=api.id, label="HTTPS", style="solid")]


def _add_protects_edges(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    if not cf:
        return edges
    existing = {_edge_key(e) for e in edges}
    out = list(edges)
    for node in nodes_by_id.values():
        if band_for_node(node) != "security":
            continue
        if shape_suffix(node) not in ("waf", "shield", "shield_advanced"):
            continue
        candidate = Edge(source_id=node.id, target_id=cf.id, label="protects", style="dashed")
        if _edge_key(candidate) not in existing:
            out.append(candidate)
            existing.add(_edge_key(candidate))
    return out


def _move_band_nodes(nodes: list[Node], keep_empty: bool) -> tuple[list[Node], dict[str, list[str]]]:
    band_members: dict[str, list[str]] = {b: [] for b in BAND_ORDER}
    band_ids = {b: f"band_{b}" for b in BAND_ORDER}
    updated: list[Node] = []

    host_id = None
    for node in nodes:
        text = f"{node.label} {node.shape_type} {node.id}".lower()
        if node.is_container and any(t in text for t in ("cloud", "account", "subscription", "project")):
            host_id = node.id
            break
    if not host_id:
        for node in nodes:
            if node.is_container and "region" in f"{node.label} {node.shape_type}".lower():
                host_id = node.id
                break

    for node in nodes:
        band = band_for_node(node)
        if band:
            band_members[band].append(node.id)
            updated.append(node.model_copy(update={"parent_id": band_ids[band], "step_number": None}))
        elif is_igw(node):
            updated.append(node.model_copy(update={"step_number": None}))
        else:
            updated.append(node)

    for band in BAND_ORDER:
        if not band_members[band] and not keep_empty:
            continue
        label = BAND_LABELS[band]
        if not band_members[band] and keep_empty:
            label = f"{label}\n(not yet in scope)"
        updated.append(Node(
            id=band_ids[band],
            label=label,
            shape_type="aws_generic_group",
            is_container=True,
            parent_id=host_id,
        ))
    return updated, band_members


def _convert_attached_edges(edges: list[Edge], nodes_by_id: dict[str, Node]) -> list[Edge]:
    out: list[Edge] = []
    for edge in edges:
        src, tgt = nodes_by_id.get(edge.source_id), nodes_by_id.get(edge.target_id)
        if not src or not tgt:
            out.append(edge)
            continue
        src_band, tgt_band = band_for_node(src), band_for_node(tgt)
        if src_band or tgt_band:
            band = src_band or tgt_band or "logging"
            label = (edge.label or "").strip() or attached_label_for_band(band, shape_suffix(src))
            out.append(edge.model_copy(update={"style": "dashed", "label": label}))
        else:
            out.append(edge)
    return out


def _add_band_attached_edges(
    edges: list[Edge],
    nodes_by_id: dict[str, Node],
    band_members: dict[str, list[str]],
) -> list[Edge]:
    """One dashed annotation edge per band (avoids fan-out overlap)."""
    cf = find_node_by_suffix(nodes_by_id, "cloudfront")
    fargate = find_node_by_suffix(nodes_by_id, "fargate")
    api = find_node_by_suffix(nodes_by_id, "api_gateway")
    targets = {
        "security": cf,
        "logging": fargate or api,
        "management": fargate or api,
        "integration": api or fargate,
    }
    existing = {_edge_key(e) for e in edges}
    out = list(edges)
    for band, member_ids in band_members.items():
        target = targets.get(band)
        if not target or not member_ids:
            continue
        # Security uses dedicated protects edges from WAF/Shield
        if band == "security":
            continue
        mid = sorted(member_ids)[0]
        node = nodes_by_id.get(mid)
        if not node:
            continue
        candidate = Edge(
            source_id=mid, target_id=target.id,
            label=attached_label_for_band(band, shape_suffix(node)), style="dashed",
        )
        if _edge_key(candidate) not in existing:
            out.append(candidate)
            existing.add(_edge_key(candidate))
    return out


def _renumber_data_path(nodes: list[Node]) -> tuple[list[Node], list[FlowStep]]:
    entry = sorted([n for n in nodes if is_entry_actor(n)], key=lambda n: (n.step_number or 0, n.id))
    candidates = sorted(
        [n for n in nodes if is_data_path_candidate(n) and n.step_number is not None],
        key=lambda n: (n.step_number or 9999, n.id),
    ) or [n for n in nodes if is_data_path_candidate(n)]

    seen: set[str] = set()
    path: list[Node] = list(entry)
    for node in candidates:
        suffix = shape_suffix(node)
        if suffix in seen and suffix in _SINGLETON_SUFFIXES:
            continue
        seen.add(suffix)
        path.append(node)

    renumber: dict[str, int] = {}
    flow_steps: list[FlowStep] = []
    for idx, node in enumerate(path, start=1):
        renumber[node.id] = idx
        title, summary = _FLOW_SUMMARIES.get(shape_suffix(node), (node.label, node.description or ""))
        flow_steps.append(FlowStep(number=idx, title=title, summary=summary))

    updated: list[Node] = []
    for node in nodes:
        if node.id.startswith("band_"):
            updated.append(node)
        elif is_attached_control(node) or is_igw(node):
            updated.append(node.model_copy(update={"step_number": None}))
        elif node.id in renumber:
            updated.append(node.model_copy(update={"step_number": renumber[node.id]}))
        else:
            updated.append(node.model_copy(update={"step_number": None}))
    return updated, flow_steps


def _canonical_singleton_id(nodes: list[Node], suffix: str) -> str:
    """Prefer blueprint-style ids (e.g. aws_api_gateway) then shortest id."""
    scored = sorted(
        nodes,
        key=lambda n: (
            0 if n.id.endswith(suffix) or n.id == f"aws_{suffix}" or n.id == suffix else 1,
            len(n.id),
            n.id,
        ),
    )
    return scored[0].id


def _dedupe_singleton_nodes(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Collapse duplicate singleton leaf shapes (LLM + blueprint different ids)."""
    by_suffix: dict[str, list[Node]] = {}
    for node in architecture.nodes:
        if node.is_container:
            continue
        suffix = shape_suffix(node)
        if suffix in _SINGLETON_SUFFIXES:
            by_suffix.setdefault(suffix, []).append(node)

    id_map: dict[str, str] = {}
    drop: set[str] = set()
    for suffix, group in by_suffix.items():
        if len(group) < 2:
            continue
        keep = _canonical_singleton_id(group, suffix)
        for node in group:
            if node.id != keep:
                id_map[node.id] = keep
                drop.add(node.id)

    if not drop:
        return architecture

    nodes = [n for n in architecture.nodes if n.id not in drop]
    edges: list[Edge] = []
    for edge in architecture.edges:
        src = id_map.get(edge.source_id, edge.source_id)
        tgt = id_map.get(edge.target_id, edge.target_id)
        if src == tgt:
            continue
        edges.append(edge.model_copy(update={"source_id": src, "target_id": tgt}))
    return architecture.model_copy(update={"nodes": nodes, "edges": _dedupe_edges(edges)})


def normalize_architecture(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    # Only create bands that have members — no empty full-width strips
    keep_empty = False
    architecture = _dedupe_singleton_nodes(architecture)
    nodes_by_id = {n.id: n for n in architecture.nodes}

    edges = _dedupe_edges(_add_protects_edges(
        _ensure_cloudfront_to_api(
            _remove_inline_security_hops(list(architecture.edges), nodes_by_id),
            nodes_by_id,
        ),
        nodes_by_id,
    ))

    nodes, band_members = _move_band_nodes(list(architecture.nodes), keep_empty)
    nodes_by_id = {n.id: n for n in nodes}
    edges = _dedupe_edges(_add_band_attached_edges(
        _convert_attached_edges(edges, nodes_by_id), nodes_by_id, band_members,
    ))
    nodes, flow_steps = _renumber_data_path(nodes)
    return architecture.model_copy(update={"nodes": nodes, "edges": edges, "flow_steps": flow_steps})
