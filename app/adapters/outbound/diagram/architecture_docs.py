"""Multi-page architecture documentation helpers (views, ADR, RAID)."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from app.adapters.outbound.config.env_config import EnvConfigAdapter
from app.adapters.outbound.diagram import palette
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document, wrap_with_mxfile
from app.domain.entities.xml_diagram import StepMarker

_VIEW_ORDER = ("logical", "network", "data_flow", "security")
#: Canonical tab/selection order across the app — Logical, Network, Data flow,
#: Security. Public alias so callers outside this module (page merging) don't
#: reach for the private name.
VIEW_ORDER = _VIEW_ORDER
VIEW_PAGE_NAMES = {
    "logical": "Logical",
    "network": "Network",
    "security": "Security",
    "data_flow": "Data flow",
}
_DOCS_PACK_FILE = "docs/adr_raid.txt"
_GAP_ANALYSIS_PACK_FILE = "gap_analysis.txt"
_CONFIG_DIR = EnvConfigAdapter.base_dir() / "config" / "xml_gen"
_MAX_ADRS = 15  # soft safety against runaway model dumps
_MAX_RAID = 24
_SERVICE_INVENTORY_LIMIT = 36
_EDGE_INVENTORY_LIMIT = 20
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
    return selected or ["logical"]


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
    # Default Network-only: IR network pack already owns hierarchy.
    if views == ["network"] and primary == "network":
        return ""
    focus = {
        "network": (
            "Focus this page on the Network / VPC view: full Cloud→Region→VPC→Zone→Subnet "
            "hierarchy with connectivity hubs at Cloud level."
        ),
        "logical": (
            "Focus this page on the Logical view: sparse next-ai-style process "
            "storyboard (icons + labeled edges; domain_* ids are JSON ranks only) "
            "— no VPC/subnet topology on this page."
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
        f"Primary IR view is `{VIEW_PAGE_NAMES[primary]}` only "
        f"(other views / ADR / RAID are separate pipeline passes).",
        focus[primary],
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


def load_gap_analysis_prompt_pack() -> str:
    path = _CONFIG_DIR / _GAP_ANALYSIS_PACK_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "You are a senior enterprise architect. Given a short brief, default any "
        "missing security, resilience, observability, and data-classification "
        "components using industry-standard practice, and record each default as "
        "an explicit, low-ceremony assumption. Never invent numbers as fact."
    )


def _plain_cell_label(value: str) -> str:
    plain = re.sub(r"<[^>]+>", " ", value or "")
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def _is_inventory_noise_label(label: str, cell_id: str) -> bool:
    """Drop badges, overview, and digit-only noise from inventory lists."""
    cid = (cell_id or "").strip().lower()
    if cid in {"overview_panel", "legend_box", "cloud_boundary", "0", "1"} or cid.startswith("step_badge_"):
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


def _labels_blob(service_labels: list[str]) -> str:
    return " ".join(service_labels).lower()


def _has_enterprise_saas(service_labels: list[str], request_text: str = "") -> bool:
    """True when inventory/request names external enterprise SaaS products."""
    blob = f"{request_text} {_labels_blob(service_labels)}".lower()
    needles = (
        "workday", "successfactors", "salesforce", "guidewire", "servicenow",
        "okta", "auth0", "stripe", "adyen", "saas", "(saas)",
    )
    return any(n in blob for n in needles)


def _has_app_runtime(service_labels: list[str], request_text: str = "") -> bool:
    blob = f"{request_text} {_labels_blob(service_labels)}".lower()
    return any(
        n in blob
        for n in (
            "ecs", "eks", "lambda", "ec2", "fargate", "app service", "cloud run",
            "gke", "aks", "portal", "bff", "microservice", "application",
        )
    )


def _has_primary_store(service_labels: list[str], request_text: str = "") -> bool:
    blob = f"{request_text} {_labels_blob(service_labels)}".lower()
    return any(
        n in blob
        for n in (
            "aurora", "rds", "dynamodb", "postgres", "mysql", "sql", "database",
            "data store", "datastore",
        )
    )


def _has_cache_layer(service_labels: list[str], request_text: str = "") -> bool:
    blob = f"{request_text} {_labels_blob(service_labels)}".lower()
    return any(
        n in blob
        for n in ("elasticache", "redis", "memcached", "memorystore", "cache")
    )


def _gap_candidates(
    request_text: str,
    service_labels: list[str],
    *,
    view: str = "",
) -> list[str]:
    """Recommend missing items relevant to the audience of ``view``.

    Security → control gaps (edge filter, IdP, KMS, monitoring).
    Data flow → data-path gaps (DLQ, backup, CDC, schema registry).
    Logical → cache / graceful degradation when SaaS egress lacks a cache.
    Other / empty view → security-flavored defaults for inventory briefs.

    Enterprise SaaS + app/store is enough to recommend resilience companions —
    callers need not mention PCI/PII for those gaps to fire.
    """
    blob = f"{request_text} {' '.join(service_labels)}".lower()
    labels_joined = " ".join(service_labels).lower()
    view_key = (view or "").strip().lower()
    gaps: list[str] = []

    def _missing(needles: tuple[str, ...]) -> bool:
        return not any(n in labels_joined for n in needles)

    def _mentioned(needles: tuple[str, ...]) -> bool:
        return any(n in blob for n in needles)

    has_saas = _has_enterprise_saas(service_labels, request_text)
    has_app = _has_app_runtime(service_labels, request_text)
    has_store = _has_primary_store(service_labels, request_text)
    saas_resilience = has_saas and (has_app or has_store)

    if view_key == "data_flow":
        checks = [
            (("dlq", "dead letter", "dead-letter", "retry queue", "sqs"), "Dead-letter / retry path"),
            (("backup", "archive", "glacier", "retention", "snapshot"), "Backup / retention archive"),
            (("cdc", "replica", "read replica", "dms"), "CDC / read replica path"),
            (("schema registry", "glue catalog", "glue", "avro schema"), "Schema registry / data catalog"),
        ]
        for needles, name in checks:
            if not _missing(needles):
                continue
            if (
                _mentioned(needles)
                or _mentioned(("pii", "pci", "pipeline", "stream", "event", "batch", "etl", "data"))
                or name.startswith(("Dead-letter", "Backup"))
                or saas_resilience
            ):
                gaps.append(name)
    elif view_key == "logical":
        if saas_resilience and not _has_cache_layer(service_labels, request_text):
            gaps.append("Cache / graceful degradation for SaaS outage")
    else:
        # Security (default) — control gaps for risk owners
        checks = [
            (("waf", "shield", "cloud armor"), "Edge web filter (WAF)"),
            (("cognito", "entra", "idp", "identity", "oauth", "auth0", "okta"), "Central login / identity service"),
            (("kms", "key vault", "secrets manager", "secrets"), "Keys / secrets vault"),
            (
                ("cloudwatch", "guardduty", "security hub", "sentinel", "siem", "logging"),
                "Attack / abuse monitoring",
            ),
            (("backup", "archive", "glacier", "retention"), "Backup / retention archive"),
        ]
        sensitive = _mentioned(("secure", "compliance", "pci", "pii", "trust", "zero trust", "ciso"))
        for needles, name in checks:
            if not _missing(needles):
                continue
            if _mentioned(needles):
                gaps.append(name)
            elif name.startswith("Edge web filter") and ("web" in blob or "api" in blob or sensitive):
                gaps.append(name)
            elif name.startswith("Central login") and (sensitive or "login" in blob or "auth" in blob or has_saas):
                gaps.append(name)
            # SaaS + app: always recommend secrets vault and attack monitoring when missing.
            elif name.startswith("Keys") and (sensitive or (has_saas and has_app)):
                gaps.append(name)
            elif name.startswith("Attack") and (
                sensitive or "monitor" in blob or "detect" in blob or (has_saas and has_app)
            ):
                gaps.append(name)
            elif view_key != "data_flow" and name.startswith("Edge web filter") and "web" in blob:
                gaps.append(name)

    seen: set[str] = set()
    out: list[str] = []
    for g in gaps:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out[:5]


def achievable_density_target(leaf_count: int) -> int:
    """Scale density expectations to inventory size (never force fabrication).

    Cap at 12 to match the IR prompt packs' "8–12 when supported; 5–7 when thin"
    guidance — a target of 14 was scoring sparse-but-correct views as under-dense
    and pushing the model to invent padding.
    """
    if leaf_count <= 0:
        return 5
    return max(5, min(12, 2 + round(leaf_count * 1.25)))


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
    gaps = _gap_candidates(request_text, svc_labels, view="")
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


class RequiredAddition(BaseModel):
    """One architecturally-necessary component the brief omitted."""

    component: str
    viewpoint: str = "generic"  # security | resilience | observability | data | network | cost
    why: str = ""
    applies_to_views: list[str] = Field(default_factory=list)  # logical|network|security|data_flow


class GapAssumption(BaseModel):
    """One explicit, defensible default the gap-analysis pass took."""

    id: str
    assumption: str
    confidence: str = "medium"  # high | medium | low
    severity: str = "medium"  # low | medium | high | critical


class GapAnalysisResult(BaseModel):
    """Pre-generation architecture gap-analysis enrichment.

    Deliberately compact — this is prompt-context for the diagram generator,
    not an ARB deliverable. See ``config/xml_gen/gap_analysis.txt`` for the
    full viewpoint checklist this is derived from.
    """

    primary_capability: str = ""
    inferred_domain: str = ""  # ecommerce | saas | fintech | healthcare | insurance | generic | ...
    diagram_altitude: str = "container"  # context | container | component | deployment
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    required_additions: list[RequiredAddition] = Field(default_factory=list)
    assumptions: list[GapAssumption] = Field(default_factory=list)
    nfr_summary: dict[str, str] = Field(default_factory=dict)


def build_gap_analysis_block(result: "GapAnalysisResult | None", views: list[str] | None = None) -> str:
    """Flatten a ``GapAnalysisResult`` into a compact prompt-text block.

    Filters ``required_additions`` to the views actually being rendered (a
    Security-only addition shouldn't bloat the Data-flow prompt). Returns
    ``""`` when there is nothing to say, so callers can safely append this to
    ``context_parts`` unconditionally.
    """
    if result is None:
        return ""
    selected = set(normalize_diagram_views(views)) if views else None
    lines: list[str] = ["Architecture gap analysis (defaulted — treat as authoritative unless the request contradicts it):"]
    if result.primary_capability:
        lines.append(f"- Primary capability: {result.primary_capability}")
    if result.in_scope:
        lines.append(f"- In scope: {', '.join(result.in_scope)}")
    if result.out_of_scope:
        lines.append(f"- Out of scope (do not draw): {', '.join(result.out_of_scope)}")
    additions = [
        a for a in result.required_additions
        if not selected or not a.applies_to_views or selected & set(a.applies_to_views)
    ]
    for a in additions:
        why = f" — {a.why}" if a.why else ""
        lines.append(f"- Add ({a.viewpoint}): {a.component}{why}")
    if result.nfr_summary:
        nfr_bits = "; ".join(f"{k}={v}" for k, v in result.nfr_summary.items() if v)
        if nfr_bits:
            lines.append(f"- NFRs: {nfr_bits}")
    return "\n".join(lines) if len(lines) > 1 else ""


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

# Banking / Insurance / enterprise SaaS — shape_type → label needles (longest match wins).
# Used on all views for brand saas_* stencils; Logical also keeps "(SAAS)" labels.
_SAAS_VENDOR_NEEDLES: list[tuple[str, tuple[str, ...]]] = [
    ("saas_salesforce", ("salesforce", "sales force", "sfdc", "sales cloud", "service cloud")),
    ("saas_workday", ("workday",)),
    ("saas_successfactors", (
        "successfactors", "success factors", "sap successfactors", "sap success factors", "sfsf",
    )),
    ("saas_servicenow", ("servicenow", "service now", "snow platform", "now platform")),
    ("saas_okta", ("okta",)),
    ("saas_guidewire", ("guidewire", "guide wire", "policycenter", "claimcenter", "billingcenter")),
    ("saas_duck_creek", ("duck creek", "duckcreek")),
    ("saas_temenos", ("temenos", "t24")),
    ("saas_finastra", ("finastra",)),
    ("saas_fiserv", ("fiserv",)),
    ("saas_fis", ("fis",)),
    ("saas_murex", ("murex", "mx.3", "mx 3")),
    ("saas_bloomberg", ("bloomberg", "bbg")),
    ("saas_refinitiv", ("refinitiv", "eikon", "lseg")),
    ("saas_experian", ("experian",)),
    ("saas_equifax", ("equifax",)),
    ("saas_stripe", ("stripe",)),
    ("saas_adyen", ("adyen",)),
    ("saas_plaid", ("plaid",)),
    ("saas_slack", ("slack",)),
    ("saas_office365", ("office 365", "microsoft 365", "m365", "o365", "office365")),
    ("saas_microsoft_365", ("microsoft 365", "m365")),
]

# Journey band for Network L→R placement (lower = further left). Generic, not industry-specific.
_SAAS_JOURNEY_ROLE: dict[str, str] = {
    "saas_okta": "identity",
    "saas_salesforce": "experience",
    "saas_servicenow": "experience",
    "saas_workday": "workforce",
    "saas_successfactors": "workforce",
    "saas_guidewire": "industry",
    "saas_duck_creek": "industry",
    "saas_temenos": "industry",
    "saas_finastra": "industry",
    "saas_fiserv": "industry",
    "saas_fis": "industry",
    "saas_murex": "industry",
    "saas_bloomberg": "industry",
    "saas_refinitiv": "industry",
    "saas_experian": "payments",
    "saas_equifax": "payments",
    "saas_stripe": "payments",
    "saas_adyen": "payments",
    "saas_plaid": "payments",
    "saas_slack": "collab",
    "saas_office365": "collab",
    "saas_microsoft_365": "collab",
}

_SAAS_JOURNEY_ORDER: dict[str, int] = {
    "identity": 0,
    "experience": 1,
    "workforce": 2,
    "industry": 3,
    "payments": 4,
    "collab": 5,
    "other": 6,
}

# Default Network side when edges don't decide the call path.
# identity = user hits IdP first (Okta → redirect into Cloud).
# Most enterprise systems-of-record are Cloud→SaaS API dependencies (right of Cloud).
_SAAS_DEFAULT_SIDE: dict[str, str] = {
    "identity": "ingress",
    "experience": "egress",
    "workforce": "egress",
    "industry": "egress",
    "payments": "egress",
    "collab": "egress",
    "other": "egress",
}

# Default Data-flow shelf when edges don't decide the direction.
# producer = a system of record feeding data INTO the pipeline (identity/
# experience/workforce/industry vendors are typically sources the pipeline
# ingests from). consumer = a system the pipeline sends data OUT to
# (payment/credit processors are called with a request; collab tools receive
# notifications) — the pipeline is the one initiating those calls.
_SAAS_DEFAULT_DIRECTION: dict[str, str] = {
    "identity": "producer",
    "experience": "producer",
    "workforce": "producer",
    "industry": "producer",
    "payments": "consumer",
    "collab": "consumer",
    "other": "consumer",
}

_SAAS_GENERIC_NEEDLES = (
    "saas", "third party", "third-party", "external idp", "auth0", "auth 0",
    "entra id", "cognito", "twilio", "sendgrid", "shopify",
)

_SAAS_HINT_TOKENS = frozenset({
    "bank", "banking", "insurance", "insurer", "claims", "policy", "underwriting",
    "fintech", "payments", "salesforce", "workday", "successfactors", "guidewire", "temenos",
    "servicenow", "okta", "duck", "creek", "finastra", "fiserv", "murex",
    "bloomberg", "refinitiv", "experian", "equifax", "stripe", "adyen", "plaid",
    "saas", "bfsi", "sfsf",
})


def saas_journey_role(shape_type: str | None, label: str = "") -> str:
    """Return journey role key for Network L→R ordering."""
    st = (shape_type or "").strip().lower()
    if st in _SAAS_JOURNEY_ROLE:
        return _SAAS_JOURNEY_ROLE[st]
    if not st.startswith("saas_"):
        matched = match_saas_shape_type(label)
        if matched and matched in _SAAS_JOURNEY_ROLE:
            return _SAAS_JOURNEY_ROLE[matched]
    return "other"


def saas_journey_sort_key(shape_type: str | None, label: str = "") -> tuple[int, str]:
    role = saas_journey_role(shape_type, label)
    return (_SAAS_JOURNEY_ORDER.get(role, 6), (label or "").lower())


def saas_default_network_side(shape_type: str | None, label: str = "") -> str:
    """ingress = left of Cloud (user path); egress = right (Cloud calls SaaS)."""
    role = saas_journey_role(shape_type, label)
    return _SAAS_DEFAULT_SIDE.get(role, "egress")


def saas_default_data_flow_direction(shape_type: str | None, label: str = "") -> str:
    """producer = feeds data into the pipeline; consumer = pipeline sends data out to it.

    Same vendor-role table as ``saas_journey_role``/``saas_default_network_side``
    (Network's ingress/egress axis) but answering a different question — a
    vendor can legitimately default to opposite answers on the two axes (e.g.
    Okta defaults to Network-ingress *and* Data-flow-producer; both are true
    independently). Used only as a fallback when no edge tells us the real
    direction — see ``resolve_data_flow_saas_direction``.
    """
    role = saas_journey_role(shape_type, label)
    return _SAAS_DEFAULT_DIRECTION.get(role, "consumer")


def resolve_saas_network_side(
    shape_type: str | None,
    label: str = "",
    *,
    cell_id: str,
    edges: list[tuple[str, str]],
    actor_ids: set[str],
    cloud_descendant_ids: set[str],
) -> str:
    """Decide Network placement from call-path edges, falling back to role default.

    - Actor/Users → SaaS (or SaaS → Edge/Cloud) ⇒ **ingress** (user hits SaaS, then Cloud)
    - Cloud/app → SaaS ⇒ **egress** (hosted services call SaaS for data)
    Mixed signals: prefer ingress if any actor link exists (user-visible path wins),
    else egress if any cloud→SaaS link, else role default.
    """
    cid = (cell_id or "").strip()
    if not cid:
        return saas_default_network_side(shape_type, label)

    from_actor = False
    to_cloud = False
    from_cloud = False
    for src, tgt in edges:
        if src == cid and tgt in actor_ids:
            # rare reverse; treat as user-adjacent
            from_actor = True
        if tgt == cid and src in actor_ids:
            from_actor = True
        if src == cid and tgt in cloud_descendant_ids:
            to_cloud = True
        if tgt == cid and src in cloud_descendant_ids:
            from_cloud = True

    if from_actor or to_cloud:
        return "ingress"
    if from_cloud:
        return "egress"
    # Label hints for portals / login that users hit first.
    low = (label or "").lower()
    if any(h in low for h in ("login", "sso", "idp", "sign-in", "signin", "auth portal")):
        return "ingress"
    if any(h in low for h in ("portal", "community", "self-service")) and saas_journey_role(
        shape_type, label
    ) == "experience":
        return "ingress"
    return saas_default_network_side(shape_type, label)


def match_saas_shape_type(label: str) -> str | None:
    """Return saas_* shape_type when label matches a curated vendor needle."""
    plain = _norm_label(label)
    if not plain:
        return None
    tokens = set(plain.split())
    best: tuple[int, str] | None = None
    for shape_type, needles in _SAAS_VENDOR_NEEDLES:
        for needle in needles:
            n = re.sub(r"[^a-z0-9]+", " ", needle.lower()).strip()
            if not n:
                continue
            matched = False
            if " " in n:
                matched = n in plain
            elif len(n) <= 3:
                matched = n in tokens
            else:
                matched = n in plain
            if not matched:
                continue
            score = len(n)
            if best is None or score > best[0]:
                best = (score, shape_type)
    return best[1] if best else None


def _label_looks_like_saas(label: str) -> bool:
    if match_saas_shape_type(label):
        return True
    low = _norm_label(label)
    if "(saas)" in low or low.endswith(" saas"):
        return True
    return any(x in low for x in _SAAS_GENERIC_NEEDLES)


def ensure_saas_labels_on_logical(page_xml: str) -> str:
    """Append `` (SAAS)`` to external-app leaves on Logical; keep brand saas_* keys."""
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
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_", "degraded_", "domain_")
        ):
            continue
        if cell.get("gap") == "1":
            continue
        raw = cell.get("value") or ""
        label = _plain_cell_label(raw)
        if _is_inventory_noise_label(label, cid):
            continue
        if not _label_looks_like_saas(label):
            continue
        if re.search(r"\(saas\)\s*$", label, flags=re.IGNORECASE):
            continue
        # Preserve HTML value wrappers when present; append to plain text otherwise.
        if raw != label and "<" in raw:
            new_label = f"{label} (SAAS)"
            cell.set("value", new_label)
        else:
            cell.set("value", f"{label} (SAAS)")
        changed = True
    if not changed:
        return page_xml
    return ET.tostring(tree, encoding="unicode")


def assign_saas_shape_types(page_xml: str) -> str:
    """Stamp curated saas_* + builtin SVG styles when a vendor label matches.

    Overwrites empty types and ``logical_saas`` (brand icons on all views).
    Does not overwrite cloud shape_types or other ``logical_*`` roles.
    """
    from app.adapters.outbound.diagram.drawio_mcp_client import _builtin_style

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
        current = (cell.get("shape_type") or "").strip().lower()
        # Keep cloud icons and non-SaaS logical roles (actor/channel/…).
        if current.startswith(("aws_", "gcp_", "azure_")):
            continue
        if current.startswith("logical_") and current != "logical_saas":
            continue
        matched = match_saas_shape_type(label)
        if not matched:
            if current.startswith("saas_"):
                matched = current
            else:
                continue
        if current != matched:
            cell.set("shape_type", matched)
            changed = True
        builtin = _builtin_style(matched)
        if builtin and cell.get("style") != builtin:
            cell.set("style", builtin)
            changed = True
        # Never touch mxGeometry — IR compiler owns leaf size (LEAF_W×LEAF_H).
    if not changed:
        return page_xml
    return ET.tostring(tree, encoding="unicode")


def _logical_leaf_role(label: str) -> str:
    """Classify a Logical leaf for generic stencil selection.

    Order matters: stores and channels before actors; external IdP SaaS before
    in-house identity capabilities; API/BFF before generic capability.
    """
    low = _norm_label(label)
    # Persistence first — "User store" / "Customer store" must not become actors.
    if any(x in low for x in (
        "data store", "datastore", "database", "persistence", "warehouse",
        "session store", "customer store", "order store", "user store",
        "catalog store", "payment store",
    )) or (low.endswith(" store") and "app store" not in low):
        return "datastore"
    # Experience surfaces before personas ("Admin portal", "Ops portal", "Application").
    if any(x in low for x in (
        "channel", "frontend", "portal", "mobile", "web ui", "spa",
        "experience app", "web app", "mobile app", "admin console",
        "employee portal", "hr portal", "corporate portal",
        "web application", "mobile application",
    )) or low in {"application", "app", "the application", "hr application"}:
        return "channel"
    # External IdP / SaaS products (not in-house Identity capability).
    if _label_looks_like_saas(label):
        return "saas"
    if any(x in low for x in ("bff", " api", "api ", "gateway", "endpoint", "interface")) or low.endswith(" api"):
        return "api"
    if any(x in low for x in ("monitor", "observ", "notification", "shared platform", "platform service")):
        return "platform"
    # Personas / actors — exclude store/portal already handled above.
    if any(x in low for x in (
        "persona", "actor", "customer", "partner", "operator", "end user", "end-user",
        "employee", "employees", "worker", "staff",
    )) or low in {"user", "users", "admin", "ops"} or low.startswith("ops "):
        return "actor"
    if "platform" in low and "capability" not in low:
        return "platform"
    return "capability"


def assign_logical_shape_types(page_xml: str) -> str:
    """Tag generic Logical leaves with logical_* shape_type + builtin stencil.

    Leaves that already have ``aws_*`` / ``gcp_*`` / ``azure_*`` / ``saas_*``
    keep those keys so MCP can resolve official icons (next-ai-style serverless
    Logical). Network / Security / Data flow are unchanged.
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
        # Preserve brand SaaS and intentional cloud icons (serverless Logical).
        existing = (cell.get("shape_type") or "").strip().lower()
        if existing.startswith("saas_"):
            continue
        if existing.startswith(("aws_", "gcp_", "azure_")):
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
        # Style/shape_type only — never resize. Compiler laid out LEAF_W×LEAF_H.
    if not changed:
        return page_xml
    return ET.tostring(tree, encoding="unicode")


# Backward-compatible alias.
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
    # Local import: view_fixers.py imports from this module, so a top-level
    # import here would be circular.
    from app.adapters.outbound.diagram.view_fixers import _is_actor_label
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
        # A "Users"/external-actor leaf is not a cloud service — Network's own
        # inventory has no real match for it, so the tier-based fallback below
        # was grabbing whatever compute entry happened to be available (a live
        # capture showed this land on Lambda). theme.py's actor_style() already
        # gives every actor kind a complete, correct, hardcoded style with no
        # MCP resolution needed at all — this function's whole job is filling
        # in *missing* icons, so an actor (which never needs one) must never
        # be a candidate for it, same as Logical already protects its own
        # actor via assign_logical_shape_types running first.
        if not existing and _is_actor_label(label):
            continue
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



def page_has_overview_panel(xml: str) -> bool:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Page"))
    except ET.ParseError:
        return False
    return any(c.get("id") == "overview_panel" for c in tree.iter("mxCell"))


def _overview_stub_body(view_title: str) -> str:
    """Audience-specific fallback overview HTML (XML-escaped attribute value)."""
    title_key = (view_title or "").strip().lower()
    if title_key == "logical":
        title = _esc("Logical architecture — system")
        return (
            f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title}&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;1. Channels and personas initiate the business journey.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;2. Owning domains handle the request through capabilities.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;3. Capabilities invoke APIs and rely on shared platform services.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;4. Generic data stores hold domain state reached via APIs.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:13px;&quot;&gt;Out of scope on this page: network topology, trust controls, and payload schemas (other tabs).&lt;/div&gt;"
        )
    if title_key == "security":
        title = _esc("Security & trust — system")
        return (
            f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title}&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;1. Outside users reach the public edge of the system.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;2. Edge protections check and filter traffic before it goes further.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;3. The system checks who someone is before sensitive work.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;4. Apps run in a private area; only allowed paths get in.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;5. The most sensitive data sits in a tighter, locked zone.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;6. Monitoring watches for abuse and raises alerts.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:13px;&quot;&gt;Out of scope here: business capabilities and data payload detail (other tabs).&lt;/div&gt;"
        )
    if title_key in {"data flow", "data_flow"}:
        title = _esc("Data journey — system")
        return (
            f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title}&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;1. Data enters from apps, partners, or batch jobs.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;2. It is cleaned or transformed in the processing step.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;3. Important records are saved in the main store (and cache if used).&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;4. Downstream apps or analytics read what they need.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:15px;&quot;&gt;5. Customer-related hops are marked as personal data where known.&lt;/div&gt;"
            "&lt;div style=&quot;font-size:13px;&quot;&gt;Out of scope here: network layout and security controls (other tabs).&lt;/div&gt;"
        )
    title = _esc(f"{view_title} overview")
    return (
        f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title}&lt;/div&gt;"
        "&lt;div style=&quot;font-size:15px;&quot;&gt;1. Review the services and groups on this page.&lt;/div&gt;"
        "&lt;div style=&quot;font-size:15px;&quot;&gt;2. Follow the relationships that matter for this view.&lt;/div&gt;"
        "&lt;div style=&quot;font-size:15px;&quot;&gt;3. Use other tabs for complementary architecture detail.&lt;/div&gt;"
    )


