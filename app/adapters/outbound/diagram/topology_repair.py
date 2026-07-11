"""Post-LLM topology repair for AWS reference-architecture conventions."""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
    REPLICATION_SUFFIXES,
    is_az_container,
    is_cloud_like,
    is_edge_service,
    is_entry_actor,
    is_igw,
    is_region_like,
    is_subnet_container,
    is_vpc_like,
    shape_suffix,
    subnet_role,
)
from app.adapters.outbound.diagram.layout import build_children_map
from app.domain.entities import ArchitectureDiagram, Edge, Node


def _find_first(nodes: list[Node], pred) -> Node | None:
    return next((n for n in nodes if pred(n)), None)


def _find_region_or_cloud(nodes: list[Node], vpc: Node | None) -> Node | None:
    nodes_by_id = {n.id: n for n in nodes}
    if vpc and vpc.parent_id and vpc.parent_id in nodes_by_id:
        parent = nodes_by_id[vpc.parent_id]
        if is_region_like(parent) or is_cloud_like(parent):
            return parent
    return _find_first(nodes, is_region_like) or _find_first(nodes, is_cloud_like)


def ensure_igw(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    if any(is_igw(n) for n in architecture.nodes):
        return architecture
    if not any(is_subnet_container(n) and subnet_role(n) == "public" for n in architecture.nodes):
        return architecture
    vpc = _find_first(architecture.nodes, is_vpc_like)
    if not vpc:
        return architecture
    existing = {n.id for n in architecture.nodes}
    igw_id = "aws_internet_gateway" if "aws_internet_gateway" not in existing else f"{vpc.id}_igw"
    igw = Node(id=igw_id, label="Internet Gateway", shape_type="aws_internet_gateway", parent_id=vpc.id)
    return architecture.model_copy(update={"nodes": list(architecture.nodes) + [igw]})


def reparent_entry_actors(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Users / clients always sit on the canvas outside cloud/account containers."""
    updated = [
        n.model_copy(update={"parent_id": None}) if is_entry_actor(n) and n.parent_id else n
        for n in architecture.nodes
    ]
    if updated == list(architecture.nodes):
        return architecture
    return architecture.model_copy(update={"nodes": updated})


def reparent_edge_services(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    vpc = _find_first(architecture.nodes, is_vpc_like)
    host = _find_region_or_cloud(architecture.nodes, vpc)
    if not host:
        return architecture
    nodes_by_id = {n.id: n for n in architecture.nodes}
    updated: list[Node] = []
    changed = False
    for node in architecture.nodes:
        if not is_edge_service(node):
            updated.append(node)
            continue
        parent = nodes_by_id.get(node.parent_id or "")
        if parent and (is_region_like(parent) or is_cloud_like(parent) or node.parent_id == host.id):
            updated.append(node)
            continue
        updated.append(node.model_copy(update={"parent_id": host.id}))
        changed = True
    return architecture if not changed else architecture.model_copy(update={"nodes": updated})


def _mirror_id(source_id: str, from_az: str, to_az: str) -> str:
    if from_az in source_id:
        return source_id.replace(from_az, to_az, 1)
    if source_id.endswith("_a"):
        return source_id[:-2] + "_b"
    return f"{source_id}_b"


def mirror_multi_az(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Clone missing AZ B subnets/services from AZ A; add dashed DB/cache replication."""
    vpc = _find_first(architecture.nodes, is_vpc_like)
    if not vpc:
        return architecture
    children_map, _, nodes_by_id = build_children_map(architecture.nodes)
    azs = sorted(
        [nodes_by_id[c] for c in children_map.get(vpc.id, []) if is_az_container(nodes_by_id[c])],
        key=lambda n: n.id,
    )
    if len(azs) < 2:
        return architecture

    az_a, az_b = azs[0], azs[1]
    a_subnets = [nodes_by_id[c] for c in children_map.get(az_a.id, []) if is_subnet_container(nodes_by_id[c])]
    b_subnets = [nodes_by_id[c] for c in children_map.get(az_b.id, []) if is_subnet_container(nodes_by_id[c])]
    b_roles = {subnet_role(s) for s in b_subnets}

    new_nodes: list[Node] = []
    id_map: dict[str, str] = {}

    for subnet_a in a_subnets:
        role = subnet_role(subnet_a)
        if role in b_roles:
            match = next((s for s in b_subnets if subnet_role(s) == role), None)
            if match:
                id_map[subnet_a.id] = match.id
            continue
        new_id = _mirror_id(subnet_a.id, az_a.id, az_b.id)
        if new_id in nodes_by_id:
            id_map[subnet_a.id] = new_id
            continue
        clone = subnet_a.model_copy(update={"id": new_id, "parent_id": az_b.id})
        new_nodes.append(clone)
        id_map[subnet_a.id] = new_id
        nodes_by_id[new_id] = clone

    for subnet_a in a_subnets:
        target_subnet = id_map.get(subnet_a.id)
        if not target_subnet:
            continue
        existing_shapes = {
            shape_suffix(nodes_by_id[c])
            for c in children_map.get(target_subnet, [])
            if c in nodes_by_id
        }
        for leaf_id in children_map.get(subnet_a.id, []):
            leaf = nodes_by_id.get(leaf_id)
            if not leaf or leaf.is_container or is_entry_actor(leaf):
                continue
            new_leaf_id = _mirror_id(leaf.id, az_a.id, az_b.id)
            if new_leaf_id in nodes_by_id or any(n.id == new_leaf_id for n in new_nodes):
                id_map[leaf.id] = new_leaf_id
                continue
            if shape_suffix(leaf) in existing_shapes:
                continue
            new_nodes.append(leaf.model_copy(update={
                "id": new_leaf_id, "parent_id": target_subnet, "step_number": None,
            }))
            id_map[leaf.id] = new_leaf_id

    if not new_nodes:
        return architecture

    merged = list(architecture.nodes) + new_nodes
    existing_ids = {n.id for n in merged}
    edge_pairs = {(e.source_id, e.target_id) for e in architecture.edges}
    new_edges: list[Edge] = []
    for src_id, dst_id in id_map.items():
        src = next((n for n in architecture.nodes if n.id == src_id), None)
        if not src or src.is_container or shape_suffix(src) not in REPLICATION_SUFFIXES:
            continue
        if dst_id not in existing_ids:
            continue
        if (src_id, dst_id) not in edge_pairs and (dst_id, src_id) not in edge_pairs:
            new_edges.append(Edge(source_id=src_id, target_id=dst_id, style="dashed"))
            edge_pairs.add((src_id, dst_id))

    return architecture.model_copy(update={"nodes": merged, "edges": list(architecture.edges) + new_edges})


def _az_hint(node: Node) -> str | None:
    """Return 'a' / 'b' / … when id or label encodes an AZ, else None."""
    id_l = (node.id or "").lower()
    label_l = (node.label or "").lower()
    for letter in ("a", "b", "c", "d"):
        if (
            id_l.endswith(f"_{letter}")
            or f"_{letter}_" in id_l
            or f"az_{letter}" in id_l
            or id_l.endswith(f"az{letter}")
            or f"az {letter}" in label_l
            or f"zone {letter}" in label_l
            or label_l.endswith(f" {letter}")
        ):
            return letter
    return None


def ensure_az_hierarchy(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Insert AZ containers between VPC and subnets so tiers can stack per zone.

    Rule: subnet must not be a direct VPC child when the diagram is multi-AZ
    (or whenever any subnet hangs directly under the VPC — wrap in AZ A).
    """
    vpc = _find_first(architecture.nodes, is_vpc_like)
    if not vpc:
        return architecture

    children_map, _, nodes_by_id = build_children_map(architecture.nodes)
    vpc_children = children_map.get(vpc.id, [])
    azs = [nodes_by_id[c] for c in vpc_children if is_az_container(nodes_by_id[c])]
    direct_subnets = [nodes_by_id[c] for c in vpc_children if is_subnet_container(nodes_by_id[c])]
    if not direct_subnets:
        return architecture

    existing_ids = {n.id for n in architecture.nodes}
    new_nodes: list[Node] = []
    reparent: dict[str, str] = {}

    def _ensure_az(letter: str) -> str:
        match = next((a for a in azs if _az_hint(a) == letter or a.id.endswith(f"_{letter}")), None)
        if match:
            return match.id
        az_id = f"{vpc.id}_az_{letter}" if f"{vpc.id}_az_{letter}" not in existing_ids else f"az_{letter}"
        if az_id not in existing_ids and not any(n.id == az_id for n in new_nodes):
            label = f"Availability Zone {letter.upper()}"
            new_nodes.append(Node(
                id=az_id, label=label, shape_type="aws_az", is_container=True, parent_id=vpc.id,
            ))
            existing_ids.add(az_id)
        return az_id

    grouped: dict[str, list[Node]] = {}
    for subnet in direct_subnets:
        letter = _az_hint(subnet) or "a"
        grouped.setdefault(letter, []).append(subnet)

    # Multi-AZ intent: distinct _a/_b hints, or already ≥2 AZ containers
    if len(grouped) == 1 and len(azs) < 2:
        letter = next(iter(grouped))
        az_id = _ensure_az(letter)
        for subnet in grouped[letter]:
            reparent[subnet.id] = az_id
    else:
        for letter, subnets in grouped.items():
            az_id = _ensure_az(letter)
            for subnet in subnets:
                reparent[subnet.id] = az_id

    if not reparent and not new_nodes:
        return architecture

    updated = list(architecture.nodes) + new_nodes
    updated = [
        n.model_copy(update={"parent_id": reparent[n.id]}) if n.id in reparent else n
        for n in updated
    ]
    return architecture.model_copy(update={"nodes": updated})


def repair_topology(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    return mirror_multi_az(
        ensure_az_hierarchy(
            reparent_edge_services(reparent_entry_actors(ensure_igw(architecture)))
        )
    )
