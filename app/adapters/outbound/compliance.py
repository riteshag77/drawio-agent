from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from app.adapters.outbound.diagram.xml_tools import find_service_keys
from app.adapters.outbound.waf_topology import Topology, read_topology
from app.adapters.outbound.llm.llm_client import call_llm, is_quota_or_rate_limit_error
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key
from app.core.logging import StepLogger

COMPLIANCE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "compliance"
LLM_PROMPT_PATH = COMPLIANCE_DIR.parent / "compliance_llm_prompt.txt"

#: Mirrors the current `shape_type` vocabulary in config/references/
#: aws_stencils.txt / gcp-icons-common.txt / azure-icons-common.txt — a leaf
#: whose key isn't in this dict silently drops out of every compliance check
#: (see _services_in_categories), so this must be kept in sync with those
#: catalogs, not with whatever the catalogs looked like when this was
#: written. Confirmed live: 72 of 134 real catalog keys (54%) were missing
#: and 29 entries referenced key names — aws_alb, azure_ad, gcp_kms among
#: them — that no longer exist in any catalog at all, silently breaking
#: entire compliance controls (see config/compliance/*.yaml's own
#: required_services, migrated in the same fix). "Generic / non-AWS actors"
#: catalog entries that represent a real component (a non-AWS database, a
#: firewall appliance) are included; pure actor placeholders (a client
#: browser, a router) are not — they're not an auditable cloud service.
_SERVICE_CATEGORIES: dict[str, str] = {
    # AWS — Compute
    "aws_lambda": "compute", "aws_ec2": "compute", "aws_ec2_instance": "compute",
    "aws_ecs": "compute", "aws_ecs_container": "compute", "aws_fargate": "compute",
    "aws_elastic_beanstalk": "compute", "aws_kubernetes": "compute",
    # AWS — Application integration
    "aws_api_gateway": "networking", "aws_eventbridge": "integration",
    "aws_sns": "integration", "aws_sqs": "integration",
    "aws_step_functions": "integration", "aws_ses": "integration",
    # AWS — Database
    "aws_dynamodb": "database", "aws_rds": "database", "aws_aurora": "database",
    "aws_elasticache": "database", "aws_redshift": "database", "aws_dms": "database",
    "aws_timestream": "database", "aws_documentdb": "database", "aws_neptune": "database",
    # AWS — Storage
    "aws_s3": "storage", "aws_efs": "storage", "aws_ebs": "storage", "aws_backup": "storage",
    "aws_storage_gateway": "storage", "aws_snowball": "storage",
    # AWS — Networking & content delivery
    "aws_cloudfront": "networking", "aws_route53": "networking",
    "aws_application_load_balancer": "networking", "aws_network_load_balancer": "networking",
    "aws_vpc": "networking", "aws_nat_gateway": "networking", "aws_internet_gateway": "networking",
    "aws_vpn_gateway": "networking", "aws_privatelink": "networking",
    "aws_endpoints": "networking", "aws_peering": "networking",
    # AWS — Security, identity & compliance
    "aws_iam": "security", "aws_cognito": "security", "aws_kms": "security",
    "aws_secrets_manager": "security", "aws_waf": "security", "aws_shield": "security",
    "aws_directory_service": "security", "aws_single_sign_on": "security",
    "aws_guardduty": "security", "aws_config": "security",
    # AWS — Management & observability
    "aws_cloudwatch": "management", "aws_cloudtrail": "management", "aws_xray": "management",
    # AWS — Analytics, streaming & ML
    "aws_kinesis": "analytics", "aws_managed_streaming_for_kafka": "analytics",
    "aws_athena": "analytics", "aws_glue": "analytics", "aws_emr": "analytics",
    "aws_sagemaker": "analytics", "aws_bedrock": "analytics", "aws_comprehend": "analytics",
    "aws_rekognition": "analytics", "aws_translate": "analytics",
    # AWS — Generic / non-AWS actors (real components only)
    "aws_generic_database": "database", "aws_generic_firewall": "security",

    # GCP — Compute & Containers
    "gcp_compute_engine": "compute", "gcp_cloud_run": "compute", "gcp_gke": "compute",
    "gcp_app_engine": "compute", "gcp_cloud_functions": "compute",
    # GCP — Networking
    "gcp_cloud_load_balancing": "networking", "gcp_cloud_cdn": "networking",
    "gcp_cloud_dns": "networking", "gcp_cloud_armor": "security", "gcp_vpc": "networking",
    "gcp_cloud_nat": "networking", "gcp_vpc_peering": "networking",
    # GCP — Data & Storage
    "gcp_cloud_storage": "storage", "gcp_cloud_sql": "database", "gcp_spanner": "database",
    "gcp_bigtable": "database", "gcp_firestore": "database", "gcp_memorystore": "database",
    "gcp_bigquery": "analytics", "gcp_pubsub": "integration", "gcp_dataflow": "analytics",
    # GCP — Security & Ops
    "gcp_iam": "security", "gcp_identity_platform": "security",
    "gcp_secret_manager": "security", "gcp_cloud_kms": "security",
    "gcp_cloud_monitoring": "management", "gcp_cloud_logging": "management",
    # GCP — AI / Other
    "gcp_vertex_ai": "analytics",

    # Azure — Compute
    "azure_app_service": "compute", "azure_functions": "compute", "azure_aks": "compute",
    "azure_vm": "compute", "azure_container_instances": "compute",
    # Azure — Networking
    "azure_load_balancer": "networking", "azure_application_gateway": "networking",
    "azure_front_door": "networking", "azure_dns": "networking",
    "azure_virtual_network": "networking", "azure_vnet_peering": "networking",
    "azure_public_ip": "networking", "azure_firewall": "networking",
    "azure_nat_gateway": "networking", "azure_vpn_gateway": "networking",
    "azure_expressroute": "networking",
    # Azure — Data & storage
    "azure_storage_account": "storage", "azure_sql_database": "database",
    "azure_cosmos_db": "database", "azure_redis": "database", "azure_postgresql": "database",
    # Azure — Integration & messaging
    "azure_service_bus": "integration", "azure_event_grid": "integration",
    "azure_logic_apps": "integration", "azure_event_hubs": "integration",
    "azure_api_management": "networking",
    # Azure — Security & ops
    "azure_key_vault": "security", "azure_entra_id": "security", "azure_monitor": "management",
}

