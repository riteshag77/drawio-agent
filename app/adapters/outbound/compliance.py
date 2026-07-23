from __future__ import annotations

from pathlib import Path
from typing import Any
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.xml_tools import find_service_keys
from app.adapters.outbound.llm.llm_client import call_llm
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key
from app.core.logging import StepLogger

COMPLIANCE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "compliance"
LLM_PROMPT_PATH = COMPLIANCE_DIR.parent / "compliance_llm_prompt.txt"

_SERVICE_CATEGORIES: dict[str, str] = {
    # AWS — Storage
    "aws_s3": "storage", "aws_ebs": "storage", "aws_efs": "storage", "aws_backup": "storage",
    # AWS — Database
    "aws_rds": "database", "aws_aurora": "database", "aws_dynamodb": "database",
    "aws_elasticache": "database", "aws_redshift": "database",
    # AWS — Compute
    "aws_ec2": "compute", "aws_ecs": "compute", "aws_eks": "compute", "aws_lambda": "compute",
    "aws_ecr": "compute",
    # AWS — Security
    "aws_kms": "security", "aws_waf": "security", "aws_secrets_manager": "security",
    "aws_cognito": "security", "aws_iam": "security", "aws_guardduty": "security",
    "aws_inspector": "security",
    # AWS — Networking
    "aws_elb": "networking", "aws_alb": "networking", "aws_nlb": "networking",
    "aws_api_gateway": "networking", "aws_cloudfront": "networking",
    "aws_route53": "networking", "aws_network_firewall": "networking", "aws_vpn": "networking",
    # AWS — Management / Logging
    "aws_cloudtrail": "management", "aws_cloudwatch": "management",
    # AWS — Integration
    "aws_sqs": "integration", "aws_sns": "integration",
    "aws_eventbridge": "integration", "aws_step_functions": "integration",
    # AWS — Analytics
    "aws_glue": "analytics", "aws_kinesis": "analytics",
    "aws_athena": "analytics", "aws_emr": "analytics",
    # GCP — Storage
    "gcp_storage": "storage", "gcp_filestore": "storage",
    # GCP — Database
    "gcp_cloud_sql": "database", "gcp_spanner": "database",
    "gcp_firestore": "database", "gcp_bigtable": "database",
    # GCP — Compute
    "gcp_compute_engine": "compute", "gcp_gke": "compute",
    "gcp_cloud_run": "compute", "gcp_cloud_functions": "compute",
    # GCP — Security
    "gcp_kms": "security", "gcp_cloud_armor": "security",
    "gcp_iam": "security", "gcp_security_scanner": "security",
    "gcp_secret_manager": "security",
    # GCP — Networking
    "gcp_lb": "networking", "gcp_cloud_cdn": "networking",
    "gcp_cloud_dns": "networking", "gcp_vpn": "networking",
    # GCP — Management
    "gcp_cloud_monitoring": "management", "gcp_cloud_logging": "management",
    # GCP — Integration
    "gcp_pubsub": "integration", "gcp_tasks": "integration",
    # GCP — Analytics
    "gcp_bigquery": "analytics", "gcp_dataflow": "analytics", "gcp_dataproc": "analytics",
    # Azure — Storage
    "azure_blob": "storage", "azure_disk": "storage", "azure_backup": "storage",
    # Azure — Database
    "azure_sql": "database", "azure_cosmos_db": "database", "azure_redis": "database",
    # Azure — Compute
    "azure_vm": "compute", "azure_aks": "compute",
    "azure_app_service": "compute", "azure_functions": "compute", "azure_aci": "compute",
    # Azure — Security
    "azure_key_vault": "security", "azure_waf": "security", "azure_ad": "security",
    "azure_defender": "security", "azure_sentinel": "security",
    # Azure — Networking
    "azure_lb": "networking", "azure_agw": "networking",
    "azure_front_door": "networking", "azure_dns": "networking",
    "azure_firewall": "networking", "azure_vpn": "networking",
    # Azure — Management
    "azure_monitor": "management",
    # Azure — Integration
    "azure_queue": "integration", "azure_service_bus": "integration",
    "azure_event_grid": "integration",
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


def _analyze_rule_based(
    services: list[str], platform: str, fw_id: str, fw_config: dict
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

        required = _get_platform_services(fw_config, ctrl_id, platform)
        status = _control_status(required, services)

        findings: list[dict[str, str]] = []
        if status == "fail":
            affected_list: list[str] = []
            if affected_cats:
                affected_list = _services_in_categories(services, affected_cats)
            else:
                affected_list = [s for s in services if s not in _CATEGORY_SERVICES.get("security", [])]
            findings.append({
                "severity": ctrl.get("severity", "medium"),
                "message": ctrl.get("description", f"Missing {ctrl.get('name', ctrl_id)}"),
                "recommendation": ctrl.get("recommendation", ""),
                "affected_services": affected_list[:10],
            })
            total_fail += 1
        else:
            total_pass += 1

        controls_result.append({
            "id": ctrl_id,
            "name": ctrl.get("name", ctrl_id),
            "status": status,
            "findings": findings,
        })

    if total_fail > 0:
        overall = "fail"
    elif total_warn > 0:
        overall = "warn"
    else:
        overall = "pass"

    summary_parts = []
    total_controls = len(controls_result)
    if total_pass > 0:
        summary_parts.append(f"{total_pass}/{total_controls} controls passing")
    if total_fail > 0:
        summary_parts.append(f"{total_fail} failing")

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
        total = len(fw.get("controls", []))
        if total == 0:
            continue
        fails = sum(1 for c in fw["controls"] if c["status"] == "fail")
        warns = sum(1 for c in fw["controls"] if c["status"] == "warn")
        passes = total - fails - warns
        if fails > 0:
            fw["overall_status"] = "fail"
        elif warns > 0:
            fw["overall_status"] = "warn"
        else:
            fw["overall_status"] = "pass"
        fw["summary"] = f"{passes}/{total} controls passing" + (f", {fails} failing" if fails else "")

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
        results[fw_id] = _analyze_rule_based(services, platform_key, fw_id, fw_config)

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
                logger.log("compliance", f"LLM enhancement skipped: {exc}", level="warning")

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