# Per-view Overview Panel copy the IR compiler synthesizes — single source of
# truth (IR packs no longer teach overview HTML; the compiler owns it).
_VIEW_TITLE_PREFIX: dict[str, str] = {
    "logical": "Logical architecture",
    "security": "Security & trust",
    "data flow": "Data journey",
    "data_flow": "Data journey",
    "network": "Network architecture",
}
_VIEW_LENS: dict[str, str] = {
    "logical": (
        "Business: what users and channels do · "
        "Technical: capabilities, APIs, and data stores — for Product and Engineering"
    ),
    "security": "Trust & Risk Boundary — for CISO, Security Architects and Auditors",
    "data flow": "Data Journey & Classification — for Data/Integration Engineers and Privacy Officers",
    "data_flow": "Data Journey & Classification — for Data/Integration Engineers and Privacy Officers",
    "network": "Cloud Infrastructure & Deployment — for Platform/SRE and Network Engineers",
}
_VIEW_FOOTER: dict[str, str] = {
    "logical": "Out of scope: network topology, trust controls, payloads (other tabs).",
    "security": "Out of scope: business capabilities and data payloads (other tabs).",
    "data flow": "Out of scope: network layout and security controls (other tabs).",
    "data_flow": "Out of scope: network layout and security controls (other tabs).",
    "network": "Out of scope: business capabilities, trust controls, and data payloads (other tabs).",
}

