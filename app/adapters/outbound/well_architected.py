from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.adapters.outbound.diagram.xml_tools import find_service_keys
from app.adapters.outbound.waf_topology import read_topology

if TYPE_CHECKING:
    from app.adapters.outbound.waf_topology import Node, Topology

PILLARS: dict[str, dict[str, Any]] = {
    "security": {
        "name": "Security",
        "description": "Protect data, systems, and assets through access control, encryption, and threat detection",
    },
    "reliability": {
        "name": "Reliability",
        "description": "Ensure workloads operate correctly and consistently through resilience and recovery",
    },
    "performance": {
        "name": "Performance Efficiency",
        "description": "Use resources efficiently to meet system requirements and maintain efficiency as demand changes",
    },
    "cost_optimization": {
        "name": "Cost Optimization",
        "description": "Run systems to deliver business value at the lowest price point",
    },
    "operational_excellence": {
        "name": "Operational Excellence",
        "description": "Run and monitor systems to deliver business value and continually improve supporting processes",
    },
    "sustainability": {
        "name": "Sustainability",
        "description": "Minimize environmental impact through efficient resource usage",
    },
}

#: Mirrors config/references/{aws_stencils,gcp-icons-common,azure-icons-common}.txt's
#: current shape_type vocabulary — same drift risk, and the same disease, as
#: compliance.py's _SERVICE_CATEGORIES and pricing.json's cloud_services:
#: confirmed live this used aws_alb/aws_elb/aws_nlb/azure_lb/azure_agw/
#: gcp_lb/azure_blob/azure_aci/gcp_storage, none of which exist in any
#: current catalog, so a diagram's real load balancer, storage account, or
#: container-instances leaf silently never counted as "good practice
#: detected" for any pillar. See tests/test_well_architected.py's
#: CatalogDriftTests, which fails loudly the next time a catalog changes
#: without these being updated alongside it.
SECURITY_SERVICES: set[str] = {
    "aws_waf", "aws_kms", "aws_secrets_manager", "aws_cognito", "aws_cloudtrail",
    "aws_shield", "aws_guardduty", "aws_iam", "aws_directory_service", "aws_config",
    "azure_key_vault", "azure_entra_id", "azure_firewall",
    "azure_application_gateway", "azure_front_door",
    "gcp_cloud_armor", "gcp_iam", "gcp_secret_manager", "gcp_cloud_kms", "gcp_identity_platform",
}

RELIABILITY_SERVICES: set[str] = {
    "aws_application_load_balancer", "aws_network_load_balancer", "aws_route53",
    "aws_ec2", "aws_rds", "aws_aurora", "aws_backup", "aws_dms",
    "azure_load_balancer", "azure_application_gateway", "azure_dns",
    "azure_sql_database", "azure_cosmos_db",
    "gcp_cloud_load_balancing", "gcp_cloud_dns", "gcp_cloud_sql", "gcp_spanner",
}

PERFORMANCE_SERVICES: set[str] = {
    "aws_cloudfront", "aws_elasticache", "aws_ecs", "aws_lambda", "aws_fargate",
    "azure_front_door", "azure_redis", "azure_functions", "azure_container_instances",
    "gcp_cloud_cdn", "gcp_cloud_run", "gcp_cloud_functions", "gcp_memorystore",
}

COST_SERVICES: set[str] = {
    "aws_lambda", "aws_s3", "aws_ecs", "aws_dynamodb", "aws_fargate",
    "azure_functions", "azure_storage_account", "azure_container_instances",
    "gcp_cloud_run", "gcp_cloud_functions", "gcp_cloud_storage",
}

OPS_SERVICES: set[str] = {
    "aws_cloudwatch", "aws_cloudtrail", "aws_xray", "aws_config",
    "azure_monitor",
    "gcp_cloud_monitoring", "gcp_cloud_logging",
}