_CATEGORY_SERVICES: dict[str, list[str]] = {}
for _key, _cat in _SERVICE_CATEGORIES.items():
    _CATEGORY_SERVICES.setdefault(_cat, []).append(_key)


@lru_cache(maxsize=1)
def _load_frameworks() -> dict[str, dict]:
    frameworks: dict[str, dict] = {}
    if not COMPLIANCE_DIR.exists():
        return frameworks
    for p in sorted(COMPLIANCE_DIR.glob("*.yaml")):
        fw_id = p.stem
        try:
            with open(p, "r", encoding="utf-8") as f:
                frameworks[fw_id] = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {p.name}: {exc}") from exc
    return frameworks


def _services_in_categories(services: list[str], categories: list[str]) -> list[str]:
    return [s for s in services if _SERVICE_CATEGORIES.get(s) in categories]


def _control_status(required_platform_services: list[str], existing_services: list[str]) -> str:
    if not required_platform_services:
        return "pass"
    found = [s for s in required_platform_services if s in existing_services]
    if found:
        return "pass"
    return "fail"


def _get_platform_services(fw_config: dict, control_id: str, platform: str) -> list[str]:
    control = fw_config.get("controls", {}).get(control_id, {})
    required = control.get("required_services", {})
    if platform in required:
        return required[platform]
    for p in ("aws", "gcp", "azure"):
        if p in required:
            return required[p]
    return []