#: Cap on how many steps the numbered story (panel prose + canvas badges)
#: ever carries — matches ``apply_step_markers``' own badge cap, so neither
#: side ever has to truncate what the other already committed to.
_MAX_STORY_STEPS = 5
#: Logical needs room for Persona → portal → capability → data → SaaS.
_MAX_STORY_STEPS_LOGICAL = 6
#: Network primary path often needs edge + gateway + app + data (7 hops).
_MAX_STORY_STEPS_NETWORK = 7
#: Business-journey bullets kept short so the panel stays scannable.
_MAX_BUSINESS_STEPS = 3


def story_step_cap(view_title: str = "") -> int:
    """Single source of truth for panel tech steps and canvas badge count."""
    title_key = (view_title or "").strip().lower()
    if title_key == "logical":
        return _MAX_STORY_STEPS_LOGICAL
    if title_key == "network":
        return _MAX_STORY_STEPS_NETWORK
    return _MAX_STORY_STEPS


def _strip_annotation_tag(label: str) -> str:
    """Drop trailing "[Tag]" annotations and a "(SAAS)" suffix for prose use."""
    label = label or ""
    # Peel nested/stacked brackets: "Aurora [Multi-AZ] [PHI]" or "X [Multi-AZ [PHI]]".
    for _ in range(4):
        nxt = re.sub(r"\s*\[[^\]]*\]\s*$", "", label).strip()
        if nxt == label:
            break
        label = nxt
    label = re.sub(r"\s*\(SAAS\)\s*$", "", label, flags=re.IGNORECASE).strip()
    return label


