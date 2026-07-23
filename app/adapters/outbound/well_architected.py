from __future__ import annotations

from typing import Any

from app.adapters.outbound.diagram.xml_tools import find_service_keys

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

SECURITY_SERVICES: set[str] = {
    "aws_waf", "aws_kms", "aws_secrets_manager", "aws_cognito", "aws_cloudtrail",
    "azure_waf", "azure_key_vault", "azure_front_door",
    "gcp_cloud_armor",
}

RELIABILITY_SERVICES: set[str] = {
    "aws_elb", "aws_alb", "aws_nlb", "aws_route53", "aws_ec2", "aws_rds", "aws_aurora",
    "azure_lb", "azure_agw", "azure_dns",
    "gcp_lb", "gcp_cloud_dns",
}

PERFORMANCE_SERVICES: set[str] = {
    "aws_cloudfront", "aws_elasticache", "aws_ecs", "aws_lambda",
    "azure_front_door", "azure_redis", "azure_functions",
    "gcp_cloud_cdn", "gcp_cloud_run", "gcp_cloud_functions",
}

COST_SERVICES: set[str] = {
    "aws_lambda", "aws_s3", "aws_ecs", "aws_dynamodb",
    "azure_functions", "azure_blob",
    "gcp_cloud_run", "gcp_cloud_functions", "gcp_storage",
}

OPS_SERVICES: set[str] = {
    "aws_cloudwatch", "aws_cloudtrail",
    "azure_monitor",
    "gcp_cloud_monitoring",
}

SUSTAINABILITY_SERVICES: set[str] = {
    "aws_lambda", "aws_ecs",
    "azure_functions", "azure_aci",
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


def _pillar_findings(pillar_key: str, services: list[str]) -> list[dict[str, str]]:
    service_set, _, _ = PILLAR_SERVICE_MAP[pillar_key]
    matched = [s for s in services if s in service_set]
    unmatched = [s for s in services if s not in service_set]

    findings: list[dict[str, str]] = []

    if matched:
        findings.append({
            "severity": "low",
            "message": f"Good practices detected: {', '.join(matched[:4])}",
        })

    missing = _MISSING_RECOMMENDATIONS.get(pillar_key, [])
    for rec in missing:
        has_any = any(m in services for m in rec["services"])
        if not has_any:
            findings.append({
                "severity": rec["severity"],
                "message": rec["message"],
                "recommendation": rec["recommendation"],
            })

    return findings


_MISSING_RECOMMENDATIONS: dict[str, list[dict[str, Any]]] = {
    "security": [
        {
            "severity": "high",
            "services": ["aws_waf", "azure_waf", "gcp_cloud_armor"],
            "message": "No WAF or web application firewall detected",
            "recommendation": "Add a WAF to protect against common web exploits",
        },
        {
            "severity": "high",
            "services": ["aws_kms", "azure_key_vault", "gcp_cloud_armor"],
            "message": "No key management or encryption service detected",
            "recommendation": "Use KMS/Key Vault for encryption key management",
        },
        {
            "severity": "medium",
            "services": ["aws_secrets_manager", "azure_key_vault"],
            "message": "No secrets management service detected",
            "recommendation": "Use Secrets Manager or Key Vault for credentials",
        },
    ],
    "reliability": [
        {
            "severity": "high",
            "services": ["aws_elb", "aws_alb", "aws_nlb", "azure_lb", "azure_agw", "gcp_lb"],
            "message": "No load balancer detected",
            "recommendation": "Add a load balancer for HA traffic distribution",
        },
        {
            "severity": "medium",
            "services": ["aws_route53", "azure_dns", "gcp_cloud_dns"],
            "message": "No DNS routing service detected",
            "recommendation": "Use managed DNS for failover and routing",
        },
    ],
    "performance": [
        {
            "severity": "medium",
            "services": ["aws_cloudfront", "azure_front_door", "gcp_cloud_cdn"],
            "message": "No CDN or edge caching detected",
            "recommendation": "Add a CDN for low-latency global content delivery",
        },
        {
            "severity": "medium",
            "services": ["aws_elasticache", "azure_redis"],
            "message": "No caching layer detected",
            "recommendation": "Use ElastiCache/Redis to reduce database load",
        },
    ],
    "cost_optimization": [
        {
            "severity": "medium",
            "services": ["aws_lambda", "azure_functions", "gcp_cloud_functions"],
            "message": "No serverless compute detected — consider for cost savings",
            "recommendation": "Use serverless for variable workloads to reduce costs",
        },
        {
            "severity": "low",
            "services": ["aws_s3", "azure_blob", "gcp_storage"],
            "message": "Consider object storage for cost-effective data",
            "recommendation": "Use S3/Blob Storage for archival and static content",
        },
    ],
    "operational_excellence": [
        {
            "severity": "high",
            "services": ["aws_cloudwatch", "azure_monitor", "gcp_cloud_monitoring"],
            "message": "No monitoring or observability service detected",
            "recommendation": "Add CloudWatch/Azure Monitor for operational visibility",
        },
        {
            "severity": "medium",
            "services": ["aws_cloudtrail"],
            "message": "No audit logging detected (AWS)",
            "recommendation": "Enable CloudTrail for API audit trails",
        },
    ],
    "sustainability": [
        {
            "severity": "low",
            "services": ["aws_lambda", "azure_functions", "gcp_cloud_functions"],
            "message": "Consider serverless for lower carbon footprint",
            "recommendation": "Serverless functions reduce idle resource waste",
        },
    ],
}


def _pillar_status(findings: list[dict[str, str]]) -> str:
    severities = [f.get("severity", "low") for f in findings]
    if "high" in severities:
        return "fail"
    if "medium" in severities:
        return "warn"
    return "pass"


def assess_well_architected(xml: str, platform: str = "aws") -> dict[str, Any]:
    services = find_service_keys(xml)
    pillars_result: list[dict[str, Any]] = []
    total_pass = 0
    total_warn = 0
    total_fail = 0

    for key, pillar_info in PILLARS.items():
        findings = _pillar_findings(key, services)
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