#: Controls whose real answer is in the *wiring*, keyed by the ``topology_check``
#: a control declares in its YAML.
#:
#: Presence is a proxy, and for these controls a poor one. "Encryption in
#: Transit" was answered by "is there a load balancer on the page?" — a diagram
#: can carry an ALB and still route cardholder data over an unprotected hop, and
#: the compiled XML says so directly, because every flow carries its own
#: ``classification`` and ``protection``. "Network Segmentation" was answered by
#: "is there a WAF?" while the database sat in a public subnet.
#:
#: A check returns ``None`` when the diagram does not carry the structure to
#: answer with — an uploaded diagram that never went through this app's
#: compiler, say — and the control then falls back to the presence rule rather
#: than reporting a pass nobody verified.
def _check_transit_encryption(topo: "Topology") -> "tuple[bool, str] | None":
    classified = [
        e for e in topo.edges
        if e.classification not in ("none", "") or e.protection not in ("none", "")
    ]
    if not classified:
        return None
    unprotected = [
        e for e in classified
        if e.classification in ("pii", "pci", "confidential") and e.protection == "none"
    ]
    if not unprotected:
        return True, f"all {len(classified)} classified flow(s) carry TLS or mTLS"
    named = ", ".join(
        f"{_label(topo, e.source)} -> {_label(topo, e.target)} ({e.classification.upper()})"
        for e in unprotected[:3]
    )
    return False, f"{len(unprotected)} sensitive flow(s) with no transport protection: {named}"


def _check_segmentation(topo: "Topology") -> "tuple[bool, str] | None":
    tiers = {topo.tier_of(n.id) for n in topo.leaves()}
    if not any(tiers):
        return None
    exposed = [
        n for n in topo.leaves_in_tier("public")
        if any(m in (n.shape_type or "").lower() for m in ("rds", "sql", "dynamodb", "database", "aurora"))
    ]
    if exposed:
        named = ", ".join(_label(topo, n.id) for n in exposed[:3])
        return False, f"datastore(s) in a public subnet: {named}"
    return True, "data tier is separated from the public subnet"


def _check_multi_az(topo: "Topology") -> "tuple[bool, str] | None":
    """Availability, as the drawing states it rather than as an icon implies it.

    SOC 2's availability criterion was satisfied by "there is a load balancer
    or an RDS on the page" — true of a single-AZ deployment with nothing to
    fail over to.
    """
    zones = topo.zone_count()
    if not zones:
        return None
    if zones > 1:
        return True, f"workload spans {zones} availability zones"
    return False, "single availability zone — nothing to fail over to"


def _check_data_residency(topo: "Topology") -> "tuple[bool, str] | None":
    """Residency is about *where*, and the region is on the page.

    The rule was "does the diagram contain S3 or RDS" — which is storage, not
    residency, and passed for data sitting anywhere on earth. This reports the
    regions actually drawn so a reviewer can judge them; it cannot know which
    jurisdictions the data is allowed in, so it never fails a named region —
    it fails only when no region is stated at all, which is the one thing that
    definitely cannot be reviewed.
    """
    regions = [
        n.label.strip() for n in topo.nodes.values()
        if n.label and _REGION_PATTERN.search(n.label)
    ]
    if regions:
        return True, "region(s) stated on the diagram: " + ", ".join(sorted(set(regions))[:4])
    if not topo.nodes:
        return None
    return False, "no region is named on the diagram, so residency cannot be reviewed"


#: Region labels as every cloud writes them — "us-east-1", "europe-west4",
#: "australiaeast". Matched on the label because that is where a compiler puts
#: it (``Region.label``), and a reviewer reads the same string.
_REGION_PATTERN = re.compile(
    r"\b((us|eu|ap|sa|ca|me|af|il)-[a-z]+-\d|"
    r"(europe|asia|australia|northamerica|southamerica|africa)[- ]?[a-z]+\d?)\b",
    re.IGNORECASE,
)

_TOPOLOGY_CHECKS = {
    "transit_encryption": _check_transit_encryption,
    "segmentation": _check_segmentation,
    "multi_az": _check_multi_az,
    "data_residency": _check_data_residency,
}


