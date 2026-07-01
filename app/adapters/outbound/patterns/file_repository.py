"""File-based pattern repository — reads architecture patterns from JSON files.

Each platform subdirectory (aws/, gcp/, azure/) contains one JSON file per pattern.
This replaces the old hardcoded ARCHITECTURE_PATTERNS tuple.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.domain.entities.patterns import ArchitecturePattern
from app.domain.ports.pattern_port import PatternRepository
from app.adapters.outbound.config.env_config import get_config

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _pattern_from_dict(data: dict[str, Any]) -> ArchitecturePattern:
    """Convert a JSON dict to an ArchitecturePattern."""
    return ArchitecturePattern(
        id=data["id"],
        name=data["name"],
        summary=data["summary"],
        keywords=tuple(data.get("keywords", [])),
        platforms=tuple(data.get("platforms", [])),
        flow=tuple(data.get("flow", [])),
        services={k: tuple(v) for k, v in data.get("services", {}).items()},
        best_practices=tuple(data.get("best_practices", [])),
    )


def load_all_patterns(data_dir: Path | None = None) -> list[ArchitecturePattern]:
    """Load all patterns from JSON files in the data directory."""
    if data_dir is None:
        data_dir = get_config().patterns_data_dir()

    if not data_dir.exists():
        return _builtin_patterns()

    patterns: list[ArchitecturePattern] = []
    for fpath in sorted(data_dir.glob("*.json")):
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    patterns.append(_pattern_from_dict(item))
            else:
                patterns.append(_pattern_from_dict(data))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            import warnings
            warnings.warn(f"Skipping pattern file {fpath.name}: {exc}")

    if not patterns:
        return _builtin_patterns()
    return patterns


def _builtin_patterns() -> list[ArchitecturePattern]:
    """Fallback built-in patterns if no data files exist."""
    return [
        ArchitecturePattern(
            id="three_tier_web",
            name="Highly Available 3-Tier Web Application",
            summary="Internet-facing web app: DNS + CDN + WAF at the edge, a load balancer in public subnets, stateless compute in private subnets, and a managed relational database in isolated data subnets, spread across two Availability Zones.",
            keywords=("web app", "web application", "3-tier", "three tier", "highly available", "ha", "load balancer", "alb", "frontend", "backend", "website", "scalable web"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Users", "DNS", "CDN", "WAF", "Load Balancer", "Compute (private)", "Cache", "Database (data tier)"),
            services={
                "aws": ("user_client", "aws_route53", "aws_cloudfront", "aws_waf", "aws_alb", "aws_ecs", "aws_elasticache", "aws_aurora", "aws_cloudwatch"),
                "gcp": ("user_client", "gcp_cloud_dns", "gcp_cdn", "gcp_cloud_armor", "gcp_load_balancing", "gcp_compute_engine", "gcp_memorystore", "gcp_cloud_sql", "gcp_cloud_monitoring"),
                "azure": ("user_client", "azure_dns", "azure_front_door", "azure_application_gateway", "azure_load_balancer", "azure_app_service", "azure_cache_redis", "azure_sql_db", "azure_monitor"),
            },
            best_practices=(
                "Spread compute and database across 2+ Availability Zones for HA.",
                "Keep databases in private/isolated data subnets — never public.",
                "Terminate TLS and filter traffic at the WAF/CDN edge.",
                "Put a cache (Redis) between compute and the database to reduce read load.",
            ),
        ),
        ArchitecturePattern(
            id="serverless_api",
            name="Serverless REST API",
            summary="Fully managed API: API Gateway fronts serverless functions that read/write a NoSQL table, with object storage for assets and centralized logging — no servers to manage and pay-per-request scaling.",
            keywords=("serverless", "lambda", "functions", "api", "rest api", "api gateway", "pay per request", "nosql", "dynamodb", "event", "managed"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Users", "API Gateway", "Function (compute)", "NoSQL DB", "Object Storage", "Monitoring"),
            services={
                "aws": ("user_client", "aws_api_gateway", "aws_lambda", "aws_dynamodb", "aws_s3", "aws_cloudwatch"),
                "gcp": ("user_client", "gcp_api_gateway", "gcp_cloud_functions", "gcp_firestore", "gcp_cloud_storage", "gcp_cloud_monitoring"),
                "azure": ("user_client", "azure_api_gateway", "azure_function", "azure_cosmos_db", "azure_blob_storage", "azure_monitor"),
            },
            best_practices=(
                "Use API Gateway throttling + auth (JWT/IAM) at the edge.",
                "Prefer single-table NoSQL design for predictable low-latency reads.",
                "Emit structured logs and traces to the monitoring service.",
            ),
        ),
        ArchitecturePattern(
            id="event_driven",
            name="Event-Driven / Asynchronous Processing",
            summary="Producers publish events to a queue/topic; decoupled consumers process them asynchronously and persist results. Buffering smooths spikes and isolates failures between producers and consumers.",
            keywords=("event driven", "event-driven", "asynchronous", "async", "queue", "message", "pub/sub", "pubsub", "sqs", "kafka", "streaming", "decoupled", "worker"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Producer", "Queue / Topic", "Worker (consumer)", "Database", "Monitoring"),
            services={
                "aws": ("aws_lambda", "aws_sqs", "aws_sns", "aws_ecs", "aws_dynamodb", "aws_cloudwatch"),
                "gcp": ("gcp_cloud_functions", "gcp_pubsub", "gcp_compute_engine", "gcp_firestore", "gcp_cloud_monitoring"),
                "azure": ("azure_function", "azure_service_bus", "azure_event_grid", "azure_cosmos_db", "azure_monitor"),
            },
            best_practices=(
                "Add a dead-letter queue for messages that repeatedly fail.",
                "Make consumers idempotent — events may be delivered more than once.",
                "Scale workers independently from producers.",
            ),
        ),
        ArchitecturePattern(
            id="data_analytics",
            name="Data Lake & Analytics Pipeline",
            summary="Raw data lands in object storage, is transformed by an ETL/processing job, loaded into a data warehouse, and served to BI/analytics consumers. Encryption protects data at rest throughout.",
            keywords=("data pipeline", "etl", "data lake", "analytics", "warehouse", "redshift", "bigquery", "synapse", "ingest", "transform", "reporting", "bi", "data"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Source", "Object Storage (lake)", "ETL / Processing", "Data Warehouse", "Analytics / BI", "Encryption"),
            services={
                "aws": ("aws_s3", "aws_lambda", "aws_redshift", "aws_athena", "aws_kms", "aws_cloudwatch"),
                "gcp": ("gcp_cloud_storage", "gcp_dataflow", "gcp_bigquery", "gcp_cloud_kms", "gcp_cloud_monitoring"),
                "azure": ("azure_blob_storage", "azure_data_factory", "azure_synapse", "azure_key_vault", "azure_monitor"),
            },
            best_practices=(
                "Partition the lake by date/source for efficient scans.",
                "Encrypt data at rest with a managed KMS/Key Vault key.",
                "Keep raw, curated, and serving layers separated.",
            ),
        ),
        ArchitecturePattern(
            id="microservices_k8s",
            name="Microservices on Managed Kubernetes",
            summary="Containerized microservices run on a managed Kubernetes cluster behind an ingress/load balancer, backed by a managed database and a container registry, with centralized observability.",
            keywords=("microservices", "kubernetes", "k8s", "containers", "eks", "gke", "aks", "cluster", "docker", "ingress", "service mesh", "pods"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Users", "Ingress / Load Balancer", "Kubernetes Cluster", "Container Registry", "Database", "Monitoring"),
            services={
                "aws": ("user_client", "aws_alb", "aws_eks", "aws_ecr", "aws_rds", "aws_cloudwatch"),
                "gcp": ("user_client", "gcp_load_balancing", "gcp_kubernetes_engine", "gcp_container_registry", "gcp_cloud_sql", "gcp_cloud_monitoring"),
                "azure": ("user_client", "azure_application_gateway", "azure_aks", "azure_container_registry", "azure_sql_db", "azure_monitor"),
            },
            best_practices=(
                "Use private node pools; expose only the ingress publicly.",
                "Store images in a private registry and scan them on push.",
                "Export metrics/logs/traces to a central observability stack.",
            ),
        ),
        ArchitecturePattern(
            id="static_site_cdn",
            name="Static Site / SPA with CDN",
            summary="A static website or single-page app served from object storage through a global CDN with DNS and TLS, optionally calling a serverless API for dynamic data.",
            keywords=("static site", "static website", "spa", "single page", "cdn", "jamstack", "frontend hosting", "s3 website", "cloudfront", "hosting"),
            platforms=("aws", "gcp", "azure", "multi"),
            flow=("Users", "DNS", "CDN", "Object Storage (static)", "Serverless API"),
            services={
                "aws": ("user_client", "aws_route53", "aws_cloudfront", "aws_s3", "aws_api_gateway", "aws_lambda"),
                "gcp": ("user_client", "gcp_cloud_dns", "gcp_cdn", "gcp_cloud_storage", "gcp_api_gateway", "gcp_cloud_functions"),
                "azure": ("user_client", "azure_dns", "azure_front_door", "azure_blob_storage", "azure_api_gateway", "azure_function"),
            },
            best_practices=(
                "Serve everything over HTTPS with a CDN-managed certificate.",
                "Cache static assets aggressively; version filenames for cache-busting.",
            ),
        ),
    ]


class FilePatternRepository(PatternRepository):
    """Reads architecture patterns from JSON data files."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._patterns = load_all_patterns(data_dir)

    def get_all(self) -> list[ArchitecturePattern]:
        return list(self._patterns)

    def get_by_id(self, pattern_id: str) -> ArchitecturePattern | None:
        for p in self._patterns:
            if p.id == pattern_id:
                return p
        return None

    def find_matching(self, prompt: str, platform: str = "aws", *, limit: int = 2, min_score: int = 1) -> list[ArchitecturePattern]:
        prompt_lower = (prompt or "").lower()
        prompt_tokens = _tokenize(prompt)
        platform = (platform or "aws").lower()

        scored: list[tuple[int, ArchitecturePattern]] = []
        for pattern in self._patterns:
            if platform not in pattern.platforms and "multi" not in pattern.platforms:
                continue
            score = 0
            for kw in pattern.keywords:
                if " " in kw:
                    if kw in prompt_lower:
                        score += 3
                elif kw in prompt_tokens:
                    score += 2
            if score >= min_score:
                scored.append((score, pattern))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]


def _resolve_services(pattern: ArchitecturePattern, platform: str) -> tuple[str, ...]:
    platform = (platform or "aws").lower()
    if platform in pattern.services:
        return pattern.services[platform]
    return pattern.services.get("aws", ())


def format_patterns_for_prompt(patterns: list[ArchitecturePattern], platform: str = "aws") -> str:
    """Render matched patterns as a compact blueprint block for the system prompt."""
    if not patterns:
        return ""

    blocks: list[str] = [
        "## Reference patterns (reuse these proven blueprints)",
        "The user's request matches the following curated architecture pattern(s). "
        "Reuse the topology, ordering, and best practices. Adapt names/services to the "
        "request, but keep the proven structure and the left-to-right flow ordering.",
    ]
    for pattern in patterns:
        services = _resolve_services(pattern, platform)
        blocks.append(
            f"\n### {pattern.name}\n"
            f"{pattern.summary}\n"
            f"- Flow (assign step_number in this order): {' → '.join(pattern.flow)}\n"
            f"- Suggested shape_type keys ({platform}): {', '.join(services)}\n"
            f"- Best practices:\n"
            + "\n".join(f"  - {bp}" for bp in pattern.best_practices)
        )
    return "\n".join(blocks)