SUSTAINABILITY_SERVICES: set[str] = {
    "aws_lambda", "aws_ecs", "aws_fargate",
    "azure_functions", "azure_container_instances",
    "gcp_cloud_run", "gcp_cloud_functions",
}

PILLAR_SERVICE_MAP: dict[str, tuple[set[str], str, str]] = {
    "security": (SECURITY_SERVICES, "Ensure data protection and access control", "security"),
    "reliability": (RELIABILITY_SERVICES, "Design for failure and automatic recovery", "reliability"),
    "performance": (PERFORMANCE_SERVICES, "Optimize for speed and scale", "performance"),
    "cost_optimization": (COST_SERVICES, "Minimize waste and maximize value", "cost_optimization"),
    "operational_excellence": (OPS_SERVICES, "Monitor, log, and continuously improve", "operational_excellence"),
    "sustainability": (SUSTAINABILITY_SERVICES, "Reduce environmental impact", "sustainability"),
}


def _applies_to_platform(rec: dict[str, Any], platform: str) -> bool:
    """Whether ``rec`` is a question worth asking of a ``platform`` diagram.

    A recommendation is only answerable if this platform *has* a service that
    could satisfy it. Without this check every rule fired on every cloud: a GCP
    diagram carrying Cloud Logging was told "No audit logging detected (AWS) —
    Enable CloudTrail", because the rule listed ``aws_cloudtrail`` and nothing
    else, so ``has_any`` was False and the finding named a service that cannot
    exist on GCP. A rule this platform cannot satisfy is *not applicable*, and
    an inapplicable rule reported as a failure is worse than silence — it
    teaches a reviewer to discount the whole report.

    ``multi`` (and anything unrecognised) keeps the cross-cloud behaviour: on a
    multi-cloud diagram every equivalent is legitimately in scope.
    """
    if platform not in ("aws", "gcp", "azure"):
        return True
    return any(s.startswith(platform + "_") for s in rec["services"])


def _precondition_met(rec: dict[str, Any], services: list[str]) -> bool:
    """Whether the diagram contains the thing this recommendation would improve.

    Advice with no precondition is a slogan. "Consider serverless for a lower
    carbon footprint" fired on every diagram without a Lambda — including one
    that was *already entirely serverless*, and one that drew no compute at
    all. Neither reader learned anything, and a panel that mixes real findings
    with horoscopes trains people to skim past both.

    ``requires`` names the substrings that have to be present for the advice to
    mean something: suggest replacing always-on compute only where there is
    always-on compute to replace. A rec with no ``requires`` is unconditional,
    which is right for the controls (a WAF is worth asking about on any
    internet-facing design).
    """
    requires = rec.get("requires") or ()
    if not requires:
        return True
    lowered = [s.lower() for s in services]
    return any(need in service for need in requires for service in lowered)


def _matches_marker(rec: dict[str, Any], services: list[str]) -> bool:
    """Whether any service *looks like* what ``rec`` is asking for.

    Exact keys are not enough, and the failure is not hypothetical: a real
    generated diagram carrying an ALB was reported as "No load balancer
    detected" — a high-severity finding that flipped the whole Reliability
    pillar to `fail` — because the model emitted ``aws_elastic_load_balancing``
    while the rule listed ``aws_application_load_balancer``. The exact-key list
    tracks the *catalog*; what arrives is whatever the model and the MCP
    resolver between them produced.

    So the expensive direction is guarded by substring markers. A false
    positive here costs a missing nudge on a diagram that already has the
    control; a false negative tells a user to add a load balancer they are
    looking at, which is the kind of wrong that makes someone close the panel
    for good.
    """
    markers = rec.get("markers") or ()
    if not markers:
        return False
    lowered = [s.lower() for s in services]
    return any(marker in service for marker in markers for service in lowered)