class _StoryStep:
    """One numbered step in a page's primary-flow story — both the prose
    ``synthesize_ir_overview_body`` writes into the panel and the badge
    ``derive_story_markers`` places on the canvas come from the same instance,
    which is what guarantees panel step *i* and canvas badge *i* always name
    the same cell (see both functions' docstrings)."""

    __slots__ = ("target_id", "text")

    def __init__(self, target_id: str, text: str) -> None:
        self.target_id = target_id
        self.text = text


def _ir_story_steps(xml: str, *, max_steps: int | None = None) -> list[_StoryStep]:
    """The page's primary-flow story, deduped and capped — shared by every
    consumer that numbers the same story, so there is exactly one place that
    decides which edges are "step 1, 2, 3...".
    """
    cap = max_steps if max_steps is not None else _MAX_STORY_STEPS
    try:
        parse_src = xml if ("<mxfile" in xml or "<mxGraphModel" in xml) else f"<root>{xml}</root>"
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return []

    cells = list(tree.iter("mxCell"))
    by_id = {c.get("id"): c for c in cells if c.get("id")}

    def _label(cell_id: str | None) -> str:
        cell = by_id.get(cell_id or "")
        if cell is None:
            return ""
        return _strip_annotation_tag((cell.get("value") or "").strip())

    def _parent_chain(cell_id: str) -> str:
        parts: list[str] = []
        cur = by_id.get(cell_id)
        seen: set[str] = set()
        while cur is not None:
            cid = (cur.get("id") or "").lower()
            if not cid or cid in seen:
                break
            seen.add(cid)
            parts.append(cid)
            parts.append((cur.get("value") or "").lower())
            pid = cur.get("parent")
            cur = by_id.get(pid) if pid else None
        return " ".join(parts)

    def _journey_rank(cell_id: str) -> int:
        """Lower = earlier in the L→R / edge→data journey."""
        cell = by_id.get(cell_id)
        if cell is None:
            return 99
        parent = (cell.get("parent") or "").lower()
        domain_order = {
            "domain_experience": 0, "domain_core": 1, "domain_commerce": 1,
            "domain_data": 2, "domain_platform": 3, "domain_saas": 4,
            "actors_left": -1,
        }
        if parent in domain_order:
            return domain_order[parent]
        val = ((cell.get("value") or "") + (cell.get("style") or "") + (cell.get("shape_type") or "")).lower()
        st = (cell.get("shape_type") or "").lower()
        # Flat Logical: leaves under cloud_boundary or parent=1 — rank by shape/label.
        if parent in {"1", "cloud_boundary", "actors_left"} or parent.startswith("actors"):
            if parent in {"1", "actors_left"} or parent.startswith("actors"):
                if "aws4.user" in val or any(
                    t in val for t in (
                        "user", "patient", "clinician", "persona", "customer",
                        "employee", "partner",
                    )
                ):
                    if not any(t in val for t in ("okta", "entra", "cognito", "auth0", "idp")):
                        return -2
                if any(t in val for t in ("okta", "entra", "cognito", "auth0", "idp", "identity")) and not st.startswith("aws_"):
                    return -1
                # Egress SaaS on the right — late in the journey.
                if st.startswith("saas_") or any(t in val for t in ("stripe", "twilio", "fhir", "workday", "saas")):
                    return 6
            if st.startswith("logical_portal") or "portal" in val or "react" in val or "chat" in val:
                return 0
            if any(st.startswith(p) for p in ("aws_dynamodb", "aws_rds", "aws_aurora", "aws_s3")) or "logical_datastore" in st:
                return 2
            if st.startswith(("aws_", "gcp_", "azure_")) or "logical_capability" in st or "logical_api" in st:
                return 1
            if parent == "cloud_boundary":
                return 1
            if parent == "1" and cell.get("vertex") == "1":
                return 0
        chain = _parent_chain(cell_id)
        if "edge" in chain or any(t in chain for t in ("route 53", "cloudfront", "waf", "cdn")):
            return 0
        if "gateway" in chain or "api gateway" in chain or re.search(r"\balb\b|\bnlb\b", chain):
            return 1
        if "public" in chain and "subnet" in chain:
            return 2
        if "app" in chain and "subnet" in chain:
            return 3
        if "data" in chain and "subnet" in chain:
            return 4
        if parent.startswith("zone_"):
            return 2
        if parent.startswith("phase_"):
            phase = {
                "phase_ingress": 0, "phase_process": 1, "phase_store": 2,
                "phase_consume": 3, "phase_archive": 4,
            }
            return phase.get(parent, 50)
        return 50

    edges = [c for c in cells if c.get("edge") == "1" and c.get("source") and c.get("target")]
    # Prefer labelled primary edges that advance the journey (earlier → later rank).
    edges.sort(key=lambda e: (
        0 if (e.get("value") or "").strip() else 1,
        0 if (e.get("primary") or "1") not in {"0", "false", "False"} else 1,
        _journey_rank(e.get("source") or ""),
        _journey_rank(e.get("target") or ""),
    ))
    steps: list[_StoryStep] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in edges:
        if len(steps) >= cap:
            break
        src_id, tgt_id = edge.get("source") or "", edge.get("target") or ""
        src, tgt = _label(src_id), _label(tgt_id)
        if not src or not tgt or (src, tgt) in seen_pairs:
            continue
        src_r, tgt_r = _journey_rank(src_id), _journey_rank(tgt_id)
        # Skip clear reverse hops once we already have a forward story.
        if steps and tgt_r < src_r - 1:
            continue
        seen_pairs.add((src, tgt))
        edge_label = re.sub(r"<[^>]+>", "", (edge.get("value") or "")).strip()
        text = f"{src} → {tgt} ({edge_label})." if edge_label else f"{src} connects to {tgt}."
        steps.append(_StoryStep(target_id=tgt_id, text=text))
    return steps