def _label(topo: "Topology", node_id: str) -> str:
    node = topo.nodes.get(node_id)
    return (node.label or node.id) if node else node_id


def _analyze_rule_based(
    services: list[str], platform: str, fw_id: str, fw_config: dict,
    topo: "Topology | None" = None,
) -> dict[str, Any]:
    in_scope_categories = fw_config.get("in_scope_categories", [])
    in_scope = _services_in_categories(services, in_scope_categories)

    controls_result: list[dict[str, Any]] = []
    total_pass = 0
    total_warn = 0
    total_fail = 0

    controls = fw_config.get("controls", {})
    for ctrl_id, ctrl in controls.items():
        affected_cats = ctrl.get("affected_categories", [])
        has_affected_services = True
        if affected_cats:
            affected_exists = _services_in_categories(services, affected_cats)
            if not affected_exists:
                has_affected_services = False

        if not has_affected_services:
            continue

        # A control a *diagram* cannot answer — a breach-notification process,
        # an incident-response programme, a patching regime. These were failed
        # for the absence of an icon: a perfectly good GDPR diagram was told it
        # failed "Breach Notification" because it didn't draw GuardDuty. The
        # architecture can support such a programme but can never evidence it,
        # so the honest report is "not assessed", excluded from the pass/fail
        # tally rather than counted against the design.
        if ctrl.get("assessment") == "process":
            controls_result.append({
                "id": ctrl_id,
                "name": ctrl.get("name", ctrl_id),
                "status": "not_assessed",
                "findings": [],
                "evidence": "",
                "assessed_from": "not_assessable_from_a_diagram",
                "note": ctrl.get("recommendation", ""),
            })
            continue

        required = _get_platform_services(fw_config, ctrl_id, platform)
        status = _control_status(required, services)

        # Evidence beats proxy: when the diagram can actually answer this
        # control, that answer wins over "the right icon is somewhere on the
        # page". Only when the check abstains does presence decide.
        evidence = ""
        check = _TOPOLOGY_CHECKS.get(ctrl.get("topology_check", ""))
        if check is not None and topo is not None:
            verdict = check(topo)
            if verdict is not None:
                passed, evidence = verdict
                status = "pass" if passed else "fail"

        findings: list[dict[str, str]] = []
        if status == "fail":
            affected_list: list[str] = []
            if affected_cats:
                affected_list = _services_in_categories(services, affected_cats)
            else:
                affected_list = [s for s in services if s not in _CATEGORY_SERVICES.get("security", [])]
            description = ctrl.get("description") or f"Missing {ctrl.get('name', ctrl_id)}"
            findings.append({
                "severity": ctrl.get("severity", "medium"),
                # The evidence, when there is any, names the offending flow or
                # cell — which is the difference between a control a reviewer
                # has to go and verify and one they can act on.
                "message": f"{description} — {evidence}" if evidence else description,
                "recommendation": ctrl.get("recommendation", ""),
                "affected_services": affected_list[:10],
                "missing_services": required,
            })
            total_fail += 1
        else:
            total_pass += 1

        controls_result.append({
            "id": ctrl_id,
            "name": ctrl.get("name", ctrl_id),
            "status": status,
            "findings": findings,
            # Present only when the verdict came from the drawing rather than
            # from a service being on the page.
            "evidence": evidence,
            "assessed_from": "topology" if evidence else "service_presence",
        })

    if total_fail > 0:
        overall = "fail"
    elif total_warn > 0:
        overall = "warn"
    else:
        overall = "pass"

    summary_parts = []
    # Controls a diagram cannot answer are out of the denominator as well as out
    # of the failures: "5/6 passing" with one marked not-assessed reads as a
    # missing mark rather than as a control that was never in scope here.
    total_controls = sum(1 for c in controls_result if c["status"] != "not_assessed")
    not_assessed = len(controls_result) - total_controls
    if total_pass > 0:
        summary_parts.append(f"{total_pass}/{total_controls} controls passing")
    if total_fail > 0:
        summary_parts.append(f"{total_fail} failing")
    if not_assessed:
        summary_parts.append(f"{not_assessed} not assessable from a diagram")

    return {
        "name": fw_config.get("name", fw_id),
        "full_name": fw_config.get("full_name", ""),
        "overall_status": overall,
        "controls": controls_result,
        "services_in_scope": in_scope,
        "services_out_of_scope": [s for s in services if s not in in_scope],
        "summary": "; ".join(summary_parts) if summary_parts else "All controls pass",
    }