def _pillar_findings(pillar_key: str, services: list[str], platform: str) -> list[dict[str, str]]:
    service_set, _, _ = PILLAR_SERVICE_MAP[pillar_key]
    matched = [s for s in services if s in service_set]

    findings: list[dict[str, str]] = []

    if matched:
        findings.append({
            # Tagged so a reader (and the UI) can tell this apart from the
            # findings around it: it is what the diagram got *right*, carries
            # no recommendation, and is deliberately incapable of moving the
            # pillar's status.
            "kind": "strength",
            "severity": "low",
            "message": f"Good practices detected: {', '.join(matched[:4])}",
        })

    missing = _MISSING_RECOMMENDATIONS.get(pillar_key, [])
    for rec in missing:
        if not _applies_to_platform(rec, platform):
            continue
        if not _precondition_met(rec, services):
            continue
        # Satisfied by *any* listed equivalent, not just this platform's: a
        # diagram labelled `aws` that fronts its app with Cloud Armor has still
        # answered the WAF question, and failing it for using the "wrong"
        # vendor's control would be a house-style opinion, not a WAF finding.
        has_any = any(m in services for m in rec["services"]) or _matches_marker(rec, services)
        if not has_any:
            platform_matches = [s for s in rec["services"] if s.startswith(platform + "_")]
            findings.append({
                "severity": rec["severity"],
                "message": rec["message"],
                "recommendation": rec["recommendation"],
                # Fall back to the full cross-cloud list when the diagram's platform has no
                # exact match — better an ambiguous hint than none for the edit-plan LLM.
                "missing_services": platform_matches or rec["services"],
            })

    return findings


_MISSING_RECOMMENDATIONS: dict[str, list[dict[str, Any]]] = {
    "security": [
        {
            "severity": "high",
            # Azure has no standalone WAF icon — capability lives on
            # Application Gateway/Front Door (same reasoning as
            # compliance.py's PCI-DSS network_segmentation control).
            "services": ["aws_waf", "azure_application_gateway", "azure_front_door", "gcp_cloud_armor"],
            "markers": ("waf", "cloud_armor", "front_door", "application_gateway"),
            "message": "No WAF or web application firewall detected",
            "recommendation": "Add a WAF to protect against common web exploits",
        },
        {
            "severity": "high",
            # Was ["aws_kms", "azure_key_vault", "gcp_cloud_armor"] — gcp_cloud_armor
            # is the WAF finding above, not encryption; copy-paste error meant a
            # GCP diagram with Cloud Armor but no KMS silently passed this check,
            # and one with real KMS but no Cloud Armor never could.
            "services": ["aws_kms", "azure_key_vault", "gcp_cloud_kms"],
            "markers": ("kms", "key_vault", "keyvault"),
            "message": "No key management or encryption service detected",
            "recommendation": "Use KMS/Key Vault for encryption key management",
        },
        {
            "severity": "medium",
            "services": ["aws_secrets_manager", "azure_key_vault", "gcp_secret_manager"],
            "markers": ("secret",),
            "message": "No secrets management service detected",
            "recommendation": "Use Secrets Manager or Key Vault for credentials",
        },
    ],
    "reliability": [
        {
            "severity": "high",
            "services": [
                "aws_application_load_balancer", "aws_network_load_balancer",
                "azure_load_balancer", "azure_application_gateway", "gcp_cloud_load_balancing",
            ],
            "markers": ("load_balanc", "loadbalanc", "_alb", "_nlb", "_elb", "application_gateway"),
            "message": "No load balancer detected",
            "recommendation": "Add a load balancer for HA traffic distribution",
        },
        {
            "severity": "medium",
            "services": ["aws_route53", "azure_dns", "gcp_cloud_dns"],
            "markers": ("route53", "route_53", "_dns", "dns_"),
            "message": "No DNS routing service detected",
            "recommendation": "Use managed DNS for failover and routing",
        },
    ],
    "performance": [
        {
            "severity": "medium",
            "services": ["aws_cloudfront", "azure_front_door", "gcp_cloud_cdn"],
            "markers": ("cloudfront", "cdn", "front_door"),
            "message": "No CDN or edge caching detected",
            "recommendation": "Add a CDN for low-latency global content delivery",
        },
        {
            "severity": "medium",
            "services": ["aws_elasticache", "azure_redis", "gcp_memorystore"],
            "markers": ("elasticache", "redis", "memorystore", "cache"),
            "message": "No caching layer detected",
            "recommendation": "Use ElastiCache/Redis to reduce database load",
        },
    ],
    "cost_optimization": [
        {
            "severity": "medium",
            "services": ["aws_lambda", "azure_functions", "gcp_cloud_functions"],
            "requires": ("ec2", "virtual_machine", "compute_engine", "_vm", "instance"),
            "message": "No serverless compute detected — consider for cost savings",
            "recommendation": "Use serverless for variable workloads to reduce costs",
        },
        {
            "severity": "low",
            "services": ["aws_s3", "azure_storage_account", "gcp_cloud_storage"],
            "requires": ("rds", "aurora", "sql", "dynamodb", "database", "datastore"),
            "message": "Consider object storage for cost-effective data",
            "recommendation": "Use S3/Blob Storage for archival and static content",
        },
    ],
    "operational_excellence": [
        {
            "severity": "high",
            "services": ["aws_cloudwatch", "azure_monitor", "gcp_cloud_monitoring"],
            "markers": ("cloudwatch", "monitor", "observab"),
            "message": "No monitoring or observability service detected",
            "recommendation": "Add CloudWatch/Azure Monitor for operational visibility",
        },
        {
            # Was AWS-only, which made it unanswerable on GCP and Azure while
            # still firing there — see _applies_to_platform. Every cloud has an
            # API-audit-trail service; naming each one is what makes the rule
            # true rather than merely applicable.
            "severity": "medium",
            "services": ["aws_cloudtrail", "gcp_cloud_logging", "azure_monitor"],
            "markers": ("cloudtrail", "audit", "cloud_logging", "activity_log"),
            "message": "No API audit trail detected",
            "recommendation": "Enable CloudTrail / Cloud Audit Logs / Azure Monitor for API audit trails",
        },
    ],
    "sustainability": [
        {
            "severity": "low",
            "services": ["aws_lambda", "azure_functions", "gcp_cloud_functions"],
            "requires": ("ec2", "virtual_machine", "compute_engine", "_vm", "instance"),
            "message": "Consider serverless for lower carbon footprint",
            "recommendation": "Serverless functions reduce idle resource waste",
        },
    ],
}