def _read_business_notes(xml: str) -> list[str]:
    """The LLM's own ``business_notes`` bullets, stamped by the compiler onto
    the page root (id="1") as JSON — see ``CellWriter.set_business_notes``.

    ``synthesize_ir_overview_body`` only ever receives already-compiled XML,
    never the live IR object, so this is the only way the LLM's own wording
    (definitions, "why it matters," per-request specifics) reaches the panel
    instead of falling back to a generic heuristic guess at it. Returns ``[]``
    on anything missing/malformed — every caller already has a fallback.
    """
    try:
        parse_src = xml if ("<mxfile" in xml or "<mxGraphModel" in xml) else f"<root>{xml}</root>"
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return []
    for cell in tree.iter("mxCell"):
        if cell.get("id") == "1":
            raw = cell.get("business_notes")
            if not raw:
                return []
            try:
                notes = json.loads(raw)
            except (ValueError, TypeError):
                return []
            if not isinstance(notes, list):
                return []
            return [str(n).strip() for n in notes if str(n).strip()]
    return []


def _logical_business_steps(xml: str) -> list[str]:
    """Business-readable journey from actual Logical leaves — not corridor fluff.

    Supports nested ``domain_*`` parents (legacy) and flat Logical (parent ``1`` /
    ``cloud_boundary``) by grouping on ``shape_type`` + label heuristics.
    """
    try:
        parse_src = xml if ("<mxfile" in xml or "<mxGraphModel" in xml) else f"<root>{xml}</root>"
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return []

    by_parent: dict[str, list[str]] = {}
    channels: list[str] = []
    core: list[str] = []
    data: list[str] = []
    saas: list[str] = []
    platform: list[str] = []
    actors: list[str] = []
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        if "container=1" in (cell.get("style") or ""):
            continue
        cid = cell.get("id") or ""
        if cid in {"0", "1", "overview_panel", "legend_box", "cloud_boundary"} or cid.startswith("step_badge_"):
            continue
        lab = _strip_annotation_tag((cell.get("value") or "").strip())
        if not lab:
            continue
        parent = cell.get("parent") or ""
        style = (cell.get("style") or "").lower()
        st = (cell.get("shape_type") or "").lower()
        val_low = lab.lower()
        is_person = (
            "aws4.user" in style
            or any(t in val_low for t in ("persona", "user", "patient", "clinician", "customer", "employee"))
        ) and not any(t in val_low for t in ("okta", "entra", "cognito", "saas", "idp"))
        if parent == "actors_left" or cid.startswith("actors_") or (
            parent == "1" and is_person and not st
        ):
            if is_person:
                actors.append(lab)
            continue
        by_parent.setdefault(parent, []).append(lab)
        # Flat Logical buckets (also used when domain parents are gone).
        if st.startswith("logical_portal") or any(t in val_low for t in ("portal", "react", "chat ui", "client")):
            channels.append(lab)
        elif st.startswith("saas_") or "saas" in val_low or parent == "domain_saas":
            saas.append(lab)
        elif (
            any(st.startswith(p) for p in ("aws_dynamodb", "aws_rds", "aws_aurora", "aws_s3", "gcp_sql", "azure_sql"))
            or "logical_datastore" in st
            or parent == "domain_data"
        ):
            data.append(lab)
        elif "logical_observability" in st or parent == "domain_platform" or any(
            t in val_low for t in ("observab", "platform", "telemetry")
        ):
            platform.append(lab)
        elif parent in {"domain_core", "domain_commerce", "cloud_boundary"} or st.startswith(
            ("aws_", "gcp_", "azure_", "logical_")
        ):
            if "cache" not in val_low:
                core.append(lab)

    def _join(names: list[str], *, limit: int = 3) -> str:
        # Preserve order, drop dupes.
        seen: set[str] = set()
        uniq: list[str] = []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        names = uniq[:limit]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} and {names[1]}"
        return f"{', '.join(names[:-1])}, and {names[-1]}"

    steps: list[str] = []
    who = _join(actors, limit=2) or "Users"
    exp = by_parent.get("domain_experience") or channels
    if exp:
        steps.append(f"{who} reach the product via {_join(exp)}.")
    elif actors:
        steps.append(f"{who} start the journey from the left (channels follow).")

    core_apis = by_parent.get("domain_core") or by_parent.get("domain_commerce") or core
    core_apis = [n for n in core_apis if "cache" not in n.lower()] or core_apis
    if core_apis:
        steps.append(f"Core work is delivered by {_join(core_apis)}.")

    data_names = by_parent.get("domain_data") or data
    if data_names:
        sensitive = [
            _strip_annotation_tag((c.get("value") or ""))
            for c in tree.iter("mxCell")
            if re.search(r"\[(?:PHI|PII|PCI)\]", c.get("value") or "", re.I)
            and (c.get("parent") or "") in {"domain_data", "1", "cloud_boundary"}
        ]
        if sensitive:
            steps.append(
                f"System of record holds regulated data in {_join(sensitive)} "
                "(treat as PHI/PII in design reviews)."
            )
        else:
            steps.append(f"System of record: {_join(data_names)}.")

    saas_names = by_parent.get("domain_saas") or saas
    if saas_names:
        steps.append(f"External partners/products: {_join(saas_names)}.")

    plat = by_parent.get("domain_platform") or platform
    if plat:
        steps.append(f"Shared platform services: {_join(plat)}.")

    return steps[:_MAX_BUSINESS_STEPS]


