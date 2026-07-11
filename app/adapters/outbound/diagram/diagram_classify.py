"""Single source of truth for node/edge classification used by layout, normalize, and repair."""

from __future__ import annotations

from app.domain.entities import Edge, Node

BAND_SECURITY = (
    "waf", "shield", "shield_advanced", "secrets_manager", "iam", "kms", "guardduty",
    "certificate_manager", "cognito", "cloudhsm",
)
BAND_LOGGING = ("cloudwatch", "cloudtrail", "config", "xray", "opensearch")
BAND_MANAGEMENT = (
    "systems_manager", "cloudformation", "organizations", "control_tower",
    "trusted_advisor", "service_catalog",
)
BAND_INTEGRATION = ("eventbridge", "sns", "sqs", "step_functions", "kinesis", "msk", "appsync")

BAND_ORDER = ("security", "logging", "management", "integration")
BAND_LABELS = {
    "security": "Security Services",
    "logging": "Logging & Monitoring",
    "management": "Management & Governance",
    "integration": "Integration Services",
}
BAND_COLORS = {
    "security": ("#DD344C", "#FFF5F5"),
    "logging": ("#E7157B", "#FFF0F6"),
    "management": ("#545B64", "#F4F4F4"),
    "integration": ("#8C4FFF", "#F5F0FF"),
}

INLINE_SECURITY_HOPS = (
    ("cloudfront", "shield"),
    ("cloudfront", "shield_advanced"),
    ("shield", "waf"),
    ("shield_advanced", "waf"),
    ("waf", "api_gateway"),
    ("shield", "api_gateway"),
    ("shield_advanced", "api_gateway"),
)

ATTACHED_LABELS = frozenset({"protects", "logs", "reads secrets", "monitors", "audits", "integrates", "governs"})
EDGE_SERVICE_SUFFIXES = frozenset({"route53", "cloudfront", "cloud_dns", "cdn", "front_door", "dns"})
REPLICATION_SUFFIXES = frozenset({
    "aurora", "rds", "elasticache", "dynamodb", "cloud_sql", "memorystore", "sql_db", "cache_redis",
})


def shape_suffix(node: Node) -> str:
    st = (node.shape_type or "").lower()
    for prefix in ("aws_", "gcp_", "azure_"):
        if st.startswith(prefix):
            return st[len(prefix):]
    return st


def node_text(node: Node) -> str:
    return f"{node.label} {node.shape_type} {node.id}".lower()


def is_band_container(node: Node) -> bool:
    return bool(getattr(node, "id", "").startswith("band_"))


def is_igw(node: Node) -> bool:
    if node.is_container:
        return False
    text = node_text(node)
    return "internet_gateway" in text or "internet gateway" in text or shape_suffix(node) == "internet_gateway"


def is_entry_actor(node: Node) -> bool:
    if node.is_container or is_igw(node):
        return False
    return any(t in node_text(node) for t in ("user", "client", "users", "external"))


def is_az_container(node: Node) -> bool:
    if not node.is_container:
        return False
    text = f" {node_text(node)} "
    return (
        "availability zone" in text
        or "_az" in text
        or " az " in text
        or " az_" in text
        or " zone a" in text
        or " zone b" in text
        or "gcp_zone" in text
    )


def is_subnet_container(node: Node) -> bool:
    return node.is_container and "subnet" in node_text(node)


def is_vpc_like(node: Node) -> bool:
    return node.is_container and any(t in node_text(node) for t in ("vpc", "vnet", "virtual network"))


def is_region_like(node: Node) -> bool:
    return node.is_container and "region" in node_text(node)


def is_cloud_like(node: Node) -> bool:
    return node.is_container and any(t in node_text(node) for t in ("cloud", "account", "subscription", "project"))


def subnet_role(node: Node) -> str:
    """public | data | private — used for AZ mirroring and scaffolds."""
    text = node_text(node)
    if any(w in text for w in ("public", "ingress", "dmz", "frontend", "edge")):
        return "public"
    if any(w in text for w in ("data", "database", "db", "storage", "persistence")):
        return "data"
    return "private"


def node_tier(node: Node) -> int:
    """0=public, 1=private/app, 2=data, 3=ops — for layout sort order."""
    role = subnet_role(node) if node.is_container or "subnet" in node_text(node) else None
    if role == "public":
        return 0
    if role == "data":
        return 2
    if role == "private":
        return 1
    text = f"{node.label} {node.shape_type}".lower()
    if "public" in text:
        return 0
    if "private" in text:
        return 1
    if any(w in text for w in ("monitoring", "logging", "security", "ops", "compliance")):
        return 3
    return 2


def scaffold_role(node: Node) -> str | None:
    """Container size role for template_merge: cloud|vpc|az|public|private."""
    if not node.is_container:
        return None
    text = f" {node_text(node)} "
    if any(t in text for t in ("cloud", "account", "subscription")):
        return "cloud"
    if any(t in text for t in ("vpc", "vnet", "project")):
        return "vpc"
    if is_az_container(node):
        return "az"
    if any(t in text for t in ("public", "ingress", "dmz")):
        return "public"
    if any(t in text for t in ("private", "data", "compute", "app")):
        return "private"
    return None


def band_for_node(node: Node) -> str | None:
    if node.is_container:
        return None
    suffix = shape_suffix(node)
    if suffix in BAND_SECURITY:
        return "security"
    if suffix in BAND_LOGGING:
        return "logging"
    if suffix in BAND_MANAGEMENT:
        return "management"
    if suffix in BAND_INTEGRATION:
        return "integration"
    text = node_text(node)
    if any(w in text for w in ("monitoring", "logging", "cloudwatch", "cloudtrail")):
        return "logging"
    if any(w in text for w in ("security", "waf", "shield", "guardduty")):
        return "security"
    return None


def is_attached_control(node: Node) -> bool:
    return band_for_node(node) is not None


def is_data_path_candidate(node: Node) -> bool:
    return not (node.is_container or is_entry_actor(node) or is_igw(node) or is_attached_control(node))


def is_edge_service(node: Node) -> bool:
    if node.is_container:
        return False
    suffix = shape_suffix(node)
    return suffix in EDGE_SERVICE_SUFFIXES or any(s in node_text(node) for s in ("route 53", "cloudfront"))


def is_attached_edge(edge: Edge) -> bool:
    label = (edge.label or "").strip().lower()
    return edge.style == "dashed" or label in ATTACHED_LABELS


def matches_suffix(node_id: str, suffix: str, nodes_by_id: dict[str, Node]) -> bool:
    node = nodes_by_id.get(node_id)
    if not node:
        return False
    s = shape_suffix(node)
    return s == suffix or suffix in s or suffix.replace("_", " ") in node_text(node)


def find_node_by_suffix(nodes_by_id: dict[str, Node], suffix: str) -> Node | None:
    for node in nodes_by_id.values():
        if shape_suffix(node) == suffix or matches_suffix(node.id, suffix, nodes_by_id):
            return node
    return None


def attached_label_for_band(band: str, target_suffix: str) -> str:
    if band == "security":
        return "protects"
    if band == "logging":
        return "logs"
    if band == "management":
        return "reads secrets" if "secret" in target_suffix else "governs"
    return "integrates"