#: Leaf ``shape_type`` fragments that identify a persistent datastore, used by
#: the topology checks below. Fragments rather than exact keys: the catalogs
#: carry a long tail (``aws_rds``, ``aws_aurora``, ``aws_dynamodb``,
#: ``azure_sql_database``, ``gcp_cloud_sql``, ``gcp_spanner``, …) and a check
#: that silently stopped matching when a new engine was added would be worse
#: than one that occasionally over-matches — an over-match here costs a finding
#: a reviewer can dismiss; an under-match hides an exposed database.
_DATASTORE_MARKERS: tuple[str, ...] = (
    "rds", "aurora", "dynamodb", "sql", "spanner", "cosmos", "database",
    "datastore", "bigtable", "documentdb", "redshift",
)
#: Classifications that make an unprotected flow a reportable finding.
_SENSITIVE = frozenset({"pii", "pci"})


def _is_datastore(shape_type: str) -> bool:
    lowered = (shape_type or "").lower()
    return any(marker in lowered for marker in _DATASTORE_MARKERS)


def _topology_findings(pillar_key: str, topo: "Topology") -> list[dict[str, str]]:
    """Findings that depend on how the diagram is *wired*, not what is on it.

    Each names the specific cell it is about, the way a governance finding
    does. "No WAF detected" is something a reviewer has to go and verify;
    "Primary DB sits in a public subnet" is something they can act on.

    Every check is silent when the diagram doesn't carry the structure it asks
    about — a page with no tiered subnets simply produces no tier findings,
    rather than a finding that the tiers are missing. Absence of structure is
    the presence checks' job, and saying it twice in two vocabularies is how a
    report stops being read.
    """
    findings: list[dict[str, str]] = []

    if pillar_key == "security":
        for node in topo.leaves_in_tier("public"):
            if _is_datastore(node.shape_type):
                findings.append({
                    "severity": "high",
                    "message": f"Datastore '{node.label or node.id}' sits in a public subnet",
                    "recommendation": (
                        "Move persistent data to a private/data-tier subnet reachable only "
                        "from the application tier"
                    ),
                })
        for edge in topo.edges:
            if edge.classification in _SENSITIVE and edge.protection == "none":
                source = topo.nodes.get(edge.source)
                target = topo.nodes.get(edge.target)
                names = f"{_name(source, edge.source)} → {_name(target, edge.target)}"
                findings.append({
                    "severity": "high",
                    "message": (
                        f"Flow {names} carries {edge.classification.upper()} with no "
                        "transport protection"
                    ),
                    "recommendation": "Terminate this flow over TLS (or mTLS between services)",
                })

    if pillar_key == "reliability":
        zones = topo.zone_count()
        data_leaves = [n for n in topo.leaves() if _is_datastore(n.shape_type)]
        if zones == 1 and data_leaves:
            findings.append({
                "severity": "high",
                "message": "Single availability zone — no failover target for the data tier",
                "recommendation": (
                    "Spread the workload across at least two availability zones and add a "
                    "standby/replica for the primary datastore"
                ),
            })
        elif zones > 1 and len(data_leaves) == 1:
            only = data_leaves[0]
            findings.append({
                "severity": "medium",
                "message": (
                    f"'{only.label or only.id}' is the only datastore across {zones} zones"
                ),
                "recommendation": "Add a replica or standby so a zone loss does not lose the data tier",
            })

    return findings