def derive_story_markers(xml: str, view_title: str = "") -> list[StepMarker]:
    """Numbered canvas badges for the same story
    ``synthesize_ir_overview_body`` numbers in the panel — deterministic (no
    LLM involved), and guaranteed to agree with the panel's numbering because
    both read ``_ir_story_steps`` on the *same* xml snapshot.
    """
    cap = story_step_cap(view_title)
    return [
        StepMarker(step_number=i, cell_id=step.target_id)
        for i, step in enumerate(_ir_story_steps(xml, max_steps=cap), start=1)
    ]


#: Corridor id prefix per view — matches each ``CorridorIR`` subclass's own
#: fixed vocabulary (``_ROW_IDS`` in logical.py/security.py/data_flow.py).
#: Network has no corridor row at all, so it is deliberately absent here.
_CORRIDOR_PREFIX_BY_VIEW: dict[str, tuple[str, str]] = {
    "logical": ("domain_", "domain"),
    "security": ("zone_", "zone"),
    "data flow": ("phase_", "phase"),
    "data_flow": ("phase_", "phase"),
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


#: Flow.classification values that count as "sensitive" for the facts line.
_SENSITIVE_CLASSIFICATIONS = frozenset({"pii", "pci", "confidential"})
#: Flow.protection values that count as "protected" for the facts line.
_PROTECTED_VALUES = frozenset({"tls", "mtls"})


def _key_facts_line(xml: str, title_key: str) -> str | None:
    """One structural summary line derived from cell counts and stamped attrs."""
    try:
        parse_src = xml if ("<mxfile" in xml or "<mxGraphModel" in xml) else f"<root>{xml}</root>"
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return None

    cells = list(tree.iter("mxCell"))
    edges = [c for c in cells if c.get("edge") == "1" and c.get("source") and c.get("target")]

    # Network has no corridor row — summarize hierarchy instead.
    if title_key == "network":
        def _count_prefix(*prefixes: str) -> int:
            n = 0
            for c in cells:
                cid = (c.get("id") or "").lower()
                style = c.get("style") or ""
                if "container=1" not in style and "swimlane" not in style:
                    continue
                if any(cid.startswith(p) or p in cid for p in prefixes):
                    n += 1
            return n

        vpcs = _count_prefix("vpc", "vnet")
        zones = sum(
            1 for c in cells
            if "container=1" in (c.get("style") or "")
            and re.search(r"\baz\b|availability|zone_", (c.get("id") or ""), re.I)
        )
        subnets = sum(
            1 for c in cells
            if "container=1" in (c.get("style") or "")
            and ("subnet" in (c.get("id") or "").lower() or "subnet" in (c.get("value") or "").lower())
        )
        leaves = [
            c for c in cells
            if c.get("vertex") == "1" and c.get("edge") != "1"
            and "container=1" not in (c.get("style") or "")
            and (c.get("id") or "") not in {"0", "1", "overview_panel", "legend_box"}
            and not (c.get("id") or "").startswith("step_badge_")
        ]
        parts = []
        if vpcs:
            parts.append(_plural(vpcs, "VPC"))
        if zones:
            parts.append(_plural(zones, "AZ"))
        if subnets:
            parts.append(_plural(subnets, "subnet"))
        parts.append(_plural(len(leaves), "service"))
        parts.append(_plural(len(edges), "flow"))
        protected = sum(1 for e in edges if e.get("protection") in _PROTECTED_VALUES)
        sensitive = sum(1 for e in edges if e.get("classification") in _SENSITIVE_CLASSIFICATIONS)
        if protected:
            parts.append(f"{protected}/{len(edges)} flows protected (TLS/mTLS)")
        if sensitive:
            parts.append(f"{_plural(sensitive, 'flow')} carrying PII/PCI/confidential")
        return " &#183; ".join(parts)

    prefix_info = _CORRIDOR_PREFIX_BY_VIEW.get(title_key)
    if prefix_info is None:
        return None
    prefix, noun = prefix_info
    corridors = [
        c for c in cells
        if (c.get("id") or "").startswith(prefix) and "container=1" in (c.get("style") or "")
    ]
    cloud = next((c for c in cells if (c.get("id") or "") == "cloud_boundary"), None)
    # Flat Logical: no domain_* containers — summarize services + flows (+ cloud).
    if not corridors and title_key == "logical":
        leaves = [
            c for c in cells
            if c.get("vertex") == "1" and c.get("edge") != "1"
            and "container=1" not in (c.get("style") or "")
            and (c.get("id") or "") not in {"0", "1", "overview_panel", "legend_box", "cloud_boundary"}
            and not (c.get("id") or "").startswith("step_badge_")
        ]
        critical_count = sum(1 for leaf in leaves if leaf.get("criticality") == "critical")
        sensitive_flow_count = sum(1 for edge in edges if edge.get("classification") in _SENSITIVE_CLASSIFICATIONS)
        protected_flow_count = sum(1 for edge in edges if edge.get("protection") in _PROTECTED_VALUES)
        parts: list[str] = []
        if cloud is not None:
            parts.append("1 cloud boundary")
        parts.append(_plural(len(leaves), "service"))
        parts.append(_plural(len(edges), "flow"))
        if critical_count:
            parts.append(_plural(critical_count, "business-critical service"))
        if protected_flow_count:
            parts.append(f"{protected_flow_count}/{len(edges)} flows protected (TLS/mTLS)")
        if sensitive_flow_count:
            parts.append(f"{_plural(sensitive_flow_count, 'flow')} carrying PII/PCI/confidential")
        return " &#183; ".join(parts)

    if not corridors:
        return None
    corridor_ids = {c.get("id") for c in corridors}
    shelf_count = sum(1 for c in corridors if palette.is_shelf((c.get("id") or "").lower()))
    leaves = [
        c for c in cells
        if c.get("vertex") == "1" and c.get("edge") != "1" and c.get("parent") in corridor_ids
    ]

    critical_count = sum(1 for leaf in leaves if leaf.get("criticality") == "critical")
    sensitive_flow_count = sum(1 for edge in edges if edge.get("classification") in _SENSITIVE_CLASSIFICATIONS)
    protected_flow_count = sum(1 for edge in edges if edge.get("protection") in _PROTECTED_VALUES)

    parts = [_plural(len(corridors), noun)]
    if shelf_count:
        parts[-1] += f" ({_plural(shelf_count, 'shelf')})"
    parts.append(_plural(len(leaves), "service"))
    parts.append(_plural(len(edges), "flow"))
    if critical_count:
        parts.append(_plural(critical_count, "business-critical service"))
    if protected_flow_count:
        parts.append(f"{protected_flow_count}/{len(edges)} flows protected (TLS/mTLS)")
    if sensitive_flow_count:
        parts.append(f"{_plural(sensitive_flow_count, 'flow')} carrying PII/PCI/confidential")
    return " &#183; ".join(parts)


def synthesize_ir_overview_body(xml: str, view_title: str, system_title: str = "") -> str:
    """Build a request-specific Overview Panel body from an IR-compiled page.

    For Logical: business journey + numbered technical flow (same numbers as
    canvas badges) + facts. Every structural block carries ``data-section`` so
    ``_trim_overview_panel_value`` cannot erase the badge-correlated steps.
    """
    title_key = (view_title or "").strip().lower()
    cap = story_step_cap(view_title)
    tech_steps = _ir_story_steps(xml, max_steps=cap)
    if not tech_steps and title_key != "logical":
        return _overview_stub_body(view_title)

    prefix = _VIEW_TITLE_PREFIX.get(title_key, view_title or "Architecture")
    title = _esc(f"{prefix} — {system_title}" if system_title else prefix)
    lens = _esc(_VIEW_LENS.get(title_key, ""))
    footer = _esc(_VIEW_FOOTER.get(title_key, ""))

    parts = [f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title}&lt;/div&gt;"]
    if lens:
        parts.append(
            "&lt;div data-section=&quot;lens&quot; style=&quot;font-size:13px;font-style:italic;color:#8A6D3B;&quot;&gt;"
            f"{lens}&lt;/div&gt;"
        )

    # How to read: badges on canvas ↔ numbered steps in this panel.
    if tech_steps:
        n = len(tech_steps)
        parts.append(
            "&lt;div data-section=&quot;howto&quot; style=&quot;font-size:12px;color:#6B7280;margin-top:4px;&quot;&gt;"
            f"Orange badges 1–{n} on the diagram match the numbered steps below — "
            "follow them left-to-right for the primary path."
            "&lt;/div&gt;"
        )
        # One-line purpose from the first hop.
        first = tech_steps[0].text.rstrip(".")
        parts.append(
            "&lt;div data-section=&quot;purpose&quot; style=&quot;font-size:13px;margin-top:2px;&quot;&gt;"
            f"Primary path starts: {_esc(first)}."
            "&lt;/div&gt;"
        )

    if title_key == "logical":
        biz = _read_business_notes(xml) or _logical_business_steps(xml)
        if biz:
            parts.append(
                "&lt;div data-section=&quot;biz-header&quot; style=&quot;font-size:13px;font-weight:bold;margin-top:6px;&quot;&gt;"
                "Business journey&lt;/div&gt;"
            )
            for text in biz:
                parts.append(
                    "&lt;div data-section=&quot;biz&quot; style=&quot;font-size:14px;&quot;&gt;"
                    f"&#8226; {_esc(text)}&lt;/div&gt;"
                )
        if tech_steps:
            parts.append(
                "&lt;div data-section=&quot;tech-header&quot; style=&quot;font-size:13px;font-weight:bold;margin-top:6px;&quot;&gt;"
                "Technical flow (badges)&lt;/div&gt;"
            )
            for i, step in enumerate(tech_steps, start=1):
                parts.append(
                    "&lt;div data-section=&quot;tech&quot; style=&quot;font-size:14px;&quot;&gt;"
                    f"{i}. {_esc(step.text)}&lt;/div&gt;"
                )
        if not biz and not tech_steps:
            return _overview_stub_body(view_title)
    else:
        if not tech_steps:
            return _overview_stub_body(view_title)
        parts.append(
            "&lt;div data-section=&quot;tech-header&quot; style=&quot;font-size:13px;font-weight:bold;margin-top:4px;&quot;&gt;"
            "Primary path (badges)&lt;/div&gt;"
        )
        for i, step in enumerate(tech_steps, start=1):
            parts.append(
                "&lt;div data-section=&quot;tech&quot; style=&quot;font-size:15px;&quot;&gt;"
                f"{i}. {_esc(step.text)}&lt;/div&gt;"
            )
        if title_key == "network":
            biz = _read_business_notes(xml)
            if biz:
                parts.append(
                    "&lt;div data-section=&quot;biz-header&quot; style=&quot;font-size:13px;font-weight:bold;margin-top:6px;&quot;&gt;"
                    "Business impact&lt;/div&gt;"
                )
                for text in biz:
                    parts.append(
                        "&lt;div data-section=&quot;biz&quot; style=&quot;font-size:14px;&quot;&gt;"
                        f"&#8226; {_esc(text)}&lt;/div&gt;"
                    )

    facts = _key_facts_line(xml, title_key)
    if facts:
        parts.append(
            "&lt;div data-section=&quot;facts&quot; style=&quot;font-size:13px;color:#555555;margin-top:6px;&quot;&gt;"
            f"Key facts: {_esc(facts)}&lt;/div&gt;"
        )
    if footer:
        parts.append(
            "&lt;div data-section=&quot;footer&quot; style=&quot;font-size:12px;color:#6B7280;margin-top:4px;&quot;&gt;"
            f"{footer}&lt;/div&gt;"
        )
    return "".join(parts)


def _inject_overview_panel(xml: str, view_title: str, body: str) -> str:
    """Shared plumbing for both fallback paths below: parse, find <root>, append
    a fresh overview_panel cell with the given (already-escaped) HTML body."""
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
    panel = ET.fromstring(
        f'<mxCell id="overview_panel" value="{body}" '
        'style="rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;'
        'spacingLeft=16;spacingTop=14;fillColor=#FFF8E1;strokeColor=#F5A623;strokeWidth=2;fontSize=15;" '
        'vertex="1" parent="1">'
        '<mxGeometry x="980" y="40" width="420" height="220" as="geometry"/></mxCell>'
    )
    root.append(panel)
    return ET.tostring(tree, encoding="unicode")


def ensure_minimal_overview_panel(xml: str, view_title: str) -> str:
    """If overview_panel is missing, add a short generic stub titled for this view."""
    if page_has_overview_panel(xml):
        return ensure_diagram_document(xml, view_title)
    return _inject_overview_panel(xml, view_title, _overview_stub_body(view_title))


def ensure_ir_overview_panel(xml: str, view_title: str, system_title: str = "") -> str:
    """If overview_panel is missing, add a request-specific panel synthesized
    from this IR-compiled page's own graph (see ``synthesize_ir_overview_body``)
    rather than the generic stub — the IR compiler never emits its own panel,
    so this is the narrative content an IR-compiled page actually ships with."""
    if page_has_overview_panel(xml):
        return ensure_diagram_document(xml, view_title)
    return _inject_overview_panel(xml, view_title, synthesize_ir_overview_body(xml, view_title, system_title))


def refresh_ir_overview_panel(xml: str, view_title: str, system_title: str = "") -> str:
    """Rewrite overview_panel HTML from the *current* graph (post-repair).

    Keeps panel steps and canvas badges in sync when edges/cells changed during
    repair. Creates the panel if missing.
    """
    body = synthesize_ir_overview_body(xml, view_title, system_title)
    if not page_has_overview_panel(xml):
        return _inject_overview_panel(xml, view_title, body)
    trimmed = ensure_diagram_document(xml or "", view_title)
    try:
        tree = ET.fromstring(trimmed)
    except ET.ParseError:
        return trimmed
    # synthesize emits attribute-escaped HTML (&lt;div…); Element.set wants
    # decoded markup (ET re-escapes on serialize).
    decoded = html.unescape(body)
    for cell in tree.iter("mxCell"):
        if cell.get("id") == "overview_panel":
            cell.set("value", decoded)
            break
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


