"""Replay a capture prompt through the live IR pipeline and score polish.

Compares:
  1) historical capture XML (pre-/mid-fix)
  2) freshly generated XML (current code + LLM)
  3) reference polish conventions (icons, anchors, leaf size, band align)

Usage:
  python tools/e2e_capture_compare.py
  python tools/e2e_capture_compare.py --capture 01785712836495237700_74584795
  python tools/e2e_capture_compare.py --skip-live   # score capture only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.outbound.diagram import geometry_spec as geo  # noqa: E402
from app.adapters.outbound.diagram.compiler import theme as network_theme  # noqa: E402
from app.adapters.outbound.diagram.logical_layout import INTER_CONTAINER_GAP  # noqa: E402
from app.adapters.outbound.diagram.view_conformance import score_view  # noqa: E402
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document  # noqa: E402
from app.core.logging import StepLogger  # noqa: E402
from app.core.services import xml_pipeline  # noqa: E402

OUT_DIR = ROOT / "data" / "e2e_compare"

#: Network's zone containers use one fixed, platform-independent style (see
#: theme.zone_style) — unlike Logical/Security/Data-flow's corridors, Network
#: has no fixed id vocabulary to key off (zone/VPC ids are whatever the model
#: chose), so peer-zone detection below matches on this style signature and
#: groups by the (arbitrary) parent VPC id instead.
_NETWORK_ZONE_STYLE = network_theme.zone_style()


def _pages(xml: str) -> dict[str, str]:
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Page"))
    except ET.ParseError:
        return {}
    if tree.tag != "mxfile":
        return {"Page": xml}
    out: dict[str, str] = {}
    for diag in tree.findall("diagram"):
        name = diag.get("name") or "Page"
        inner = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
        out[name] = ensure_diagram_document(inner, name)
    return out


def _geo(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    if g is None:
        return 0, 0, 0, 0
    return (
        float(g.get("x") or 0),
        float(g.get("y") or 0),
        float(g.get("width") or 0),
        float(g.get("height") or 0),
    )


def _is_leaf(cell: ET.Element) -> bool:
    if cell.get("vertex") != "1" or cell.get("edge") == "1":
        return False
    cid = cell.get("id") or ""
    style = cell.get("style") or ""
    if "container=1" in style or "swimlane" in style:
        return False
    if cid in {"0", "1", "overview_panel", "legend_box", "actors_left"}:
        return False
    if cid.startswith(("step_badge_", "note_", "ctrl_", "sec_", "gap_", "domain_", "zone_", "phase_")):
        return False
    if cid.startswith(("cloud", "region", "vpc", "subnet", "az_", "group_")):
        return False
    return True


def score_archimate_page(name: str, xml: str) -> dict:
    """ArchiMate's own polish axis — NOT comparable to score_page's number.

    score_page's weights are cloud-view-shaped and cannot be reused as-is:
    element size is checked against LEAF_W/H (78x78) when ArchiMate elements
    are ARCHIMATE_ELEMENT_W/H (170x70, verified empirically to score
    wrong_leaf_size=100% on a perfect page), edges_anchored counts exitX/
    entryX ports that ArchiMate's root-layer relationship edges never carry,
    and bottom_labels rewards a label position ArchiMate never uses (elements
    are top-labeled, by design, to leave room for the notation glyph).
    icon_rate is left out on purpose too, but for the opposite reason: every
    ArchiMate element shares one stencil family
    (mxgraph.archimate3.application), so it is always ~100% regardless of
    diagram quality — present, but not a real signal.
    """
    root = ET.fromstring(xml)
    cells = [c for c in root.iter("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells}
    elements = [c for c in cells if "mxgraph.archimate3" in (c.get("style") or "")]
    edges = [c for c in cells if c.get("edge") == "1"]
    bands = [cid for cid in by_id if cid.startswith("archimate_band_")]

    wrong_size = sum(
        1 for c in elements
        if abs(_geo(c)[2] - geo.ARCHIMATE_ELEMENT_W) > 1 or abs(_geo(c)[3] - geo.ARCHIMATE_ELEMENT_H) > 1
    )
    overflow = 0
    for c in elements:
        x, y, w, h = _geo(c)
        parent = by_id.get(c.get("parent") or "")
        if parent is not None and "container=1" in (parent.get("style") or ""):
            _, _, pw, ph = _geo(parent)
            if x + w > pw + 1 or y + h > ph + 1:
                overflow += 1
    labelled_edges = sum(1 for e in edges if (e.get("value") or "").strip())
    band_lefts = {round(_geo(by_id[b])[0], 1) for b in bands}
    layout_ir = any(c.get("id") == "1" and c.get("layout") == "ir" for c in cells)

    n_elements = max(len(elements), 1)
    n_edges = max(len(edges), 1)
    size_ok = 1.0 - (wrong_size / n_elements)
    overflow_ok = 1.0 - (overflow / n_elements)
    label_coverage = labelled_edges / n_edges
    band_aligned = 1.0 if len(band_lefts) <= 1 else 0.0

    polish = round(100 * (
        0.35 * size_ok
        + 0.25 * overflow_ok
        + 0.25 * label_coverage
        + 0.15 * band_aligned
    ))

    return {
        "page": name,
        "layout_ir": layout_ir,
        "elements": len(elements),
        "edges": len(edges),
        "wrong_element_size": wrong_size,
        "element_overflow": overflow,
        "edges_labelled": labelled_edges,
        "bands": len(bands),
        "bands_left_aligned": len(band_lefts) <= 1,
        "polish_score": polish,
    }


def score_page(name: str, xml: str) -> dict:
    """Polish score aligned with AWS-skill conventions."""
    root = ET.fromstring(xml)
    cells = [c for c in root.iter("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells}
    leaves = [c for c in cells if _is_leaf(c)]
    edges = [c for c in cells if c.get("edge") == "1"]

    mxgraph = sum(1 for c in leaves if "mxgraph." in (c.get("style") or ""))
    placeholder = sum(
        1 for c in leaves
        if "fillColor=#F5F5F5" in (c.get("style") or "") or "shape_type=" in ET.tostring(c, encoding="unicode")
        and "mxgraph." not in (c.get("style") or "")
    )
    # Prefer style-only placeholder count
    placeholder = sum(
        1 for c in leaves
        if "mxgraph." not in (c.get("style") or "") and "shape=mxgraph" not in (c.get("style") or "")
    )
    wrong_leaf_size = 0
    overflow = 0
    bottom_label = 0
    for c in leaves:
        x, y, w, h = _geo(c)
        if abs(w - geo.LEAF_W) > 1 or abs(h - geo.LEAF_H) > 1:
            # Allow label-band height only if exactly LEAF_H + LABEL_BAND (legacy bug)
            if not (abs(w - geo.LEAF_W) <= 1 and abs(h - (geo.LEAF_H + getattr(geo, "LABEL_BAND_H", 28))) <= 1):
                wrong_leaf_size += 1
            else:
                wrong_leaf_size += 1  # still wrong under current contract
        parent = by_id.get(c.get("parent") or "")
        if parent is not None and "container=1" in (parent.get("style") or ""):
            _, _, pw, ph = _geo(parent)
            if x + w > pw + 1 or y + h > ph + 1:
                overflow += 1
        if "verticalLabelPosition=bottom" in (c.get("style") or ""):
            bottom_label += 1

    anchored = sum(
        1 for e in edges
        if "exitX=" in (e.get("style") or "") and "entryX=" in (e.get("style") or "")
    )
    rounded_edges = sum(1 for e in edges if "rounded=1" in (e.get("style") or ""))
    layout_ir = any(c.get("id") == "1" and c.get("layout") == "ir" for c in cells)

    # Corridor row band (Logical/Security/Data flow)
    row_y_range = None
    min_gap = None
    gap_floor = INTER_CONTAINER_GAP
    prefix = {"Logical": "domain_", "Security": "zone_", "Data flow": "phase_"}.get(name)
    if prefix:
        rows = [
            cid for cid, c in by_id.items()
            if cid.startswith(prefix)
            and c.get("parent") == "1"
            and c.get("vertex") == "1"
            and "saas" not in cid
            and cid not in {
                "domain_saas", "zone_external_saas", "phase_consume",
                "phase_saas_producer", "phase_saas_consumer", "phase_control",
            }
        ]
        if len(rows) >= 2:
            ys = [_geo(by_id[r])[1] for r in rows]
            row_y_range = max(ys) - min(ys)
            ordered = sorted(rows, key=lambda r: _geo(by_id[r])[0])
            gaps = []
            for a, b in zip(ordered, ordered[1:]):
                ax, _, aw, _ = _geo(by_id[a])
                bx, _, _, _ = _geo(by_id[b])
                gaps.append(bx - (ax + aw))
            min_gap = min(gaps) if gaps else None
    elif name == "Network":
        # No fixed id vocabulary to key off (unlike domain_/zone_/phase_ above)
        # — group AvailabilityZone containers by their real parent VPC id
        # instead, matched via zone_style()'s fixed style signature. This is
        # the direct visible signal network.txt's "peer zones render as
        # mirrors, same size, matching tiers on the same row" promise
        # produces once implemented (see elk_layout_network.py::_elk_zone) —
        # previously this branch didn't exist at all, so Network's band_ok/
        # gap_ok silently defaulted to a perfect 1.0 measuring nothing.
        # zone_style()'s color (#6B7280) collides byte-for-byte with the
        # "management" CrossCuttingGroup's own style (group_style reuses the
        # same hex for a different kind — see theme.py's _GROUP_COLORS/
        # _ZONE_COLOR) — style-string equality alone over-counts. Disambiguate
        # structurally instead: a zone's children are Subnet containers
        # (container=1 in their own style); a group's children are plain
        # leaves (never container=1) — confirmed against a live compiled page
        # before this branch shipped, catching a real false-positive.
        children_by_parent: dict[str, list[ET.Element]] = {}
        for c in by_id.values():
            children_by_parent.setdefault(c.get("parent") or "", []).append(c)
        zones_by_vpc: dict[str, list[str]] = {}
        for cid, c in by_id.items():
            if (c.get("style") or "") != _NETWORK_ZONE_STYLE:
                continue
            if not any("container=1" in (child.get("style") or "") for child in children_by_parent.get(cid, [])):
                continue
            zones_by_vpc.setdefault(c.get("parent") or "", []).append(cid)
        peer_groups = [ids for ids in zones_by_vpc.values() if len(ids) >= 2]
        if peer_groups:
            row_y_range = max(
                max(_geo(by_id[z])[3] for z in ids) - min(_geo(by_id[z])[3] for z in ids)
                for ids in peer_groups
            )
            all_gaps: list[float] = []
            for ids in peer_groups:
                ordered = sorted(ids, key=lambda z: _geo(by_id[z])[0])
                for a, b in zip(ordered, ordered[1:]):
                    ax, _, aw, _ = _geo(by_id[a])
                    bx, _, _, _ = _geo(by_id[b])
                    all_gaps.append(bx - (ax + aw))
            min_gap = min(all_gaps) if all_gaps else None
        gap_floor = geo.ZONE_GAP_X

    n_leaves = max(len(leaves), 1)
    n_edges = max(len(edges), 1)
    icon_rate = mxgraph / n_leaves
    size_ok = 1.0 - (wrong_leaf_size / n_leaves)
    overflow_ok = 1.0 - (overflow / n_leaves)
    anchor_rate = anchored / n_edges
    label_rate = bottom_label / n_leaves
    band_ok = 1.0 if row_y_range is None else (1.0 if row_y_range <= 2.0 else max(0.0, 1.0 - row_y_range / 200.0))
    gap_ok = 1.0 if min_gap is None else (1.0 if min_gap >= gap_floor - 20 else max(0.0, min_gap / gap_floor))

    # Weighted polish score (icons + geometry + edges)
    polish = round(100 * (
        0.30 * icon_rate
        + 0.20 * size_ok
        + 0.15 * overflow_ok
        + 0.15 * anchor_rate
        + 0.10 * label_rate
        + 0.05 * band_ok
        + 0.05 * gap_ok
    ))

    return {
        "page": name,
        "layout_ir": layout_ir,
        "leaves": len(leaves),
        "edges": len(edges),
        "mxgraph_icons": mxgraph,
        "non_mxgraph_leaves": placeholder,
        "wrong_leaf_size": wrong_leaf_size,
        "leaf_overflow": overflow,
        "edges_anchored": anchored,
        "edges_rounded": rounded_edges,
        "bottom_labels": bottom_label,
        "row_y_range": row_y_range,
        "min_corridor_gap": min_gap,
        "polish_score": polish,
    }


def score_mxfile(xml: str, label: str) -> dict:
    pages = _pages(xml)
    # ArchiMate scores on its own axis (score_archimate_page) — its numbers
    # are not comparable to the cloud pages' polish_score, so it is kept out
    # of page_scores/mean_polish and reported separately.
    cloud_pages = {n: pxml for n, pxml in pages.items() if n not in {"ADR Notes", "RAID", "ArchiMate"}}
    page_scores = {n: score_page(n, pxml) for n, pxml in cloud_pages.items()}
    archimate_score = score_archimate_page("ArchiMate", pages["ArchiMate"]) if "ArchiMate" in pages else None
    view_scores = {}
    primary = pages.get("Network") or pages.get("Logical") or next(iter(pages.values()), "")
    for view, pname in (
        ("logical", "Logical"), ("network", "Network"), ("security", "Security"),
        ("data_flow", "Data flow"), ("archimate", "ArchiMate"),
    ):
        if pname in pages:
            vs = score_view(pages[pname], view=view, primary_xml=primary, request_text="", min_score=75)
            view_scores[pname] = {"score": vs.score, "passes": vs.passes, "violations": [v.code for v in vs.violations][:6]}
    polish_vals = [s["polish_score"] for s in page_scores.values()]
    return {
        "label": label,
        "pages": list(page_scores.keys()) + (["ArchiMate"] if archimate_score else []),
        "mean_polish": round(sum(polish_vals) / len(polish_vals), 1) if polish_vals else 0,
        "page_scores": page_scores,
        "archimate_score": archimate_score,
        "view_conformance": view_scores,
    }


def load_capture(capture_id: str | None) -> tuple[Path, dict]:
    cap_dir = ROOT / "data" / "captures"
    if capture_id:
        path = cap_dir / f"{capture_id}.json"
        if not path.exists():
            path = cap_dir / capture_id
    else:
        # Prefer latest IR capture with the reference example prompt, else newest
        candidates = sorted(cap_dir.glob("*.json"), key=lambda p: p.name, reverse=True)
        path = candidates[0]
        for c in candidates:
            d = json.loads(c.read_text(encoding="utf-8"))
            snippet = (d.get("prompt_snippet") or "").lower()
            if "authentication" in snippet and "react" in snippet and (d.get("response") or {}).get("generation_path") == "ir":
                path = c
                break
    data = json.loads(path.read_text(encoding="utf-8"))
    return path, data


def prompt_from_capture(data: dict) -> tuple[str, str, list[str], str]:
    req = data["request"]
    msgs = req.get("messages") or []
    text = ""
    for m in msgs:
        if m.get("role") == "user":
            text = m.get("content") or text
    text = text or data.get("prompt_snippet") or ""
    provider = req.get("provider") or "claude"
    views = req.get("diagram_views") or ["logical", "network", "security", "data_flow"]
    platform = req.get("platform") or "aws"
    return text, provider, views, platform


async def run_live(prompt: str, provider: str, views: list[str], platform: str) -> dict:
    logger = StepLogger()
    result = await xml_pipeline.run_xml_pipeline(
        user_prompt=prompt,
        provider=provider,
        platform=platform,
        logger=logger,
        diagram_renderer="auto",
        diagram_views=views,
        adr_notes=False,
        raid_register=False,
        cost_estimate=False,
        well_architected=False,
        compliance=False,
        include_suggestions=False,
        libavoid=True,
    )
    result["_log_steps"] = [
        {"step": e.step, "level": e.level, "message": e.message}
        for e in logger.entries
        if e.step in {"ir", "ir_compile", "views", "repair", "mcp", "complete", "usage"}
    ]
    return result


def print_report(old: dict, new: dict | None, prompt: str, capture_path: Path) -> None:
    print("=" * 72)
    print("E2E CAPTURE COMPARE vs reference polish conventions")
    print("=" * 72)
    print(f"Prompt: {prompt}")
    print(f"Capture: {capture_path.name}")
    print()
    print("Reference conventions scored:")
    print("  - official mxgraph icons (not grey placeholders)")
    print("  - fixed 78x78 leaves + bottom labels")
    print("  - no leaf overflow of parents")
    print("  - explicit edge exit/entry anchors + rounded edges")
    print("  - corridor row Y alignment + healthy inter-domain gaps")
    print()

    def _block(title: str, scored: dict) -> None:
        print(f"--- {title} (mean polish {scored['mean_polish']}/100) ---")
        for name, ps in scored["page_scores"].items():
            vc = scored["view_conformance"].get(name, {})
            print(
                f"  {name:12} polish={ps['polish_score']:3}  leaves={ps['leaves']:2}  "
                f"icons={ps['mxgraph_icons']:2}/{ps['leaves']}  "
                f"bad_size={ps['wrong_leaf_size']}  overflow={ps['leaf_overflow']}  "
                f"anchored={ps['edges_anchored']}/{ps['edges']}  "
                f"band_dY={ps['row_y_range']}  "
                f"view={vc.get('score', '-')}{' PASS' if vc.get('passes') else (' FAIL' if vc else '')}"
            )
        archi = scored.get("archimate_score")
        if archi:
            vc = scored["view_conformance"].get("ArchiMate", {})
            # Own axis, own number — never averaged into mean_polish above.
            print(
                f"  {'ArchiMate':12} polish={archi['polish_score']:3} (own axis)  "
                f"elements={archi['elements']:2}  bands={archi['bands']}  "
                f"bad_size={archi['wrong_element_size']}  overflow={archi['element_overflow']}  "
                f"labelled={archi['edges_labelled']}/{archi['edges']}  "
                f"bands_aligned={archi['bands_left_aligned']}  "
                f"view={vc.get('score', '-')}{' PASS' if vc.get('passes') else (' FAIL' if vc else '')}"
            )
        print()

    _block("HISTORICAL CAPTURE", old)
    if new:
        _block("LIVE REGEN (current code)", new)
        delta = new["mean_polish"] - old["mean_polish"]
        print(f"Delta mean polish: {delta:+.1f}")
        print()
        print("Verdict:")
        if new["mean_polish"] >= 80:
            print("  Strong — deterministic IR layout + MCP icons clear the polish bar")
            print("    on geometry/icons/anchors, with multi-view architecture consistency.")
        elif new["mean_polish"] >= 65:
            print("  Competitive — most conventions met; residual icon misses or density.")
        else:
            print("  Behind — check MCP icon resolve / leaf sizing / band alignment.")
    print("=" * 72)


def extract_irs_from_capture(data: dict) -> dict[str, dict]:
    """Pull the last successful IR JSON per view from capture llm_output logs."""
    from app.domain.ir import IR_TYPES_BY_VIEW

    found: dict[str, dict] = {}
    for entry in data.get("response", {}).get("logs") or []:
        if entry.get("step") != "llm_output":
            continue
        raw = (entry.get("details") or {}).get("raw")
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        view = str(obj.get("view") or "").lower()
        if view in IR_TYPES_BY_VIEW and ("cloud" in obj or "corridors" in obj or "elements" in obj):
            found[view] = obj  # last write wins (post-correction)
    return found


def recompile_from_capture_irs(data: dict) -> str:
    """Compile capture IRs with *current* compiler + style + repair (no LLM)."""
    from app.adapters.outbound.diagram.architecture_docs import (
        VIEW_PAGE_NAMES,
        assign_logical_shape_types,
        assign_saas_shape_types,
        merge_mxfile_pages,
    )
    from app.adapters.outbound.diagram.compiler import compile_archimate_ir, compile_corridor_ir, compile_network_ir
    from app.adapters.outbound.diagram.xml_diagnostics import repair_xml
    from app.domain.ir import IR_TYPES_BY_VIEW

    irs = extract_irs_from_capture(data)
    if not irs:
        raise RuntimeError("No IR payloads found in capture logs")
    pages: list[tuple[str, str]] = []
    order = ["logical", "network", "security", "data_flow", "archimate"]
    for view in order:
        if view not in irs:
            continue
        model = IR_TYPES_BY_VIEW[view].model_validate(irs[view])
        title = VIEW_PAGE_NAMES[view]
        try:
            if view == "network":
                # A public-subnet/NAT pair used to be fabricated here whenever
                # the IR tripped "only one subnet tier", so a flat historical
                # capture could still be measured. That rule is a *warning*
                # now — a flat network compiles — so the workaround can never
                # fire, and measuring the geometry the compiler really produces
                # beats measuring a doctored IR.
                if not model.is_valid():
                    raise RuntimeError("; ".join(model.semantic_errors()[:2]))
                xml = compile_network_ir(model)
            elif view == "archimate":
                xml = compile_archimate_ir(model, page_name=title)
            else:
                xml = compile_corridor_ir(model, page_name=title)
        except Exception as exc:
            print(f"  skip {view}: {exc}", flush=True)
            continue
        if view == "logical":
            xml = assign_logical_shape_types(xml)
            xml = assign_saas_shape_types(xml)
        elif view == "archimate":
            # ArchiMate icons resolve only from _ARCHIMATE_BUILTIN_STYLES
            # (compiler/theme.py), never MCP search — assign_saas_shape_types
            # would be a no-op here at best and a wrong-icon risk at worst.
            pass
        else:
            xml = assign_saas_shape_types(xml)
        xml, _ = repair_xml(xml)
        pages.append((title, xml))
    if len(pages) == 1:
        return pages[0][1]
    return merge_mxfile_pages(pages)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", default=None)
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument(
        "--recompile",
        action="store_true",
        help="Recompile IRs from capture logs with current compiler (no LLM)",
    )
    ap.add_argument("--provider", default=None, help="Override capture provider (default: claude)")
    args = ap.parse_args()

    path, data = load_capture(args.capture)
    prompt, cap_provider, views, platform = prompt_from_capture(data)
    # Prefer stronger IR model for live regen unless explicitly set
    provider = args.provider or "claude"
    old_xml = (data.get("response") or {}).get("xml") or ""
    old = score_mxfile(old_xml, "capture")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "capture_score.json").write_text(json.dumps(old, indent=2), encoding="utf-8")

    new = None
    if args.recompile or args.skip_live:
        if args.recompile or args.skip_live:
            pass
    if args.recompile:
        print("Recompiling capture IRs with current compiler…", flush=True)
        new_xml = recompile_from_capture_irs(data)
        (OUT_DIR / "recompiled.xml").write_text(new_xml, encoding="utf-8")
        new = score_mxfile(new_xml, "recompiled")
        (OUT_DIR / "recompiled_score.json").write_text(json.dumps(new, indent=2), encoding="utf-8")
        # Also list which IR views were recovered
        recovered = list(extract_irs_from_capture(data).keys())
        print(f"Recovered IR views: {recovered}", flush=True)
    elif not args.skip_live:
        print(f"Running live pipeline provider={provider} views={views} …", flush=True)
        result = asyncio.run(run_live(prompt, provider, views, platform))
        new_xml = result.get("xml") or ""
        (OUT_DIR / "live.xml").write_text(new_xml, encoding="utf-8")
        meta = {
            "status": result.get("status"),
            "generation_path": result.get("generation_path"),
            "renderer_used": result.get("renderer_used"),
            "degraded_features": result.get("degraded_features"),
            "llm_usage": result.get("llm_usage"),
            "log_steps": result.get("_log_steps"),
        }
        (OUT_DIR / "live_meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        if result.get("status") != "success" or not new_xml:
            print("LIVE RUN FAILED:", meta, file=sys.stderr)
            print("Falling back to --recompile from capture IR logs…", file=sys.stderr)
            try:
                new_xml = recompile_from_capture_irs(data)
                (OUT_DIR / "recompiled.xml").write_text(new_xml, encoding="utf-8")
                new = score_mxfile(new_xml, "recompiled")
                (OUT_DIR / "recompiled_score.json").write_text(json.dumps(new, indent=2), encoding="utf-8")
            except Exception as exc:
                print(f"Recompile also failed: {exc}", file=sys.stderr)
                print_report(old, None, prompt, path)
                return 1
        else:
            new = score_mxfile(new_xml, "live")
            (OUT_DIR / "live_score.json").write_text(json.dumps(new, indent=2), encoding="utf-8")

    print_report(old, new, prompt, path)
    print(f"Artifacts: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
