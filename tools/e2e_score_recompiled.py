"""Apply MCP/cache icon resolve to recompiled capture XML and print scores."""
from __future__ import annotations

import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.outbound.diagram.architecture_docs import merge_mxfile_pages
from app.adapters.outbound.diagram.drawio_mcp_client import resolve_shape_styles
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document
from app.core.logging import StepLogger
from tools.e2e_capture_compare import (
    OUT_DIR,
    load_capture,
    prompt_from_capture,
    recompile_from_capture_irs,
    score_mxfile,
)


def geometry_scores(scored: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, ps in scored["page_scores"].items():
        leaves = max(ps["leaves"], 1)
        band_ok = (
            1.0
            if ps["row_y_range"] is None or ps["row_y_range"] <= 2.0
            else 0.0
        )
        out[name] = round(
            100
            * (
                0.35 * (1 - ps["wrong_leaf_size"] / leaves)
                + 0.35 * (1 - ps["leaf_overflow"] / leaves)
                + 0.15 * band_ok
                + 0.15 * (ps["edges_anchored"] / max(ps["edges"], 1))
            )
        )
    return out


async def resolve_all(xml: str) -> str:
    logger = StepLogger()
    tree = ET.fromstring(ensure_diagram_document(xml, "Page"))
    pages: list[tuple[str, str]] = []
    for diag in tree.findall("diagram"):
        name = diag.get("name") or "Page"
        inner = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
        page = ensure_diagram_document(inner, name)
        types = [
            c.get("shape_type")
            for c in ET.fromstring(page).iter("mxCell")
            if c.get("shape_type")
        ]
        types = [t for t in types if t]
        overrides = await resolve_shape_styles(list(set(types)), "aws", logger=logger) if types else {}
        if overrides:
            root = ET.fromstring(page)
            for cell in root.iter("mxCell"):
                st = cell.get("shape_type")
                if st and st in overrides:
                    cell.set("style", overrides[st])
            page = ET.tostring(root, encoding="unicode")
        pages.append((name, page))
    return merge_mxfile_pages(pages) if len(pages) > 1 else pages[0][1]


def main() -> int:
    _, data = load_capture(None)
    prompt, *_ = prompt_from_capture(data)
    old = score_mxfile(data["response"]["xml"], "capture")
    print(f"Prompt: {prompt}")
    print("Recompiling capture IRs…", flush=True)
    xml = recompile_from_capture_irs(data)
    print("Resolving icons via MCP/cache…", flush=True)
    try:
        xml2 = asyncio.run(resolve_all(xml))
    except Exception as exc:
        print(f"MCP resolve failed ({exc}); scoring placeholder styles")
        xml2 = xml
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "recompiled_mcp.xml").write_text(xml2, encoding="utf-8")
    new = score_mxfile(xml2, "recompiled+mcp")
    (OUT_DIR / "recompiled_mcp_score.json").write_text(json.dumps(new, indent=2), encoding="utf-8")

    print("\n=== GEOMETRY (layout conventions) ===")
    print("capture :", geometry_scores(old))
    print("current :", geometry_scores(new))
    print("\n=== FULL POLISH (icons + geometry + anchors) ===")
    print(f"capture mean={old['mean_polish']}")
    for n, ps in old["page_scores"].items():
        print(
            f"  {n:12} polish={ps['polish_score']:3} icons={ps['mxgraph_icons']}/{ps['leaves']} "
            f"bad_size={ps['wrong_leaf_size']} overflow={ps['leaf_overflow']} band_dY={ps['row_y_range']}"
        )
    print(f"current mean={new['mean_polish']}")
    for n, ps in new["page_scores"].items():
        print(
            f"  {n:12} polish={ps['polish_score']:3} icons={ps['mxgraph_icons']}/{ps['leaves']} "
            f"bad_size={ps['wrong_leaf_size']} overflow={ps['leaf_overflow']} band_dY={ps['row_y_range']}"
        )
    print("\nCapture vs current pipeline:")
    print("  Same prompt as the reference README example (React auth / serverless).")
    print("  Us: 4 IR views, deterministic geometry, MCP icons, overview/legend.")
    geo_old = geometry_scores(old)
    geo_new = geometry_scores(new)
    logical_gain = geo_new.get("Logical", 0) - geo_old.get("Logical", 0)
    print(f"  Logical geometry gain: {logical_gain:+d} (band align + leaf fit)")
    print(f"  Artifacts: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
