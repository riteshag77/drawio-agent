"""Classify diagram nodes and edges for layout, bands, and quality gates."""

from __future__ import annotations

from app.domain.entities import Edge, Node

# shape_type suffix (after aws_/gcp_/azure_) → band id
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

# Inline security hops to strip from primary path (source_suffix, target_suffix)
INLINE_SECURITY_HOPS = (
    ("cloudfront", "shield"),
    ("cloudfront", "shield_advanced"),
    ("shield", "waf"),
    ("shield_advanced", "waf"),
    ("waf", "api_gateway"),
    ("shield", "api_gateway"),
    ("shield_advanced", "api_gateway"),
)

ATTACHED_LABELS = frozenset({"protects", "logs", "reads secrets", "monitors", "audits", "integrates"})


def shape_suffix(node: Node) -> str:
    st = (node.shape_type or "").lower()
    for prefix in ("aws_", "gcp_", "azure_"):
        if st.startswith(prefix):
            return st[len(prefix):]
    return st


def node_text(node: Node) -> str:
    return f"{node.label} {node.shape_type} {node.id}".lower()


def is_entry_actor(node: Node) -> bool:
    if node.is_container or is_igw(node):
        return False
    return any(t in node_text(node) for t in ("user", "client", "users", "external"))


def is_igw(node: Node) -> bool:
    if node.is_container:
        return False
    text = node_text(node)
    return "internet_gateway" in text or "internet gateway" in text or shape_suffix(node) == "internet_gateway"


def is_nat(node: Node) -> bool:
    return "nat" in node_text(node) and "internet" not in node_text(node)


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
    if node.is_container or is_entry_actor(node) or is_igw(node):
        return False
    return band_for_node(node) is None


def matches_suffix(node_id: str, suffix: str, nodes_by_id: dict[str, Node]) -> bool:
    node = nodes_by_id.get(node_id)
    if not node:
        return False
    s = shape_suffix(node)
    return s == suffix or suffix in s or suffix.replace("_", " ") in node_text(node)


def find_node_by_suffix(nodes_by_id: dict[str, Node], suffix: str) -> Node | None:
    for node in nodes_by_id.values():
        if matches_suffix(node.id, suffix, {node.id: node}):
            return node
        if shape_suffix(node) == suffix:
            return node
    return None


def is_attached_edge(edge: Edge) -> bool:
    label = (edge.label or "").strip().lower()
    return edge.style == "dashed" or label in ATTACHED_LABELS


def attached_label_for_band(band: str, target_suffix: str) -> str:
    if band == "security":
        return "protects"
    if band == "logging":
        return "logs"
    if band == "management":
        return "reads secrets" if "secret" in target_suffix else "governs"
    return "integrates"