# ── LLM Enhancement ─────────────────────────────────────────────────────────────

class ComplianceFinding(BaseModel):
    framework: str = Field(description="Framework ID this finding applies to")
    control: str = Field(description="Name of the compliance control area")
    status: str = Field(description="pass, warn, or fail")
    message: str = Field(description="Clear description of the finding")
    affected_services: list[str] = Field(description="Service keys affected")
    recommendation: str = Field(description="Actionable remediation step")


class ComplianceLlmOutput(BaseModel):
    findings: list[ComplianceFinding] = Field(description="Compliance findings across all frameworks")


def _build_llm_prompt(services: list[str], xml: str, fw_ids: list[str], frameworks: dict[str, dict]) -> str:
    path = LLM_PROMPT_PATH
    if not path.is_file():
        return ""
    template = path.read_text(encoding="utf-8")

    fw_contexts: list[str] = []
    for fw_id in fw_ids:
        cfg = frameworks.get(fw_id, {})
        lines = [f"{cfg.get('name', fw_id)}: {cfg.get('description', '')}"]
        controls = cfg.get("controls") or {}
        for ctrl_id, ctrl in list(controls.items())[:12]:
            name = ctrl.get("name") or ctrl_id
            req = ctrl.get("required_services") or {}
            # required_services may be dict of category→list or a flat list
            if isinstance(req, dict):
                svc_bits = []
                for cat, vals in req.items():
                    if isinstance(vals, list):
                        svc_bits.append(f"{cat}: {', '.join(vals[:6])}")
                    else:
                        svc_bits.append(f"{cat}: {vals}")
                req_str = "; ".join(svc_bits) if svc_bits else "n/a"
            elif isinstance(req, list):
                req_str = ", ".join(str(x) for x in req[:10])
            else:
                req_str = str(req) if req else "n/a"
            lines.append(f"  - Control `{name}`: required → {req_str}")
        fw_contexts.append("\n".join(lines))
    framework_description = "\n\n".join(fw_contexts)

    service_list_str = "\n".join(
        f"  - {s} ({_SERVICE_CATEGORIES.get(s, 'unknown')})" for s in services
    )

    xml_snippet = xml[:3000] if len(xml) > 3000 else xml

    return template.format(
        framework_name=", ".join(frameworks.get(f, {}).get("name", f) for f in fw_ids),
        framework_full_name=" / ".join(frameworks.get(f, {}).get("full_name", "") for f in fw_ids),
        framework_description=framework_description,
        xml=xml_snippet,
        service_list=service_list_str,
    )


