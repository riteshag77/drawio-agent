"""Multi-page architecture documentation helpers (views, ADR, RAID)."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from app.adapters.outbound.config.env_config import EnvConfigAdapter
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document, wrap_with_mxfile
from app.domain.entities.xml_diagram import StepMarker

_VIEW_ORDER = ("logical", "network", "security", "data_flow")
VIEW_PAGE_NAMES = {
    "logical": "Logical",
    "network": "Network",
    "security": "Security",
    "data_flow": "Data flow",
}
_VIEW_PAGE_NAMES = VIEW_PAGE_NAMES  # backward-compatible alias

_VIEW_PACK_FILES = {
    "logical": "views/logical.txt",
    "security": "views/security.txt",
    "data_flow": "views/data_flow.txt",
}
_DOCS_PACK_FILE = "docs/adr_raid.txt"
_VIEW_COMMON_FILE = "views/common.txt"
_CORE_PACK_FILE = "core.txt"
_CONFIG_DIR = EnvConfigAdapter.base_dir() / "config" / "xml_gen"
_MAX_ADRS = 15  # soft safety against runaway model dumps
_MAX_RAID = 24
_SERVICE_INVENTORY_LIMIT = 36
_EDGE_INVENTORY_LIMIT = 20
_REQUEST_TRUNCATE_VIEW = 1000
_REQUEST_TRUNCATE_DOCS = 600

INTERVIEW_QUESTIONS = [
    {
        "id": "regions_envs",
        "prompt": "Which region(s) and environments (prod/non-prod) should this cover?",
    },
    {
        "id": "trust_boundaries",
        "prompt": "What trust boundaries matter (public internet, VPC, accounts, on-prem)?",
    },
    {
        "id": "traffic_pattern",
        "prompt": "What is the primary traffic / data path (request flow or batch/event)?",
    },
    {
        "id": "slos_nongoals",
        "prompt": "Any SLOs, compliance constraints, or explicit non-goals?",
    },
    {
        "id": "data_classification",
        "prompt": "What data sensitivity / classification applies (public, PII, PCI, etc.)?",
    },
]


def normalize_diagram_views(views: list[str] | None) -> list[str]:
    allowed = set(_VIEW_ORDER)
    cleaned = [v.strip().lower() for v in (views or []) if isinstance(v, str) and v.strip()]
    selected = [v for v in _VIEW_ORDER if v in cleaned and v in allowed]
    return selected or ["network"]


def build_interview_prompt_block(answers: dict[str, str] | None) -> str:
    if not answers:
        return ""
    lines = ["Architecture interview answers (treat as authoritative requirements):"]
    for key, val in answers.items():
        text = (val or "").strip()
        if text:
            lines.append(f"- {key}: {text}")
    return "\n".join(lines) if len(lines) > 1 else ""


def pick_primary_view(views: list[str] | None) -> str:
    """Prefer Network as the canonical architecture page whenever it is selected.

    Logical/Security/Data flow are derived focus pages; generating Logical first
    left Network/Security/Data flow empty when the LLM only filled one page.
    """
    selected = normalize_diagram_views(views)
    if "network" in selected:
        return "network"
    return selected[0]


def build_views_prompt_block(views: list[str], *, adr_notes: bool = False, raid_register: bool = False) -> str:
    """Token-light focus for the *primary* generate only.

    Extra views / ADR / RAID are handled in separate pipeline passes — do not
    ask the main diagram call to emit multipage XML or documentation pages.
    ``adr_notes`` / ``raid_register`` are accepted for API compatibility but
    intentionally ignored here (saves tokens on the expensive XML call).
    """
    del adr_notes, raid_register  # docs are a separate pass
    views = normalize_diagram_views(views)
    primary = pick_primary_view(views)
    # Default Network-only: no extra instruction — CORE prompt already describes network hierarchy.
    if views == ["network"] and primary == "network":
        return ""
    focus = {
        "network": (
            "Focus this page on the Network / VPC view: full Cloud→Region→VPC→Zone→Subnet "
            "hierarchy with connectivity hubs at Cloud level."
        ),
        "logical": (
            "Focus this page on the Logical view: services/components and relationships; "
            "simplify deep VPC/subnet nesting — show clear tiers without every subnet box."
        ),
        "security": (
            "Focus this page on the Security / trust view: trust boundaries, IAM/edge "
            "controls, private endpoints, secrets — de-emphasize data-path detail."
        ),
        "data_flow": (
            "Focus this page on the Data flow view: labeled request/data path edges "
            "(protocol / data type); keep topology sparse."
        ),
    }
    lines = [
        f"Emit a SINGLE draw.io page named `{VIEW_PAGE_NAMES[primary]}` only "
        f"(do not emit other view pages or ADR/RAID in this response).",
        focus[primary],
        f"Overview panel REQUIRED on this `{VIEW_PAGE_NAMES[primary]}` page "
        f"(id=overview_panel): title names this view; 4–6 short layman steps "
        f"(~100 characters each) describing what *this* page shows.",
    ]
    return "\n".join(lines)


def load_docs_prompt_pack() -> str:
    path = _CONFIG_DIR / _DOCS_PACK_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "Write ADR and RAID for business readers. Include every material decision. "
        "RAID types: Risk, Assumption, Issue, Dependency."
    )


def load_core_pack() -> str:
    path = _CONFIG_DIR / _CORE_PACK_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def load_view_common_pack() -> str:
    path = _CONFIG_DIR / _VIEW_COMMON_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "Produce one briefing-quality draw.io page. Reuse inventory services. "
        "Require overview_panel with 4–6 layman steps. Use shape_type placeholders."
    )


def load_view_prompt_pack(view: str) -> str:
    filename = _VIEW_PACK_FILES.get(view)
    if not filename:
        return ""
    path = _CONFIG_DIR / filename
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


class SingleViewPage(BaseModel):
    """One focused architecture view page (Logical / Security / Data flow)."""

    xml: str = Field(description="Complete mxfile or mxGraphModel for this view page only")
    step_markers: list[StepMarker] = Field(
        default_factory=list,
        description=(
            "Numbered badges matching Overview Panel steps — each entry's cell_id must be an "
            "mxCell id already present in this page's xml (same contract as Network)."
        ),
    )


# Kept for backward-compatible imports in tests / older call sites.
class ExtraViewPage(BaseModel):
    view: str
    xml: str = Field(description="Complete mxfile or mxGraphModel for this view page only")


class ExtraViewsPack(BaseModel):
    pages: list[ExtraViewPage] = Field(default_factory=list)


_SINGLE_VIEW_SYSTEM_PREFIX = """You are producing ONE product-grade architecture briefing page as draw.io XML.
Adopt the specialised architect persona named in the view pack below (25+ years in that discipline).
The Network tab already shows VPC/subnet placement — this page must answer a DIFFERENT question for that persona's audience.
Reuse inventory services (same shape_type keys). Do not invent unrelated cloud products.
Emit one mxfile with one <diagram name="..."> matching the required page title.
REQUIRED: overview_panel (numbered amber steps) + step_markers + official shape_type on EVERY service leaf.
No ADR/RAID. No commentary outside xml + step_markers.
"""


def build_single_view_system(view: str) -> str:
    core = load_core_pack()
    common = load_view_common_pack()
    pack = load_view_prompt_pack(view)
    return f"{_SINGLE_VIEW_SYSTEM_PREFIX}\n\n{core}\n\n{common}\n\n{pack}".strip()


def _plain_cell_label(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", value or "")
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def _is_inventory_noise_label(label: str, cell_id: str) -> bool:
    """Drop badges, overview, and digit-only noise from inventory lists."""
    cid = (cell_id or "").strip().lower()
    if cid in {"overview_panel", "legend_box", "0", "1"} or cid.startswith("step_badge_"):
        return True
    text = (label or "").strip()
    if not text:
        return True
    if text.isdigit():
        return True
    if text.lower() in {"service", "icon", "node", "group", "background"}:
        return True
    return False


def _infer_service_tier(label: str, shape_type: str, parent_label: str) -> str:
    blob = f"{label} {shape_type} {parent_label}".lower()
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("edge", (
            "alb", "nlb", "elb", "load balancer", "api gateway", "apigateway",
            "cloudfront", "cdn", "waf", "front door", "application gateway",
            "traffic manager", "dns", "route 53", "route53",
        )),
        ("connectivity", (
            "transit gateway", "tgw", "vpn", "direct connect", "privatelink",
            "private link", "expressroute", "interconnect", "peering",
        )),
        ("security", (
            "iam", "cognito", "kms", "secrets", "key vault", "guardduty",
            "shield", "inspector", "security hub", "entra", "active directory",
            "certificate", "acm",
        )),
        ("data", (
            "rds", "aurora", "dynamodb", "cosmos", "sql", "postgres", "mysql",
            "redis", "elasticache", "memorystore", "s3", "blob", "bucket",
            "opensearch", "elasticsearch", "redshift", "bigquery", "synapse",
            "firestore", "mongo", "neptune",
        )),
        ("integration", (
            "sqs", "sns", "eventbridge", "event hub", "service bus", "pubsub",
            "kafka", "kinesis", "step functions", "logic app", "data factory",
            "glue", "queue", "topic", "bus",
        )),
        ("app", (
            "ecs", "eks", "lambda", "ec2", "fargate", "aks", "aci", "functions",
            "app service", "cloud run", "gke", "beanstalk", "compute", "api",
            "service", "worker", "bff",
        )),
        ("observability", (
            "cloudwatch", "x-ray", "prometheus", "grafana", "datadog", "monitor",
            "logging", "log analytics",
        )),
    ]
    for tier, needles in rules:
        if any(n in blob for n in needles):
            return tier
    parent = parent_label.lower()
    if any(x in parent for x in ("public", "edge", "dmz")):
        return "edge"
    if any(x in parent for x in ("data", "db", "storage")):
        return "data"
    if any(x in parent for x in ("app", "private", "compute")):
        return "app"
    if "security" in parent:
        return "security"
    return "other"


def _infer_control_hints(request_text: str, service_labels: list[str]) -> list[str]:
    """Derive security/control talking points from request + inventory (no new products)."""
    blob = f"{request_text} {' '.join(service_labels)}".lower()
    hints: list[str] = []
    checks: list[tuple[tuple[str, ...], str]] = [
        (("waf",), "Edge filtering via WAF (block common web attacks before origin)"),
        (("cloudfront", "cdn", "front door"), "TLS termination / static cache at CDN edge"),
        (("alb", "load balancer", "nlb"), "TLS to load balancer; only LB reaches private app tier"),
        (("private subnet", "private subnets"), "App/data in private subnets (no direct internet)"),
        (("multi-az", "multiple az", "multi az", "availability zone"), "Multi-AZ placement for failure isolation"),
        (("rds", "aurora", "database", "sql"), "Managed database; prefer encryption at rest + private access"),
        (("read replica", "read replicas"), "Read replicas for scale-out reads (protect primary)"),
        (("elasticache", "redis", "cache", "memorystore"), "Cache layer reduces DB exposure / load"),
        (("iam", "cognito", "entra", "identity"), "Identity / IAM as control plane for who can call what"),
        (("kms", "key vault", "secrets manager", "secrets"), "Secrets/keys managed — not baked into images"),
        (("cloudwatch", "monitor", "logging", "guardduty", "security hub"), "Monitoring / detection for abuse and outages"),
        (("vpn", "direct connect", "privatelink", "private link"), "Private connectivity path (not public internet)"),
        (("shield",), "DDoS protection at edge (Shield)"),
    ]
    for needles, hint in checks:
        if any(n in blob for n in needles):
            hints.append(hint)
    return hints[:10]


def _infer_capability_hints(service_labels: list[str]) -> list[str]:
    """Map inventory services to business capabilities for Logical view."""
    mapping: list[tuple[tuple[str, ...], str]] = [
        (("route 53", "route53", "dns"), "DNS routing / health-based entry"),
        (("waf",), "Edge threat filter"),
        (("cloudfront", "cdn", "front door"), "Global content delivery / static assets"),
        (("alb", "nlb", "load balancer"), "Traffic distribution into the app"),
        (("ec2", "ecs", "eks", "lambda", "app service", "cloud run"), "Application / business logic runtime"),
        (("elasticache", "redis", "memorystore"), "Low-latency session or data cache"),
        (("rds", "aurora", "dynamodb", "sql", "postgres"), "System of record / primary data store"),
        (("s3", "blob", "bucket"), "Object / file storage"),
        (("cloudwatch", "monitor"), "Health, metrics, and alarms"),
        (("sqs", "sns", "eventbridge", "kafka", "pubsub"), "Async integration / events"),
    ]
    lower = [s.lower() for s in service_labels]
    out: list[str] = []
    for needles, cap in mapping:
        if any(any(n in lab for n in needles) for lab in lower):
            out.append(cap)
    return out[:12]


def _infer_payload_hints(service_labels: list[str]) -> list[str]:
    """Suggested edge payload labels for Data flow view."""
    labels = " ".join(service_labels).lower()
    hints = ["HTTPS request", "DNS lookup"]
    if "cloudfront" in labels or "cdn" in labels:
        hints.append("Cached static asset")
    if "alb" in labels or "load" in labels:
        hints.append("Load-balanced app request")
    if "elasticache" in labels or "redis" in labels or "cache" in labels:
        hints.append("Cache get/set")
    if "rds" in labels or "sql" in labels or "aurora" in labels:
        hints.append("SQL read/write")
        if "replica" in labels or "read" in labels:
            hints.append("Read-replica query")
    if "s3" in labels or "blob" in labels:
        hints.append("Object get/put")
    if any(x in labels for x in ("sqs", "sns", "event", "kafka")):
        hints.append("Async event")
    return hints[:10]


_TIER_TO_ZONE = {
    "edge": "dmz",
    "app": "private",
    "data": "restricted",
    "security": "identity",
    "observability": "detect",
    "connectivity": "dmz",
    "other": "private",
}

_CONTROL_REFS = {
    "dmz": "ISO 27001 A.13.1 / NIST CSF PR.AC-5 (perimeter)",
    "private": "ISO 27001 A.13.1.3 / NIST CSF PR.AC-3 (network segregation)",
    "restricted": "ISO 27001 A.8.2 / NIST CSF PR.DS-1 (data at rest / classification)",
    "identity": "ISO 27001 A.9 / NIST CSF PR.AC-1 (identity & access)",
    "detect": "ISO 27001 A.12.4 / NIST CSF DE.CM (monitoring)",
}


def _classify_data_label(label: str) -> str:
    low = (label or "").lower()
    if any(x in low for x in ("card", "pci", "payment", "pan")):
        return "PCI"
    if any(x in low for x in ("customer", "user", "pii", "email", "phone", "person")):
        return "PII"
    if any(x in low for x in ("secret", "key", "credential", "token")):
        return "Confidential"
    if any(x in low for x in ("order", "invoice", "ledger", "transaction")):
        return "Confidential"
    if any(x in low for x in ("public", "marketing", "static")):
        return "Public"
    return "Internal"


def _gap_candidates(request_text: str, service_labels: list[str]) -> list[str]:
    blob = f"{request_text} {' '.join(service_labels)}".lower()
    labels_joined = " ".join(service_labels).lower()
    gaps: list[str] = []
    checks = [
        (("waf", "shield", "cloud armor"), "WAF / edge threat filter"),
        (("cognito", "entra", "idp", "identity", "oauth"), "Identity Provider / IdP"),
        (("kms", "key vault", "secrets manager"), "KMS / secrets management"),
        (("backup", "archive", "glacier", "retention"), "Backup / retention archive"),
    ]
    for needles, name in checks:
        if any(n in blob for n in needles) and not any(n in labels_joined for n in needles):
            gaps.append(name)
        elif not any(n in labels_joined for n in needles) and name.startswith(("WAF", "Identity", "KMS")):
            # Recommend common controls even when request is silent — marked as gaps
            if name not in gaps and (
                ("secure" in blob or "compliance" in blob or "pci" in blob or "pii" in blob)
                or name.startswith("WAF")
            ):
                if name.startswith("WAF") and "web" in blob:
                    gaps.append(name)
                elif not name.startswith("WAF"):
                    gaps.append(name)
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for g in gaps:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out[:5]


def achievable_density_target(leaf_count: int) -> int:
    """Scale density expectations to inventory size (never force fabrication)."""
    if leaf_count <= 0:
        return 5
    return max(5, min(14, 2 + round(leaf_count * 1.5)))


def build_view_target_brief(
    services: list[tuple[str, str, str, str]],
    *,
    request_text: str = "",
) -> str:
    """Per-view target model: zones, classification, control refs, gaps, density."""
    svc_labels = [label for _, label, _, _ in services]
    density = achievable_density_target(len(services))
    zone_lines: list[str] = []
    for _, label, tier, _ in services[:24]:
        zone = _TIER_TO_ZONE.get(tier, "private")
        zone_lines.append(f"- {label} → trust zone `{zone}` (from tier {tier})")
    class_lines = [
        f"- {label} → [{_classify_data_label(label)}]"
        for _, label, tier, _ in services
        if tier in {"data", "app"} or any(
            x in label.lower() for x in ("customer", "user", "order", "payment", "rds", "sql")
        )
    ][:12]
    control_lines = [f"- {zone}: {ref}" for zone, ref in _CONTROL_REFS.items()]
    gaps = _gap_candidates(request_text, svc_labels)
    gap_lines = [f"- {g} (recommended - not in current design)" for g in gaps] or ["- (none)"]
    domain_hints = [
        "- domain_experience — channels / personas",
        "- domain_core — primary business capabilities (expand app tier)",
        "- domain_data — generic data stores (Logical labels)",
        "- domain_platform — shared / observability when present",
    ]
    phase_hints = [
        "- phase_ingest — producers",
        "- phase_process — app / stream processors",
        "- phase_store — cache + system of record",
        "- phase_consume — consumers / analytics if present",
    ]
    return "\n".join([
        f"Achievable density target: ~{density} nodes (do not fabricate beyond inventory).",
        "Trust-zone assignment (Security grouping):",
        "\n".join(zone_lines) if zone_lines else "- (none)",
        "Data classification tags (Data flow edges/cards):",
        "\n".join(class_lines) if class_lines else "- (none inferred)",
        "Control-reference candidates (Security cards — cite when applicable):",
        "\n".join(control_lines),
        "Gap candidates (dashed gap=1 nodes only — never as-built):",
        "\n".join(gap_lines),
        "Suggested Logical domains:",
        "\n".join(domain_hints),
        "Suggested Data flow phases:",
        "\n".join(phase_hints),
    ])


def build_architecture_inventory(
    xml: str,
    *,
    service_limit: int = _SERVICE_INVENTORY_LIMIT,
    edge_limit: int = _EDGE_INVENTORY_LIMIT,
    request_text: str = "",
) -> str:
    """Structured services + flows + lens hints for extra-view / docs prompting."""
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Architecture"))
    except ET.ParseError:
        return "Services:\n- (none extracted)\nKey flows:\n- (none)"

    id_to_label: dict[str, str] = {}
    id_to_parent: dict[str, str] = {}
    services: list[tuple[str, str, str, str]] = []  # id, label, tier, shape_type

    for cell in tree.iter("mxCell"):
        cid = cell.get("id") or ""
        if not cid:
            continue
        if cell.get("vertex") == "1" and cell.get("edge") != "1":
            label = _plain_cell_label(cell.get("value") or "")
            if label:
                id_to_label[cid] = label
            id_to_parent[cid] = cell.get("parent") or ""

    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        label = id_to_label.get(cid, "")
        if _is_inventory_noise_label(label, cid):
            continue
        shape_type = cell.get("shape_type") or ""
        if not shape_type and "shape_type=" in style:
            m = re.search(r"shape_type=([^;]+)", style)
            if m:
                shape_type = m.group(1).strip()
        # Prefer real service leaves (have shape_type) over annotation boxes
        parent_id = id_to_parent.get(cid, "")
        parent_label = id_to_label.get(parent_id, "")
        if not parent_label and parent_id:
            grand = id_to_parent.get(parent_id, "")
            parent_label = id_to_label.get(grand, "")
        tier = _infer_service_tier(label, shape_type, parent_label)
        services.append((cid, label, tier, shape_type))
        if len(services) >= service_limit:
            break

    # Prefer entries with shape_type when list is long
    with_st = [s for s in services if s[3]]
    without_st = [s for s in services if not s[3]]
    services = (with_st + without_st)[:service_limit]

    label_by_id = {cid: label for cid, label, _, _ in services}
    for cid, label in id_to_label.items():
        if not _is_inventory_noise_label(label, cid):
            label_by_id.setdefault(cid, label)

    flows: list[str] = []
    for cell in tree.iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        src = cell.get("source") or ""
        tgt = cell.get("target") or ""
        if not src or not tgt:
            continue
        src_l = label_by_id.get(src)
        tgt_l = label_by_id.get(tgt)
        if not src_l or not tgt_l:
            continue
        edge_label = _plain_cell_label(cell.get("value") or "")
        if edge_label:
            flows.append(f"{src_l} → {tgt_l} ({edge_label})")
        else:
            flows.append(f"{src_l} → {tgt_l}")
        if len(flows) >= edge_limit:
            break

    svc_labels = [label for _, label, _, _ in services]
    svc_lines = [
        f"- {label} [{tier}]" + (f" shape_type={st}" if st else "")
        for _, label, tier, st in services
    ] or ["- (none extracted)"]
    flow_lines = [f"- {f}" for f in flows] or ["- (none)"]
    caps = _infer_capability_hints(svc_labels)
    controls = _infer_control_hints(request_text, svc_labels)
    payloads = _infer_payload_hints(svc_labels)

    parts = [
        "Services (reuse labels + shape_type on leaves):",
        "\n".join(svc_lines),
        "Key flows (from Network — re-label by THIS view's lens):",
        "\n".join(flow_lines),
    ]
    if caps:
        parts += ["Capability hints (Logical — rename leaves to capabilities when clearer):",
                  "\n".join(f"- {c}" for c in caps)]
    if controls:
        parts += [
            "Control hints (Security — show as labeled control notes / edge labels; do not invent new products):",
            "\n".join(f"- {c}" for c in controls),
        ]
    if payloads:
        parts += [
            "Payload hints (Data flow — put these on edge labels / hop notes):",
            "\n".join(f"- {p}" for p in payloads),
        ]
    parts += [
        "Per-view target model (use this — do not re-invent zones/payloads from scratch):",
        build_view_target_brief(services, request_text=request_text or ""),
    ]
    return "\n".join(parts)


_VIEW_BRIEFING: dict[str, str] = {
    "logical": (
        "PERSONA: Principal Application / Solution Architect — 25+ years (capability & domain modelling).\n"
        "VIEWPOINT = LOGICAL — what the system does; dependencies between capabilities.\n"
        "GROUP BY: Bounded Contexts / Business Domains. 8–12 capability nodes when possible.\n"
        "EDGES: every edge labeled Invokes / Calls API / Relies on — NOT TLS, NOT JSON payloads.\n"
        "FORBIDDEN: WAF, VPC/AZ/subnet, specific DB product names as the story, encryption, queues-as-hero.\n"
        "ALLOWED: personas, frontends, microservices/capabilities, generic Data Stores, SaaS deps.\n"
        "AUTO-FILL domain detail cards (3–5 bullets). Use generic architecture shapes (no cloud shape_type / MCP icons)."
    ),
    "security": (
        "PERSONA: Principal Security / Trust Architect — 25+ years (zero-trust / cyber / audit).\n"
        "VIEWPOINT = SECURITY — trust boundaries, attack surface, identity verification.\n"
        "GROUP BY: Trust Zones (Public / DMZ / Private / Highly Restricted / Identity / Detect).\n"
        "EDGES: every edge labeled AuthN / AuthZ / TLS / mTLS / inspect / private path — NOT business Invokes, NOT JSON payloads.\n"
        "FORBIDDEN: business microservice soup, UI, queues-as-hero, DB tables, subnet wallpaper.\n"
        "ALLOWED: IdP/IAM, WAF, API GW/ALB, KMS/secrets/ACM when present, high-level app block, sensitive boundary, detect.\n"
        "AUTO-FILL control cards per zone (3–5 bullets). 8–12 nodes when inventory supports."
    ),
    "data_flow": (
        "PERSONA: Principal Integration / Data-Flow Architect — 25+ years (messaging / ETL / state lifecycle).\n"
        "VIEWPOINT = DATA FLOW — lifecycle, transformation, and storage of state/data.\n"
        "GROUP BY: Ingestion / Processing / Storage (/ Analytics) OR Hot path vs Cold path.\n"
        "EDGES: every edge labeled with payload/object (JSON: Order, SQL: CustomerRow, Event: OrderPlaced, Cache: SessionKey).\n"
        "FORBIDDEN: WAF/UI/auth login flows, VPC/subnet wallpaper, security-control essays.\n"
        "ALLOWED: specific DB/cache/broker/ETL types from inventory, producers/consumers.\n"
        "AUTO-FILL phase journey cards (3–5 bullets). 8–12 nodes; omit pure security hops."
    ),
}


def build_single_view_human(
    *,
    human_prompt: str,
    platform: str,
    view: str,
    primary_xml: str | None = None,
    primary_xml_summary: list[str] | None = None,
    inventory_text: str | None = None,
) -> str:
    title = VIEW_PAGE_NAMES.get(view, view)
    if inventory_text is None:
        if primary_xml:
            inventory_text = build_architecture_inventory(
                primary_xml, request_text=human_prompt or "",
            )
        elif primary_xml_summary:
            services = primary_xml_summary[:_SERVICE_INVENTORY_LIMIT]
            inventory_text = (
                "Services (reuse labels + shape_type on leaves):\n"
                + "\n".join(f"- {s} [other]" for s in services)
                + "\nKey flows (from Network — re-label by THIS view's lens):\n- (none)"
            )
        else:
            inventory_text = (
                "Services (reuse labels + shape_type on leaves):\n- (none)\n"
                "Key flows (from Network — re-label by THIS view's lens):\n- (none)"
            )
    briefing = _VIEW_BRIEFING.get(view, "")
    return (
        f"Platform: {platform}\n"
        f"Original request (truncate ok): {human_prompt[:_REQUEST_TRUNCATE_VIEW]}\n"
        f"{briefing}\n"
        "Architecture inventory (from Network/primary — ground truth):\n"
        f"{inventory_text}\n"
        f"Produce ONE page named `{title}` only.\n"
        "This page MUST be visually and narratively distinct from the other view tabs.\n"
        "Return complete draw.io XML in xml + step_markers."
    )


def build_extra_views_human(
    *,
    human_prompt: str,
    platform: str,
    primary_xml_summary: list[str],
    extra_views: list[str],
) -> str:
    """Deprecated batch helper — prefer build_single_view_human per view."""
    titles = ", ".join(VIEW_PAGE_NAMES[v] for v in extra_views if v in VIEW_PAGE_NAMES)
    return (
        f"Platform: {platform}\n"
        f"Original request (truncate ok): {human_prompt[:_REQUEST_TRUNCATE_VIEW]}\n"
        f"Services already in Network diagram: "
        f"{', '.join(primary_xml_summary[:_SERVICE_INVENTORY_LIMIT]) or '(none)'}\n"
        f"Produce pages for: {titles}\n"
        "Return one ExtraViewPage per requested view with complete XML."
    )


_EXTRA_VIEW_SYSTEM = _SINGLE_VIEW_SYSTEM_PREFIX  # backward-compatible alias


class AdrRecord(BaseModel):
    title: str
    status: str = "Accepted"
    context: str = ""
    decision: str = ""
    consequences: str = ""
    alternatives: str = ""


class RaidItem(BaseModel):
    type: str  # Risk | Assumption | Issue | Dependency
    description: str
    impact: str = ""
    mitigation: str = ""
    owner: str = ""


class ArchitectureDocPack(BaseModel):
    """Structured ADR/RAID content derived from the architecture."""

    adrs: list[AdrRecord] = Field(default_factory=list)
    raid: list[RaidItem] = Field(default_factory=list)


def _esc(text: str) -> str:
    return escape(html.unescape(text or ""), {"\"": "&quot;", "'": "&apos;"})


def _td(text: str) -> str:
    return f"&lt;td style=&quot;padding:8px;border:1px solid #BDBDBD;vertical-align:top;&quot;&gt;{_esc(text)}&lt;/td&gt;"


def _th(text: str) -> str:
    return (
        f"&lt;th style=&quot;padding:8px;border:1px solid #9E9E9E;background:#EEEEEE;"
        f"text-align:left;vertical-align:top;&quot;&gt;{_esc(text)}&lt;/th&gt;"
    )


def _tr(*cells: str, zebra: bool = False) -> str:
    bg = "background:#FAFAFA;" if zebra else ""
    return f"&lt;tr style=&quot;{bg}&quot;&gt;{''.join(cells)}&lt;/tr&gt;"


def _html_table(header_cells: list[str], body_rows: list[str]) -> str:
    head = _tr(*[_th(h) for h in header_cells])
    return (
        "&lt;table style=&quot;border-collapse:collapse;width:100%;font-size:12px;&quot;&gt;"
        f"&lt;thead&gt;{head}&lt;/thead&gt;"
        f"&lt;tbody&gt;{''.join(body_rows)}&lt;/tbody&gt;"
        "&lt;/table&gt;"
    )


def _estimate_table_height(row_count: int, *, chars_per_row: int = 220) -> int:
    # Rough wrap estimate: ~80 chars/line in a wide cell → ~28px/line + header.
    lines_per_row = max(2, min(8, (chars_per_row // 90) + 1))
    return 48 + (row_count + 1) * (22 * lines_per_row)


def build_adr_page_xml(adrs: list[AdrRecord]) -> str:
    rows = list(adrs or [])
    if len(rows) > _MAX_ADRS:
        rows = rows[:_MAX_ADRS]
    if not rows:
        rows = [
            AdrRecord(
                title="Document key decisions",
                status="Proposed",
                context="No ADRs were inferred; replace with project decisions.",
                decision="Capture architecture decisions as ADR-NNN entries.",
                consequences="Design rationale stays reviewable by business and engineering.",
                alternatives="Informal notes only.",
            )
        ]
    body_rows: list[str] = []
    for i, adr in enumerate(rows, start=1):
        body_rows.append(
            _tr(
                _td(f"ADR-{i:03d}"),
                _td(adr.title),
                _td(adr.status),
                _td(adr.context),
                _td(adr.decision),
                _td(adr.consequences),
                _td(adr.alternatives),
                zebra=(i % 2 == 0),
            )
        )
    table = _html_table(
        [
            "ID",
            "Title",
            "Status",
            "Situation",
            "What we chose",
            "What happens next / tradeoffs",
            "Alternatives",
        ],
        body_rows,
    )
    height = max(280, _estimate_table_height(len(rows), chars_per_row=320))
    parts = [
        '<mxCell id="0"/><mxCell id="1" parent="0"/>',
        '<mxCell id="adr_title" value="Architecture Decision Records" '
        'style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;'
        'fontStyle=1;fontSize=20;" vertex="1" parent="1">'
        '<mxGeometry x="40" y="20" width="520" height="36" as="geometry"/></mxCell>',
        f'<mxCell id="adr_table" value="{table}" '
        'style="rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        'spacingLeft=8;spacingTop=8;fillColor=#FFFFFF;strokeColor=#666666;fontSize=12;" '
        'vertex="1" parent="1">'
        f'<mxGeometry x="40" y="70" width="1080" height="{height}" as="geometry"/></mxCell>',
    ]
    inner = (
        '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" '
        'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1200" '
        f'pageHeight="{max(900, height + 140)}" math="0" shadow="0">'
        f"<root>{''.join(parts)}</root></mxGraphModel>"
    )
    return wrap_with_mxfile(inner, "ADR Notes")


def build_raid_page_xml(items: list[RaidItem]) -> str:
    counters = {"Risk": 0, "Assumption": 0, "Issue": 0, "Dependency": 0}
    prefixes = {"Risk": "R", "Assumption": "A", "Issue": "I", "Dependency": "D"}
    rows = list(items or [])[:_MAX_RAID]
    if not rows:
        rows = [
            RaidItem(
                type="Risk",
                description="If workloads stay in one availability zone, an outage there takes the service down.",
                impact="High — customer-facing downtime",
                mitigation="Run critical tiers across two or more zones with health checks.",
            ),
            RaidItem(
                type="Assumption",
                description="Managed cloud services will meet the latency targets the business needs.",
                impact="Medium — could miss SLOs",
                mitigation="Validate with a load test before go-live.",
            ),
            RaidItem(
                type="Issue",
                description="This diagram may not yet show a secondary disaster-recovery region.",
                impact="Medium — recovery story incomplete",
                mitigation="Confirm DR scope with stakeholders and update the design.",
            ),
            RaidItem(
                type="Dependency",
                description="Identity provider, DNS, and networking must be ready before cutover.",
                impact="High — blocks launch",
                mitigation="Track need-by dates and define a fallback path.",
            ),
        ]

    body_rows: list[str] = []
    for i, item in enumerate(rows, start=1):
        kind = item.type.strip().title()
        if kind not in counters:
            kind = "Risk"
        counters[kind] += 1
        rid = f"{prefixes[kind]}-{counters[kind]:02d}"
        action = item.mitigation
        if kind == "Assumption" and action:
            action = f"Validation: {action}"
        elif kind == "Dependency" and action:
            action = f"Need-by / fallback: {action}"
        body_rows.append(
            _tr(
                _td(rid),
                _td(kind),
                _td(item.description),
                _td(item.impact),
                _td(action),
                _td(item.owner or "—"),
                zebra=(i % 2 == 0),
            )
        )
    table = _html_table(
        [
            "ID",
            "Type",
            "What this means",
            "Why it matters",
            "What we will do",
            "Owner",
        ],
        body_rows,
    )
    height = max(220, _estimate_table_height(len(rows), chars_per_row=280))
    cells = (
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="raid_title" value="RAID Register" '
        'style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;'
        'fontStyle=1;fontSize=20;" vertex="1" parent="1">'
        '<mxGeometry x="40" y="20" width="400" height="36" as="geometry"/></mxCell>'
        f'<mxCell id="raid_table" value="{table}" '
        'style="rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        'spacingLeft=8;spacingTop=8;fillColor=#FFFFFF;strokeColor=#F5A623;fontSize=12;" '
        'vertex="1" parent="1">'
        f'<mxGeometry x="40" y="70" width="1080" height="{height}" as="geometry"/></mxCell>'
    )
    inner = (
        '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" '
        'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1200" '
        f'pageHeight="{max(900, height + 140)}" math="0" shadow="0">'
        f"<root>{cells}</root></mxGraphModel>"
    )
    return wrap_with_mxfile(inner, "RAID")


_VIEW_LEAF_PLACEHOLDER_STYLE = (
    "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#CCCCCC;"
    "fontColor=#232F3E;verticalLabelPosition=bottom;verticalAlign=top;"
    "align=center;fontSize=12;aspect=fixed;"
)
_VIEW_CARD_STYLE = (
    "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#9E9E9E;"
    "strokeWidth=1;align=left;verticalAlign=top;spacingLeft=10;spacingTop=8;fontSize=11;"
)

_LOGICAL_ROLE_TO_SHAPE_TYPE: dict[str, str] = {
    "actor": "logical_actor",
    "channel": "logical_channel",
    "capability": "logical_capability",
    "api": "logical_api",
    "datastore": "logical_datastore",
    "saas": "logical_saas",
    "platform": "logical_platform",
}


def _logical_leaf_role(label: str) -> str:
    """Classify a Logical leaf for generic stencil selection."""
    low = _norm_label(label)
    if any(x in low for x in (
        "persona", "actor", "user", "customer", "partner", "ops ", "operator", "admin",
    )) and "data store" not in low and "store" not in low:
        return "actor"
    if any(x in low for x in ("channel", "frontend", "portal", "mobile", "web ui", "spa", "experience app")):
        return "channel"
    if any(x in low for x in (
        "data store", "datastore", "database", "persistence", "warehouse",
        "session store", "customer store", "order store", "user store",
    )):
        return "datastore"
    if any(x in low for x in ("saas", "third party", "external", "stripe", "salesforce", "okta")):
        return "saas"
    if any(x in low for x in ("bff", "api", "gateway", "endpoint", "interface")):
        return "api"
    if any(x in low for x in ("monitor", "observ", "platform", "notification", "shared")):
        return "platform"
    return "capability"


def assign_logical_shape_types(page_xml: str) -> str:
    """Tag Logical leaves with logical_* shape_type + non-cloud stencil for MCP.

    Applies builtin generic styles immediately (so local renderer looks right).
    MCP may overwrite with search_shapes hits; miss path uses the same builtins.
    Network / Security / Data flow are unchanged.
    """
    from app.adapters.outbound.diagram.drawio_mcp_client import _LOGICAL_BUILTIN_STYLES

    parsed = _iter_diagram_root(page_xml)
    if parsed is None:
        return page_xml
    tree, root = parsed
    changed = False
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        if cid in {"overview_panel", "legend_box", "0", "1", "2"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_", "degraded_")
        ):
            continue
        if cell.get("gap") == "1":
            continue
        label = _plain_cell_label(cell.get("value") or "")
        if _is_inventory_noise_label(label, cid):
            continue
        role = _logical_leaf_role(label)
        st = _LOGICAL_ROLE_TO_SHAPE_TYPE.get(role, "logical_capability")
        new_style = _LOGICAL_BUILTIN_STYLES.get(st) or _LOGICAL_BUILTIN_STYLES["logical_capability"]
        if not new_style.endswith(";"):
            new_style += ";"
        if cell.get("shape_type") != st:
            cell.set("shape_type", st)
            changed = True
        if cell.get("style") != new_style:
            cell.set("style", new_style)
            changed = True
    if not changed:
        return page_xml
    return ET.tostring(tree, encoding="unicode")


# Backward-compatible alias.
def apply_logical_generic_stencils(page_xml: str) -> str:
    """Assign logical_* shape_types for MCP (builtins on miss)."""
    return assign_logical_shape_types(page_xml)


# Capability / alias phrases → needles matched against inventory labels.
_SHAPE_ALIAS_NEEDLES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("route 53", "route53", "dns routing", "dns"), ("route 53", "route53", "dns")),
    (("waf", "edge shield", "web application firewall"), ("waf",)),
    (("cloudfront", "cdn", "global cdn", "front door"), ("cloudfront", "cdn", "front door")),
    (("alb", "application load balancer", "traffic distributor", "load balancer", "nlb",
      "api gateway", "apigateway"),
     ("alb", "load balancer", "nlb", "application load", "api gateway", "apigateway")),
    (("channel", "frontend", "portal", "experience", "web ui", "mobile app", "bff", "spa"),
     ("alb", "cloudfront", "cdn", "front door", "api gateway")),
    (("capability", "microservice", "micro service", "service api", "orders api", "catalog",
      "checkout", "backend", "application", "runtime", "compute", "web application",
      "app server", "worker", "processor"),
     ("ec2", "ecs", "eks", "lambda", "fargate", "app service", "cloud run", "functions")),
    (("ec2",), ("ec2", "ecs", "eks", "lambda", "fargate", "app service")),
    (("elasticache", "cache", "session store", "session", "redis"),
     ("elasticache", "redis", "cache", "memorystore")),
    (("data store", "datastore", "system of record", "primary database", "database",
      "persistence", "customer store", "order store", "user store"),
     ("rds", "aurora", "dynamodb", "sql", "postgres", "mysql", "cosmos", "spanner")),
    (("rds", "aurora"), ("rds", "aurora", "dynamodb", "sql", "postgres", "mysql")),
    (("cloudwatch", "monitoring", "observability"), ("cloudwatch", "monitor")),
    (("s3", "object", "bucket", "object store"), ("s3", "blob", "bucket")),
    (("queue", "topic", "event bus", "messaging", "broker"),
     ("sqs", "sns", "eventbridge", "kafka", "pubsub", "service bus")),
]


def _norm_label(text: str) -> str:
    plain = _plain_cell_label(text)
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def _iter_diagram_root(xml: str) -> tuple[ET.Element, ET.Element] | None:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Page"))
    except ET.ParseError:
        return None
    root = None
    if tree.tag == "mxfile":
        diag = tree.find("diagram")
        model = diag.find("mxGraphModel") if diag is not None else None
        root = model.find("root") if model is not None else None
    elif tree.tag == "mxGraphModel":
        root = tree.find("root")
    if root is None:
        return None
    return tree, root


def _primary_shape_entries(primary_xml: str) -> list[tuple[str, str, str]]:
    """Return (norm_label, shape_type, display_label) from primary Network XML."""
    parsed = _iter_diagram_root(primary_xml)
    if parsed is None:
        return []
    _, root = parsed
    out: list[tuple[str, str, str]] = []
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        if _is_inventory_noise_label(_plain_cell_label(cell.get("value") or ""), cid):
            continue
        st = cell.get("shape_type") or ""
        if not st and "shape_type=" in style:
            m = re.search(r"shape_type=([^;]+)", style)
            if m:
                st = m.group(1).strip()
        if not st:
            continue
        label = _plain_cell_label(cell.get("value") or "")
        out.append((_norm_label(label), st, label))
    return out


def _primary_entries_by_tier(
    entries: list[tuple[str, str, str]],
) -> dict[str, list[str]]:
    """Map service tier → ordered unique shape_type keys from Network inventory."""
    by_tier: dict[str, list[str]] = {}
    seen: set[str] = set()
    for _nlab, st, display in entries:
        if not st or st in seen:
            continue
        seen.add(st)
        tier = _infer_service_tier(display, st, "")
        by_tier.setdefault(tier, []).append(st)
    return by_tier


def _infer_view_leaf_tier(label: str) -> str:
    """Best-effort tier for a Logical/Security/Data-flow leaf label."""
    low = _norm_label(label)
    if any(x in low for x in ("data store", "datastore", "database", "persistence", "warehouse")):
        return "data"
    if any(x in low for x in ("cache", "session store", "redis")):
        return "data"
    if any(x in low for x in ("waf", "shield", "firewall", "idp", "cognito", "iam", "kms", "secret")):
        return "security"
    if any(x in low for x in ("channel", "persona", "partner", "frontend", "portal", "mobile")):
        return "edge"
    if any(x in low for x in ("monitor", "observ", "cloudwatch", "logging")):
        return "observability"
    if any(x in low for x in ("queue", "topic", "event", "broker", "kafka", "sns", "sqs")):
        return "other"
    return "app"


def _match_shape_type(label: str, entries: list[tuple[str, str, str]]) -> str | None:
    norm = _norm_label(label)
    if not norm or not entries:
        return None
    for nlab, st, _ in entries:
        if nlab and (nlab == norm or nlab in norm or norm in nlab):
            return st
    for aliases, needles in _SHAPE_ALIAS_NEEDLES:
        if not any(a in norm for a in aliases):
            continue
        for nlab, st, _ in entries:
            if any(n in nlab for n in needles):
                return st
            st_low = st.lower()
            if any(n.replace(" ", "_") in st_low or n in st_low for n in needles):
                return st
    return None


def _match_shape_type_by_tier(
    label: str,
    by_tier: dict[str, list[str]],
    used: set[str],
) -> str | None:
    """Assign an inventory shape_type by inferred tier (Logical capability names)."""
    preferred = _infer_view_leaf_tier(label)
    order = [preferred]
    if preferred == "edge":
        order += ["app", "other"]
    elif preferred == "app":
        order += ["other", "edge"]
    elif preferred == "data":
        order += ["other"]
    else:
        order += ["app", "other", "edge", "data"]
    for tier in order:
        pool = by_tier.get(tier) or []
        for st in pool:
            if st not in used:
                used.add(st)
                return st
        if pool:
            return pool[0]
    for sts in by_tier.values():
        if sts:
            return sts[0]
    return None


def inject_shape_types_from_primary(page_xml: str, primary_xml: str) -> str:
    """Copy shape_type from Network inventory onto view leaves (fixes missing icons).

    Extra-view LLMs often emit grey rectangles/ellipses without ``shape_type`` and
    rename leaves to capabilities (\"Orders capability\", \"Customer data store\"),
    so MCP never resolves official icons. Match by label / alias, then by tier.
    """
    entries = _primary_shape_entries(primary_xml)
    if not entries:
        return page_xml
    parsed = _iter_diagram_root(page_xml)
    if parsed is None:
        return page_xml
    tree, root = parsed
    by_tier = _primary_entries_by_tier(entries)
    used: set[str] = set()
    changed = False
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        if cid in {"overview_panel", "legend_box", "0", "1"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_")
        ):
            continue
        if cell.get("gap") == "1":
            continue
        label = _plain_cell_label(cell.get("value") or "")
        if _is_inventory_noise_label(label, cid):
            continue
        existing = cell.get("shape_type") or ""
        matched = existing or _match_shape_type(label, entries)
        if not matched:
            matched = _match_shape_type_by_tier(label, by_tier, used) or ""
        elif matched:
            used.add(matched)
        if not matched:
            continue
        if cell.get("shape_type") != matched:
            cell.set("shape_type", matched)
            changed = True
        # Replace generic ellipse/rect/hexagon so MCP merge has a clean leaf style.
        low = style.lower()
        if (
            "shape=ellipse" in low
            or "shape=rectangle" in low
            or "shape=hexagon" in low
            or "shape=cylinder" in low
            or "mxgraph." not in low
        ):
            if "verticalLabelPosition=bottom" not in style or "shape=ellipse" in low or "shape=rectangle" in low:
                cell.set("style", _VIEW_LEAF_PLACEHOLDER_STYLE)
                changed = True
        elif not existing and "fillColor=#F5F5F5" not in style and "mxgraph." not in low:
            cell.set("style", _VIEW_LEAF_PLACEHOLDER_STYLE)
            changed = True
    if not changed:
        return page_xml
    return ET.tostring(tree, encoding="unicode")



def _detail_card_html(title: str, bullets: list[str]) -> str:
    parts = [
        f'&lt;div style=&quot;font-size:12px;font-weight:bold;&quot;&gt;{_esc(title)}&lt;/div&gt;'
    ]
    for b in bullets:
        parts.append(f"&lt;div&gt;• {_esc(b)}&lt;/div&gt;")
    return "".join(parts)


def _service_names_by_tier(primary_xml: str) -> dict[str, list[str]]:
    entries = _primary_shape_entries(primary_xml)
    parsed = _iter_diagram_root(primary_xml)
    by_tier: dict[str, list[str]] = {}
    if parsed is None:
        return by_tier
    _, root = parsed
    for cell in root.findall("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        label = _plain_cell_label(cell.get("value") or "")
        cid = cell.get("id") or ""
        if _is_inventory_noise_label(label, cid):
            continue
        st = cell.get("shape_type") or ""
        tier = _infer_service_tier(label, st, "")
        by_tier.setdefault(tier, []).append(label)
    # ensure we have something from entries
    if not by_tier and entries:
        for _, st, lab in entries:
            by_tier.setdefault(_infer_service_tier(lab, st, ""), []).append(lab)
    return by_tier


def _build_view_detail_specs(
    view: str,
    primary_xml: str,
    request_text: str,
) -> list[tuple[str, str, list[str]]]:
    """Return (card_id, title, bullets) for auto-injected section cards."""
    tiers = _service_names_by_tier(primary_xml)
    edge = tiers.get("edge", [])
    app = tiers.get("app", [])
    data = tiers.get("data", [])
    obs = tiers.get("observability", []) or tiers.get("other", [])
    req = (request_text or "").lower()

    def join(names: list[str], fallback: str) -> str:
        return ", ".join(names[:4]) if names else fallback

    if view == "logical":
        return [
            ("note_experience", "Experience domain", [
                "Channels & personas that consume the product",
                "Frontend / BFF capabilities — no VPC or WAF here",
                f"Hands off via API to: {join(app, 'application capabilities')}",
                "Dependency style: Calls API / Relies on — not TLS or JSON payload labels",
            ]),
            ("note_core", "Core business domain", [
                f"Primary capabilities: {join(app, 'application services')}",
                "Owns business rules; invokes other domains via API contracts",
                f"Persists via generic data stores (icons from: {join(data, 'inventory data services')})",
                "Expand into multiple capability nodes when the request is rich (8–12 on page)",
            ]),
            ("note_data_cap", "Data capabilities (logical)", [
                "Label as generic Data Stores (Customer store, Session store) — not engine brand names",
                f"Inventory backing for icons: {join(data, 'data services')}",
                "Dependencies: Relies on / Reads-Writes via API from core domain",
                "No subnet or encryption storytelling on this page",
            ]),
            ("note_shared", "Shared / platform domain", [
                f"Cross-cutting capabilities when present: {join(obs, 'platform services')}",
                "Keep shared services out of core domain boxes",
                "No request-path edges into pure monitoring on Logical",
                "Bounded context boundaries must stay visible",
            ]),
        ]
    if view == "security":
        return [
            ("ctrl_public", "Public Internet / Untrusted", [
                "Attack surface starts here — actors outside the trust boundary",
                "No standing trust; all entry must cross the DMZ/perimeter",
                "Do not place private app or data icons in this zone",
            ]),
            ("ctrl_dmz", "DMZ / Perimeter", [
                f"Perimeter controls: {join(edge, 'WAF / CDN / public LB / API GW')}",
                "AuthN/TLS/inspect happen on perimeter crossings",
                "Only this zone should be directly internet-reachable",
                "Edge labels: TLS, inspect, AuthN — not business Invokes",
            ]),
            ("ctrl_private", "Private application zone", [
                f"High-level private app block (collapse microservices): {join(app, 'app tier')}",
                "No UI/business soup — one trusted app boundary is enough",
                "Crossings from DMZ only via controlled private path",
                "Multi-AZ isolation when the request implies HA" if ("az" in req or "multi" in req or "ha" in req)
                else "Keep admin paths off the public internet",
            ]),
            ("ctrl_restricted", "Highly restricted / sensitive", [
                f"Sensitive boundary for: {join(data, 'data stores')}",
                "App-only private path; no internet → data",
                "KMS/secrets/ACM only when inventory/request supports them",
                "Edge labels: private path / TLS — not JSON payloads",
            ]),
            ("ctrl_detect", "Detect & respond", [
                f"Detection/visibility: {join(obs, 'monitoring / security tools')}",
                "Complements perimeter — does not replace it",
                "Place as a shelf below trust zones with clear gap",
            ]),
        ]
    return [
        ("note_ingest", "Ingestion / producers", [
            f"Producers: {join((app[:2] or edge[:1]), 'client / app producers')}",
            "What enters the system (events, API writes, batch files)",
            "Edge labels use payload names (JSON:… / Event:…) — not WAF inspect essays",
            "Omit UI wireframes and auth login sequences",
        ]),
        ("note_process", "Processing", [
            f"Processors: {join(app, 'application / stream processors')}",
            "Transforms or enriches state before durable write",
            "Sync vs async: solid vs dashed with payload labels",
            "Hot path stays on the main corridor",
        ]),
        ("note_store", "Storage", [
            f"Stores: {join(data, 'cache / database / object store')}",
            "Name specific tech when inventory has it (RDS, ElastiCache, S3…)",
            "Payloads: SQL:…, Cache:…, Object:…",
            "Replicas/cache as distinct hops when present",
        ]),
        ("note_consume", "Consumers / analytics", [
            "Who reads the data next (app read models, batch, analytics if present)",
            "Cold path below hot path when batch exists",
            "Every edge still carries a payload label",
            "No VPC/subnet grouping on this page",
        ]),
    ]


def ensure_view_detail_cards(
    page_xml: str,
    *,
    view: str,
    primary_xml: str,
    request_text: str = "",
    min_cards: int = 4,
) -> str:
    """Inject rich section detail cards when the LLM under-delivers, then de-overlap."""
    from app.adapters.outbound.diagram.view_fixers import space_detail_cards
    from app.adapters.outbound.diagram.xml_diagnostics import absolute_position

    parsed = _iter_diagram_root(page_xml)
    if parsed is None:
        return page_xml
    tree, root = parsed
    existing = 0
    for cell in root.iter("mxCell"):
        cid = cell.get("id") or ""
        if cid.startswith(("note_", "ctrl_")):
            existing += 1
    if existing >= min_cards:
        # LLM already emitted cards — still snap them next to their sections
        return space_detail_cards(ET.tostring(tree, encoding="unicode"), view=view)

    specs = _build_view_detail_specs(view, primary_xml, request_text)
    have = {c.get("id") for c in root.iter("mxCell") if c.get("id")}
    by_id = {c.get("id"): c for c in root.iter("mxCell") if c.get("id")}

    # Temporary seed positions; space_detail_cards snaps next to section boxes
    panel = by_id.get("overview_panel")
    content_max_y = 40.0
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        if cid in {"0", "1", "overview_panel", "legend_box"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_")
        ):
            continue
        ax, ay = absolute_position(cell, by_id)
        geo = cell.find("mxGeometry")
        try:
            h = float(geo.get("height") or 78) if geo is not None else 78.0
        except ValueError:
            h = 78.0
        content_max_y = max(content_max_y, ay + h + 36)

    base_x, base_y = 40.0, content_max_y + 16.0
    if panel is not None:
        geom = panel.find("mxGeometry")
        if geom is not None:
            try:
                base_x = max(40.0, float(geom.get("x") or 1000) - 340.0)
            except ValueError:
                pass
    y = base_y
    added = 0
    for card_id, title, bullets in specs:
        if card_id in have:
            continue
        cell = ET.Element("mxCell")
        cell.set("id", card_id)
        cell.set(
            "value",
            f'<div style="font-size:12px;font-weight:bold;">{title}</div>'
            + "".join(f"<div>• {b}</div>" for b in bullets),
        )
        cell.set("style", _VIEW_CARD_STYLE)
        cell.set("vertex", "1")
        cell.set("parent", "1")
        geom = ET.SubElement(cell, "mxGeometry")
        geom.set("x", str(int(base_x)))
        geom.set("y", str(int(y)))
        geom.set("width", "280")
        h = 56 + 18 * len(bullets)
        geom.set("height", str(h))
        geom.set("as", "geometry")
        root.append(cell)
        have.add(card_id)
        y += h + 24
        added += 1
    xml_out = ET.tostring(tree, encoding="unicode") if added else page_xml
    return space_detail_cards(xml_out, view=view)


def page_has_overview_panel(xml: str) -> bool:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Page"))
    except ET.ParseError:
        return False
    return any(c.get("id") == "overview_panel" for c in tree.iter("mxCell"))


def ensure_minimal_overview_panel(xml: str, view_title: str) -> str:
    """If overview_panel is missing, add a short stub titled for this view."""
    if page_has_overview_panel(xml):
        return ensure_diagram_document(xml, view_title)
    trimmed = ensure_diagram_document(xml or "", view_title)
    try:
        tree = ET.fromstring(trimmed)
    except ET.ParseError:
        return trimmed
    root = None
    if tree.tag == "mxfile":
        diag = tree.find("diagram")
        if diag is None:
            return trimmed
        model = diag.find("mxGraphModel")
        root = model.find("root") if model is not None else None
    elif tree.tag == "mxGraphModel":
        root = tree.find("root")
    if root is None:
        return trimmed
    title = _esc(f"{view_title} overview")
    body = (
        f"&lt;b&gt;{title}&lt;/b&gt;&lt;br/&gt;"
        "1. Review the services and groups on this page.&lt;br/&gt;"
        "2. Follow the relationships that matter for this view.&lt;br/&gt;"
        "3. Use other tabs for network, security, or data-path detail."
    )
    panel = ET.fromstring(
        f'<mxCell id="overview_panel" value="{body}" '
        'style="rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        'spacingLeft=10;spacingTop=8;fillColor=#FFF8E1;strokeColor=#F5A623;fontSize=11;" '
        'vertex="1" parent="1">'
        '<mxGeometry x="980" y="40" width="260" height="140" as="geometry"/></mxCell>'
    )
    root.append(panel)
    return ET.tostring(tree, encoding="unicode")


def _extract_diagram_inner(page_xml: str) -> tuple[str, str]:
    """Return (name, mxGraphModel XML) from a page or full mxfile.

    Recursively unwraps accidental nested ``<mxfile>`` wrappers (LLM/MCP often
    emit a full document where only an ``mxGraphModel`` belongs).
    """
    trimmed = ensure_diagram_document(page_xml or "", "Page")
    try:
        tree = ET.fromstring(trimmed)
    except ET.ParseError:
        return "Page", trimmed

    name = "Page"
    for _ in range(4):  # bounded unwrap
        if tree.tag != "mxfile":
            break
        diag = tree.find("diagram")
        if diag is None:
            break
        name = diag.get("name") or name
        nested = diag.find("mxfile")
        if nested is not None:
            tree = nested
            continue
        model = diag.find("mxGraphModel")
        if model is not None:
            return name, ET.tostring(model, encoding="unicode")
        model_xml = "".join(ET.tostring(c, encoding="unicode") for c in list(diag)).strip()
        if not model_xml and (diag.text or "").strip():
            model_xml = diag.text.strip()
        if model_xml.lstrip().startswith("<mxfile"):
            try:
                tree = ET.fromstring(model_xml)
                continue
            except ET.ParseError:
                pass
        if "<mxGraphModel" in model_xml:
            return name, model_xml
        return name, model_xml or ET.tostring(diag, encoding="unicode")

    if tree.tag == "mxGraphModel":
        return name, ET.tostring(tree, encoding="unicode")
    return name, trimmed


def normalize_single_page_xml(page_xml: str, page_name: str = "Page") -> str:
    """Unwrap nested mxfile wrappers and return a single-page mxfile document."""
    _, inner = _extract_diagram_inner(page_xml)
    if "<mxfile" in inner:
        _, inner = _extract_diagram_inner(inner)
    return ensure_diagram_document(inner, page_name)


def merge_mxfile_pages(pages: list[tuple[str, str]]) -> str:
    """Merge (page_name, page_xml) into one mxfile with multiple diagram pages."""
    if not pages:
        return wrap_with_mxfile("", "Architecture")
    if len(pages) == 1:
        name, xml = pages[0]
        return ensure_diagram_document(xml, name)

    parts = ['<mxfile host="embed.diagrams.net">']
    for idx, (name, xml) in enumerate(pages):
        safe_name = re.sub(r"[^\w\s\-./]", "", name or f"Page-{idx + 1}").strip() or f"Page-{idx + 1}"
        page_id = f"page-{idx + 1}"
        _, inner = _extract_diagram_inner(xml)
        # Never embed a nested mxfile inside <diagram>
        if "<mxfile" in inner:
            _, inner = _extract_diagram_inner(inner)
        if "<mxGraphModel" not in inner:
            inner = (
                '<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
                'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                'pageWidth="1169" pageHeight="827" math="0" shadow="0">'
                f"<root>{inner}</root></mxGraphModel>"
            )
        parts.append(f'<diagram name="{escape(safe_name)}" id="{page_id}">')
        parts.append(inner)
        parts.append("</diagram>")
    parts.append("</mxfile>")
    return "\n".join(parts)


def page_names_in_mxfile(xml: str) -> list[str]:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml, "Architecture"))
    except ET.ParseError:
        return []
    if tree.tag != "mxfile":
        return ["Architecture"]
    return [d.get("name") or "Page" for d in tree.findall("diagram")]


def apply_repair_per_view_page(xml: str, repair_fn) -> str:
    """Run a repair callable on each diagram page; skip ADR/RAID doc pages."""
    trimmed = (xml or "").strip()
    if "<mxfile" not in trimmed:
        return repair_fn(trimmed)
    try:
        tree = ET.fromstring(trimmed)
    except ET.ParseError:
        return repair_fn(trimmed)
    if tree.tag != "mxfile":
        return repair_fn(trimmed)

    skip = {"adr notes", "raid"}
    changed = False
    for diag in list(tree.findall("diagram")):
        name = (diag.get("name") or "").strip().lower()
        if name in skip:
            continue
        model_xml = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
        if not model_xml.strip() and (diag.text or "").strip():
            model_xml = diag.text.strip()
        wrapped = wrap_with_mxfile(model_xml, diag.get("name") or "Page")
        repaired = repair_fn(wrapped)
        try:
            rtree = ET.fromstring(ensure_diagram_document(repaired, diag.get("name") or "Page"))
        except ET.ParseError:
            continue
        new_diag = rtree.find("diagram") if rtree.tag == "mxfile" else None
        if new_diag is None:
            continue
        diag.clear()
        for k, v in new_diag.attrib.items():
            diag.set(k, v)
        diag.extend(list(new_diag))
        changed = True
    if not changed:
        return trimmed
    return ET.tostring(tree, encoding="unicode")


def summarize_services_from_xml(xml: str, limit: int = 40) -> list[str]:
    """Collect leaf labels for ADR/RAID prompting."""
    labels: list[str] = []
    try:
        tree = ET.fromstring(ensure_diagram_document(xml, "Architecture"))
    except ET.ParseError:
        return labels
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        style = cell.get("style") or ""
        if "container=1" in style or cell.get("id") in {"overview_panel", "legend_box"}:
            continue
        value = (cell.get("value") or "").strip()
        if not value or value.lower() in {"service", "icon", "node", "group"}:
            continue
        # strip simple HTML
        plain = re.sub(r"<[^>]+>", " ", value)
        plain = html.unescape(plain).strip()
        if plain and plain not in labels:
            labels.append(plain)
        if len(labels) >= limit:
            break
    return labels
