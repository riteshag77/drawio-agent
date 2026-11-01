"""Self-correct loop: generate complex architecture via Gemini, dump artifacts."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import truststore

truststore.inject_into_ssl()

from app.core.logging import StepLogger
from app.core.services import xml_pipeline

OUT = ROOT / "data" / "e2e_compare" / "self_correct"
PROMPT = """
Design a production-grade multi-tenant SaaS platform for an Australian health-tech
company: clinicians and patients use a React web app and native mobile apps;
the platform stores clinical notes (PHI/PII), appointments, and billing.

Requirements the architecture MUST show:
- Edge: CloudFront + WAF + Route53; API Gateway + Cognito (patients) and a
  separate workforce IdP path (Okta / Entra for clinicians)
- App: ECS Fargate services for clinical API, appointments API, notifications;
  async via SQS/EventBridge; Step Functions for referral workflows
- Data: Aurora PostgreSQL (Multi-AZ) for clinical SoR, ElastiCache Redis,
  S3 for documents with KMS CMK, DynamoDB for session/feature flags
- Integrations: HL7/FHIR partner gateway (egress), Stripe for billing (egress SaaS),
  Twilio for SMS
- Security: private subnets, Secrets Manager, GuardDuty, CloudTrail, CloudWatch,
  no public data-plane endpoints; mTLS or TLS on sensitive paths
- Observability and backup/DR called out
- Multi-AZ in ap-southeast-2

Produce full multi-view: Logical, Network, Security, and Data flow.
""".strip()


def _plain(val: str) -> str:
    t = re.sub(r"<[^>]+>", " ", val or "")
    return re.sub(r"\s+", " ", t).strip()


def analyze(xml: str) -> dict:
    pages: dict[str, dict] = {}
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as exc:
        return {"error": str(exc)}
    diags = tree.findall("diagram") if tree.tag == "mxfile" else []
    for diag in diags:
        name = diag.get("name") or "Page"
        if name in {"ADR Notes", "RAID"}:
            continue
        cells = {c.get("id"): c for c in diag.iter("mxCell") if c.get("id")}
        leaves = [
            c for c in cells.values()
            if c.get("vertex") == "1" and c.get("edge") != "1"
            and "container=1" not in (c.get("style") or "")
            and (c.get("id") or "") not in {"0", "1", "overview_panel", "legend_box"}
            and not (c.get("id") or "").startswith(("step_badge_", "gap_"))
        ]
        edges = [c for c in cells.values() if c.get("edge") == "1"]
        badges = [c for cid, c in cells.items() if cid.startswith("step_badge_")]
        ov = cells.get("overview_panel")
        overview = _plain(ov.get("value") or "") if ov is not None else ""
        mx = sum(1 for c in leaves if "mxgraph." in (c.get("style") or ""))
        grey = sum(1 for c in leaves if "fillColor=#F5F5F5" in (c.get("style") or ""))
        anchored = sum(
            1 for e in edges
            if "exitX=" in (e.get("style") or "") and "entryX=" in (e.get("style") or "")
        )
        labeled = sum(1 for e in edges if _plain(e.get("value") or ""))
        pii = sum(1 for e in edges if (e.get("classification") or "") in {"pii", "pci"})
        tls = sum(1 for e in edges if (e.get("protection") or "") in {"tls", "mtls"})
        # badge drift
        drifts = []
        for b in badges:
            g = b.find("mxGeometry")
            anchor = b.get("anchor") or ""
            if anchor not in cells or g is None:
                drifts.append({"badge": b.get("id"), "issue": "missing_anchor"})
                continue
            # absolute of anchor
            ax = ay = 0.0
            cur = cells[anchor]
            visited = set()
            while cur is not None:
                cid = cur.get("id") or ""
                if cid in visited:
                    break
                visited.add(cid)
                gg = cur.find("mxGeometry")
                if gg is not None:
                    ax += float(gg.get("x") or 0)
                    ay += float(gg.get("y") or 0)
                pid = cur.get("parent")
                cur = cells.get(pid) if pid else None
            bx, by = float(g.get("x") or 0), float(g.get("y") or 0)
            drifts.append({
                "badge": b.get("value"),
                "anchor": anchor,
                "dx": round(bx - (ax - 11), 1),
                "dy": round(by - (ay - 11), 1),
            })
        tech_in_overview = bool(re.search(r"\b1\.\s+", overview))
        howto = "badge" in overview.lower() or "Orange" in overview
        pages[name] = {
            "leaves": len(leaves),
            "mxgraph": mx,
            "grey_placeholders": grey,
            "edges": len(edges),
            "anchored": anchored,
            "labeled_edges": labeled,
            "pii_edges": pii,
            "tls_edges": tls,
            "badges": len(badges),
            "overview_chars": len(overview),
            "overview_has_numbered_steps": tech_in_overview,
            "overview_has_howto": howto,
            "overview_snippet": overview[:500],
            "badge_drifts": drifts[:8],
            "leaf_labels": [_plain(c.get("value") or "")[:40] for c in leaves[:20]],
        }
    return pages


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    logger = StepLogger()
    print("Generating with gemini…", flush=True)
    result = await xml_pipeline.run_xml_pipeline(
        user_prompt=PROMPT,
        provider="gemini",
        platform="aws",
        logger=logger,
        diagram_renderer="auto",
        diagram_views=["logical", "network", "security", "data_flow"],
        adr_notes=False,
        raid_register=False,
        cost_estimate=False,
        well_architected=False,
        compliance=False,
        include_suggestions=False,
        libavoid=True,
    )
    xml = result.get("xml") or ""
    (OUT / "live.xml").write_text(xml, encoding="utf-8")
    meta = {
        "status": result.get("status"),
        "generation_path": result.get("generation_path"),
        "renderer_used": result.get("renderer_used"),
        "degraded_features": result.get("degraded_features"),
        "llm_usage": result.get("llm_usage"),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    pages = analyze(xml)
    (OUT / "analysis.json").write_text(json.dumps(pages, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2, default=str))
    print(json.dumps(pages, indent=2)[:8000])
    print("Wrote", OUT)
    return 0 if result.get("status") == "success" and xml else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