def _name(node: "Node | None", fallback: str) -> str:
    return (node.label or node.id) if node is not None else fallback


def _pillar_status(findings: list[dict[str, str]]) -> str:
    severities = [f.get("severity", "low") for f in findings]
    if "high" in severities:
        return "fail"
    if "medium" in severities:
        return "warn"
    return "pass"


def assess_well_architected(xml: str, platform: str = "aws") -> dict[str, Any]:
    platform_key = (platform or "aws").strip().lower()
    if platform_key not in ("aws", "gcp", "azure"):
        platform_key = "aws"
    services = find_service_keys(xml)
    topology = read_topology(xml)
    pillars_result: list[dict[str, Any]] = []
    total_pass = 0
    total_warn = 0
    total_fail = 0

    for key, pillar_info in PILLARS.items():
        # Topology first: a finding that names the offending cell is more
        # actionable than one that says a category of service is absent, and
        # ordering it first is what a reader sees before the list is truncated.
        findings = _topology_findings(key, topology)
        findings += _pillar_findings(key, services, platform_key)
        status = _pillar_status(findings)
        if status == "pass":
            total_pass += 1
        elif status == "warn":
            total_warn += 1
        else:
            total_fail += 1

        pillar_entry: dict[str, Any] = {
            "name": pillar_info["name"],
            "description": pillar_info["description"],
            "status": status,
            "findings": findings,
        }
        pillars_result.append(pillar_entry)

    pillar_count = len(PILLARS)
    if total_fail > 0:
        overall = "fail"
    elif total_warn > 0:
        overall = "warn"
    else:
        overall = "pass"

    summary_parts = []
    if total_pass > 0:
        summary_parts.append(f"{total_pass}/{pillar_count} pillars passing")
    if total_warn > 0:
        summary_parts.append(f"{total_warn} with warnings")
    if total_fail > 0:
        summary_parts.append(f"{total_fail} with critical findings")

    return {
        "overall_status": overall,
        "pillars": pillars_result,
        "summary": ", ".join(summary_parts) + ". Review recommendations for each pillar.",
        "services_analyzed": len(services),
        "platform": platform,
    }