def _merge_llm_findings(
    rule_results: dict[str, dict], llm_findings: list[dict], frameworks: dict[str, dict]
) -> dict[str, dict]:
    for finding in llm_findings:
        fw_id = finding.get("framework", "")
        if fw_id not in rule_results:
            continue

        rule_fw = rule_results[fw_id]
        control_name = finding.get("control", "")
        status = finding.get("status", "warn")

        existing_control = None
        for c in rule_fw.get("controls", []):
            if c["name"] == control_name:
                existing_control = c
                break

        if existing_control:
            if status == "fail" and existing_control["status"] != "fail":
                existing_control["status"] = "warn"
            existing_control["findings"].append({
                "severity": status,
                "message": finding.get("message", ""),
                "recommendation": finding.get("recommendation", ""),
                "affected_services": finding.get("affected_services", []),
            })
        else:
            rule_fw.setdefault("controls", []).append({
                "id": control_name.lower().replace(" ", "_"),
                "name": control_name,
                "status": status,
                "findings": [{
                    "severity": status,
                    "message": finding.get("message", ""),
                    "recommendation": finding.get("recommendation", ""),
                    "affected_services": finding.get("affected_services", []),
                }],
            })

        if status == "fail" and rule_fw.get("overall_status") != "fail":
            rule_fw["overall_status"] = "warn"

    for fw_id in rule_results:
        fw = rule_results[fw_id]
        # Controls a diagram cannot answer are out of the denominator as well
        # as out of the failures — "5/6 passing" with one of the six marked
        # not-assessed reads as a missing mark rather than as a control that
        # was never in scope for this artifact.
        assessable = [c for c in fw.get("controls", []) if c["status"] != "not_assessed"]
        total = len(assessable)
        if total == 0:
            continue
        fails = sum(1 for c in assessable if c["status"] == "fail")
        warns = sum(1 for c in assessable if c["status"] == "warn")
        passes = total - fails - warns
        if fails > 0:
            fw["overall_status"] = "fail"
        elif warns > 0:
            fw["overall_status"] = "warn"
        else:
            fw["overall_status"] = "pass"
        fw["summary"] = f"{passes}/{total} assessable controls passing" + (f", {fails} failing" if fails else "")

    return rule_results


# ── Public API ──────────────────────────────────────────────────────────────────

def assess_compliance(
    xml: str,
    platform: str = "aws",
    frameworks: list[str] | None = None,
    provider: str | None = None,
    logger: StepLogger | None = None,
) -> dict[str, Any]:
    services = find_service_keys(xml or "")
    topo = read_topology(xml or "")
    if not services:
        return {"frameworks": {}, "services_analyzed": 0, "llm_enhanced": False}

    all_frameworks = _load_frameworks()
    if frameworks is None:
        selected = [fw for fw in all_frameworks]
    else:
        selected = [fw for fw in frameworks if fw in all_frameworks]
    platform_key = platform.strip().lower()
    if platform_key not in ("aws", "gcp", "azure"):
        platform_key = "aws"

    results: dict[str, dict] = {}
    for fw_id in selected:
        fw_config = all_frameworks[fw_id]
        results[fw_id] = _analyze_rule_based(services, platform_key, fw_id, fw_config, topo)

    llm_used = False
    if provider and selected and LLM_PROMPT_PATH.is_file():
        try:
            provider_key = normalize_provider_key(provider)
            get_provider(provider_key)

            human = _build_llm_prompt(services, xml, selected, all_frameworks)
            if human:
                if logger:
                    logger.log("compliance", "Running LLM-enhanced compliance analysis", level="info",
                               details={"frameworks": selected, "provider": provider_key})
                llm_result = call_llm(
                    "You are a precise cloud compliance assessment tool. Return findings in the exact JSON format requested.",
                    human, provider_key, ComplianceLlmOutput, logger,
                )
                if llm_result and hasattr(llm_result, "findings"):
                    raw = [f.model_dump() for f in llm_result.findings]
                    results = _merge_llm_findings(results, raw, all_frameworks)
                    llm_used = True
        except Exception as exc:
            if logger:
                details = (
                    {"quota_exceeded": True, "feature": "Compliance assessment"}
                    if is_quota_or_rate_limit_error(exc) else None
                )
                logger.log("compliance", f"LLM enhancement skipped: {exc}", level="warning", details=details)

    if logger:
        fw_summaries = {k: f"{v['overall_status']} ({v['summary']})" for k, v in results.items()}
        logger.log("compliance", f"Compliance assessment complete: {len(results)} frameworks",
                   level="success", details={"frameworks": fw_summaries, "services": len(services),
                                              "llm_enhanced": llm_used})

    return {
        "frameworks": results,
        "services_analyzed": len(services),
        "llm_enhanced": llm_used,
    }
