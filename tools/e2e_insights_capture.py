"""E2E: Well-Architected, Compliance, Suggestions vs captures + live pipeline.

Validates:
  1) Historical capture payloads match UI panel contracts (placeholder fields).
  2) Deterministic WAF/compliance recompute on capture XML is stable.
  3) Live pipeline with flags ON returns payloads the frontend can render.

Usage:
  python tools/e2e_insights_capture.py
  python tools/e2e_insights_capture.py --skip-live
  python tools/e2e_insights_capture.py --capture 01785144965506558500_89fad471
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import truststore

truststore.inject_into_ssl()

from app.adapters.outbound.compliance import assess_compliance  # noqa: E402
from app.adapters.outbound.well_architected import assess_well_architected  # noqa: E402
from app.core.logging import StepLogger  # noqa: E402
from app.core.services import xml_pipeline  # noqa: E402

OUT = ROOT / "data" / "e2e_compare" / "insights"
CAPTURES = ROOT / "data" / "captures"

# Fields the frontend panels actually read (insights-panels.js / captures.js).
_WAF_REQUIRED = {"overall_status", "pillars", "summary", "services_analyzed"}
_PILLAR_REQUIRED = {"name", "status", "findings"}
_FINDING_REQUIRED = {"message"}
_COMP_REQUIRED = {"frameworks", "services_analyzed"}
_FW_REQUIRED = {"name", "overall_status", "summary", "controls"}
_CONTROL_REQUIRED = {"status"}


def _load_capture(capture_id: str | None) -> tuple[str, dict[str, Any]]:
    files = sorted(CAPTURES.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No captures in data/captures")
    if capture_id:
        match = [p for p in files if capture_id in p.name]
        if not match:
            raise SystemExit(f"Capture not found: {capture_id}")
        path = match[0]
    else:
        # Prefer a capture that actually ran WAF/compliance.
        path = None
        for p in files:
            rec = json.loads(p.read_text(encoding="utf-8"))
            resp = rec.get("response") or {}
            if isinstance(resp.get("well_architected"), dict) and isinstance(resp.get("compliance"), dict):
                path = p
                break
        path = path or files[0]
    return path.stem, json.loads(path.read_text(encoding="utf-8"))


def _check_waf(payload: Any, *, label: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(payload, dict):
        return [f"{label}: expected dict, got {type(payload).__name__}"]
    missing = _WAF_REQUIRED - set(payload)
    if missing:
        errs.append(f"{label}: missing keys {sorted(missing)}")
    if payload.get("overall_status") not in {"pass", "warn", "fail"}:
        errs.append(f"{label}: bad overall_status={payload.get('overall_status')!r}")
    pillars = payload.get("pillars")
    if not isinstance(pillars, list) or not pillars:
        errs.append(f"{label}: pillars must be non-empty list")
        return errs
    for i, p in enumerate(pillars):
        if not isinstance(p, dict):
            errs.append(f"{label}.pillars[{i}]: not a dict")
            continue
        miss = _PILLAR_REQUIRED - set(p)
        if miss:
            errs.append(f"{label}.pillars[{i}]: missing {sorted(miss)}")
        for j, f in enumerate(p.get("findings") or []):
            if not isinstance(f, dict):
                errs.append(f"{label}.pillars[{i}].findings[{j}]: not a dict")
                continue
            if _FINDING_REQUIRED - set(f):
                errs.append(f"{label}.pillars[{i}].findings[{j}]: missing message")
            # UI only creates apply-checkbox when recommendation present
            if f.get("recommendation") is not None and not str(f.get("recommendation")).strip():
                errs.append(f"{label}.pillars[{i}].findings[{j}]: empty recommendation")
    return errs


def _check_compliance(payload: Any, *, label: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(payload, dict):
        return [f"{label}: expected dict, got {type(payload).__name__}"]
    missing = _COMP_REQUIRED - set(payload)
    if missing:
        errs.append(f"{label}: missing keys {sorted(missing)}")
    frameworks = payload.get("frameworks")
    if not isinstance(frameworks, dict) or not frameworks:
        errs.append(f"{label}: frameworks must be non-empty dict (UI early-returns otherwise)")
        return errs
    for fw_id, fw in frameworks.items():
        if not isinstance(fw, dict):
            errs.append(f"{label}.frameworks[{fw_id}]: not a dict")
            continue
        miss = _FW_REQUIRED - set(fw)
        if miss:
            errs.append(f"{label}.frameworks[{fw_id}]: missing {sorted(miss)}")
        if fw.get("overall_status") not in {"pass", "warn", "fail"}:
            errs.append(f"{label}.frameworks[{fw_id}]: bad status={fw.get('overall_status')!r}")
        for j, ctrl in enumerate(fw.get("controls") or []):
            if not isinstance(ctrl, dict):
                errs.append(f"{label}.frameworks[{fw_id}].controls[{j}]: not a dict")
                continue
            if _CONTROL_REQUIRED - set(ctrl):
                errs.append(f"{label}.frameworks[{fw_id}].controls[{j}]: missing status")
            # displayCompliance reads recommendation for apply checkboxes
            if ctrl.get("recommendation") and not str(ctrl.get("id") or ctrl.get("title") or ctrl.get("name") or ""):
                # title/name optional in older captures — warn only via soft note
                pass
    return errs


def _check_suggestions(payload: Any, *, label: str, require_nonempty: bool) -> list[str]:
    errs: list[str] = []
    if payload is None:
        if require_nonempty:
            errs.append(f"{label}: missing suggestions list")
        return errs
    if not isinstance(payload, list):
        return [f"{label}: expected list, got {type(payload).__name__}"]
    if require_nonempty and not payload:
        errs.append(f"{label}: empty suggestions (include_suggestions was true)")
    for i, s in enumerate(payload):
        if isinstance(s, str):
            if not s.strip():
                errs.append(f"{label}[{i}]: blank string")
            continue
        if isinstance(s, dict):
            text = str(s.get("text") or s.get("suggestion") or "").strip()
            if not text:
                errs.append(f"{label}[{i}]: missing text/suggestion (displaySuggestions needs it)")
            continue
        errs.append(f"{label}[{i}]: unsupported type {type(s).__name__}")
    return errs


def _ui_placeholder_contract() -> dict[str, list[str]]:
    """DOM ids the frontend writes into — must exist in static/index.html."""
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    required = {
        "waf": [
            "waf-compliance-panel",
            "well-architected-panel",
            "waf-panel-body",
            "waf-overall-badge",
            "well-architected-toggle",
        ],
        "compliance": [
            "compliance-panel",
            "compliance-tabs",
            "compliance-body",
            "compliance-toggle",
        ],
        "suggestions": [
            "suggestions-panel",
            "suggestions-panel-body",
            "suggestions-wrap",
            "suggestions-toggle",
            "suggestions-action-row",
        ],
        "playback": [
            # capture playback must reach the same panels
            "captures-select",
        ],
    }
    missing: dict[str, list[str]] = {}
    for group, ids in required.items():
        bad = [i for i in ids if f'id="{i}"' not in html]
        if bad:
            missing[group] = bad
    return missing


def validate_capture(rec: dict[str, Any]) -> dict[str, Any]:
    req = rec.get("request") or {}
    resp = rec.get("response") or {}
    xml = resp.get("xml") or ""
    report: dict[str, Any] = {
        "prompt": (rec.get("prompt_snippet") or "")[:120],
        "request_flags": {
            "well_architected": bool(req.get("well_architected")),
            "compliance": bool(req.get("compliance")),
            "suggestions": bool(req.get("suggestions")),
            "compliance_frameworks": req.get("compliance_frameworks") or [],
            "diagram_views": req.get("diagram_views") or [],
        },
        "errors": [],
        "notes": [],
    }

    # Playback wiring gap detection (static check against captures.js source).
    captures_js = (ROOT / "static" / "js" / "captures.js").read_text(encoding="utf-8")
    if "displaySuggestions" not in captures_js:
        report["notes"].append(
            "BUG: captures.js playback does not call displaySuggestions — "
            "suggestions from captures never fill #suggestions-panel-body"
        )

    waf = resp.get("well_architected")
    if req.get("well_architected"):
        report["errors"].extend(_check_waf(waf, label="capture.well_architected"))
    elif waf:
        report["notes"].append("Response has well_architected but request flag was false")

    comp = resp.get("compliance")
    if req.get("compliance"):
        report["errors"].extend(_check_compliance(comp, label="capture.compliance"))
    elif comp:
        report["notes"].append("Response has compliance but request flag was false")

    sugg = resp.get("suggestions")
    report["errors"].extend(
        _check_suggestions(sugg, label="capture.suggestions", require_nonempty=bool(req.get("suggestions")))
    )

    # Recompute deterministic WAF on capture XML and compare overall status.
    if xml and isinstance(waf, dict):
        recomputed = assess_well_architected(xml, platform=(req.get("platform") or "aws"))
        report["waf_recompute"] = {
            "capture_status": waf.get("overall_status"),
            "recomputed_status": recomputed.get("overall_status"),
            "capture_services": waf.get("services_analyzed"),
            "recomputed_services": recomputed.get("services_analyzed"),
            "match_status": waf.get("overall_status") == recomputed.get("overall_status"),
        }
        if not report["waf_recompute"]["match_status"]:
            report["notes"].append(
                f"WAF status drift: capture={waf.get('overall_status')} "
                f"recomputed={recomputed.get('overall_status')} "
                "(rules may have changed since capture)"
            )
        report["errors"].extend(_check_waf(recomputed, label="recomputed.well_architected"))

        frameworks = req.get("compliance_frameworks") or list((comp or {}).get("frameworks") or {})
        if frameworks:
            # Rule-based only (provider=None) — stable offline check.
            rule = assess_compliance(
                xml=xml,
                platform=req.get("platform") or "aws",
                frameworks=frameworks,
                provider=None,
            )
            report["compliance_recompute"] = {
                "frameworks": sorted((rule.get("frameworks") or {}).keys()),
                "services_analyzed": rule.get("services_analyzed"),
                "llm_enhanced": rule.get("llm_enhanced"),
            }
            report["errors"].extend(_check_compliance(rule, label="recomputed.compliance"))

    return report


async def run_live(prompt: str, frameworks: list[str]) -> dict[str, Any]:
    logger = StepLogger()
    result = await xml_pipeline.run_xml_pipeline(
        user_prompt=prompt,
        provider="gemini",
        platform="aws",
        logger=logger,
        diagram_renderer="auto",
        diagram_views=["logical", "network", "security", "data_flow"],
        adr_notes=False,
        raid_register=False,
        cost_estimate=True,
        well_architected=True,
        compliance=True,
        compliance_frameworks=frameworks or ["hipaa", "pci_dss", "soc2"],
        include_suggestions=True,
        libavoid=False,
    )
    out: dict[str, Any] = {
        "status": result.get("status"),
        "generation_path": result.get("generation_path"),
        "degraded_features": result.get("degraded_features") or [],
        "errors": [],
        "notes": [],
    }
    waf = result.get("well_architected")
    comp = result.get("compliance")
    sugg = result.get("suggestions")
    cost = result.get("cost_estimate")

    out["errors"].extend(_check_waf(waf, label="live.well_architected"))
    out["errors"].extend(_check_compliance(comp, label="live.compliance"))
    out["errors"].extend(_check_suggestions(sugg, label="live.suggestions", require_nonempty=True))

    if not isinstance(cost, dict) or "total" not in cost:
        out["errors"].append("live.cost_estimate: missing or incomplete (cost panel needs total)")

    # Placeholder routing: network required for WAF/compliance branch.
    if waf is None:
        out["errors"].append("live.well_architected is null — pipeline skipped WAF (network view?)")
    if comp is None:
        out["errors"].append("live.compliance is null — pipeline skipped compliance")

    out["summary"] = {
        "waf_status": (waf or {}).get("overall_status") if isinstance(waf, dict) else None,
        "waf_pillars": len((waf or {}).get("pillars") or []) if isinstance(waf, dict) else 0,
        "compliance_frameworks": sorted(((comp or {}).get("frameworks") or {}).keys()) if isinstance(comp, dict) else [],
        "suggestions_count": len(sugg) if isinstance(sugg, list) else 0,
        "suggestions_sample": [
            (s.get("text") if isinstance(s, dict) else s)
            for s in (sugg or [])[:3]
        ] if isinstance(sugg, list) else [],
        "cost_total": (cost or {}).get("total") if isinstance(cost, dict) else None,
        "xml_chars": len(result.get("xml") or ""),
    }
    # Persist live payloads for UI inspection / playback.
    (OUT / "live_response.json").write_text(
        json.dumps(
            {
                "well_architected": waf,
                "compliance": comp,
                "suggestions": sugg,
                "cost_estimate": cost,
                "status": result.get("status"),
                "generation_path": result.get("generation_path"),
                "llm_usage": result.get("llm_usage"),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if result.get("xml"):
        (OUT / "live.xml").write_text(result["xml"], encoding="utf-8")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default=None)
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cid, rec = _load_capture(args.capture)
    print(f"Using capture: {cid}", flush=True)

    placeholder_gaps = _ui_placeholder_contract()
    capture_report = validate_capture(rec)
    report: dict[str, Any] = {
        "capture_id": cid,
        "ui_placeholder_gaps": placeholder_gaps,
        "capture": capture_report,
    }
    if placeholder_gaps:
        capture_report["errors"].append(f"Missing DOM placeholders: {placeholder_gaps}")

    if not args.skip_live:
        req = rec.get("request") or {}
        prompt = (rec.get("prompt_snippet") or "").strip()
        if len(prompt) < 40:
            # Fall back to last user message.
            msgs = req.get("messages") or []
            if msgs:
                prompt = (msgs[-1].get("content") or prompt).strip()
        if not prompt:
            prompt = (
                "Highly available AWS web app with ALB, ECS in private subnets, "
                "Aurora Multi-AZ, WAF, CloudFront, and Cognito. Multi-view."
            )
        frameworks = req.get("compliance_frameworks") or ["hipaa", "pci_dss", "soc2"]
        print("Running live pipeline with WAF+compliance+suggestions…", flush=True)
        report["live"] = await run_live(prompt, frameworks)

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str)[:12000])
    print("Wrote", OUT / "report.json")

    errors = list(capture_report.get("errors") or [])
    if report.get("live"):
        errors.extend(report["live"].get("errors") or [])
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
