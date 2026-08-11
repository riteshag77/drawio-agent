"""End-to-end hygiene audit: compile IR → style pass → repair → assert invariants."""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Allow `python tools/audit_ir_hygiene.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.architecture_docs import (
    assign_logical_shape_types,
    assign_saas_shape_types,
)
from app.adapters.outbound.diagram.compiler import compile_archimate_ir, compile_corridor_ir
from app.adapters.outbound.diagram.logical_layout import INTER_CONTAINER_GAP
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import AnchoredCorridor, Corridor, ExternalActor, Flow, Leaf, LogicalIR
from tests.test_archimate_compiler_layout import realistic_archimate_ir
from tests.test_corridor_ir_layout import (
    realistic_data_flow_ir,
    realistic_logical_ir,
    realistic_security_ir,
)

def _geo(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    return (
        float(g.get("x") or 0),
        float(g.get("y") or 0),
        float(g.get("width") or 0),
        float(g.get("height") or 0),
    )


def audit_page(name: str, xml: str, *, row_prefix: str) -> list[str]:
    problems: list[str] = []
    root = ET.fromstring(xml)
    cells = {c.get("id"): c for c in root.iter("mxCell") if c.get("id")}
    layer = cells.get("1")
    if layer is None or layer.get("layout") != "ir":
        problems.append(f"{name}: missing layout=ir on layer 1")

    # Row corridors: shared Y/H, healthy gaps
    rows = [
        cid for cid in cells
        if cid.startswith(row_prefix) and not cid.startswith(row_prefix + "saas")
        and cells[cid].get("parent") == "1"
        and cells[cid].get("vertex") == "1"
    ]
    # Filter shelves: domain_saas, zone_external, phase_consume etc.
    shelves = {"domain_saas", "zone_external_saas", "phase_consume", "phase_saas_producer",
               "phase_saas_consumer", "phase_control", "actors_left"}
    rows = [r for r in rows if r not in shelves and "saas" not in r and r != "phase_control"]
    if len(rows) >= 2:
        ys = [_geo(cells[r])[1] for r in rows]
        hs = [_geo(cells[r])[3] for r in rows]
        if max(ys) - min(ys) > 2.0:
            problems.append(f"{name}: row Y stagger {sorted(zip(rows, ys))}")
        if max(hs) - min(hs) > 2.0:
            problems.append(f"{name}: row H unequal {sorted(zip(rows, hs))}")
        ordered = sorted(rows, key=lambda r: _geo(cells[r])[0])
        for a, b in zip(ordered, ordered[1:]):
            ax, _, aw, _ = _geo(cells[a])
            bx, _, _, _ = _geo(cells[b])
            gap = bx - (ax + aw)
            if gap < INTER_CONTAINER_GAP - 20:
                problems.append(f"{name}: tight gap {a}->{b} = {gap:.1f}")

    # Leaves: LEAF_W/H and fit parent
    for cid, cell in cells.items():
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        style = cell.get("style") or ""
        if "container=1" in style or cid in {"0", "1", "overview_panel", "legend_box", "actors_left"}:
            continue
        if cid.startswith(("step_badge", "domain_", "zone_", "phase_", "cloud", "region", "vpc", "subnet", "az_")):
            continue
        parent = cell.get("parent") or ""
        if parent in {"0", "1"} and cid.startswith("actors"):
            continue
        x, y, w, h = _geo(cell)
        if parent and parent in cells and parent not in {"0", "1"}:
            # leaf under corridor/shelf/actors
            if abs(w - geo.LEAF_W) > 1.5 or abs(h - geo.LEAF_H) > 1.5:
                # actors may also be LEAF sized
                problems.append(f"{name}: leaf {cid} size {w}x{h} != {geo.LEAF_W}x{geo.LEAF_H}")
            pw = _geo(cells[parent])[2]
            if x + w > pw + 1.5:
                problems.append(f"{name}: leaf {cid} overflows {parent} (x+w={x+w:.1f} > {pw:.1f})")

    # Edges: exit/entry present after repair
    for cid, cell in cells.items():
        if cell.get("edge") != "1":
            continue
        style = cell.get("style") or ""
        if "exitX=" not in style or "entryX=" not in style:
            problems.append(f"{name}: edge {cid} missing exit/entry ports")

    # Actors align to band if present
    if "actors_left" in cells and rows:
        ay = _geo(cells["actors_left"])[1]
        ry = min(_geo(cells[r])[1] for r in rows)
        if abs(ay - ry) > 2.0:
            problems.append(f"{name}: actors_left y={ay} vs row y={ry}")

    diag = diagnose_xml(xml)
    for e in diag.errors:
        problems.append(f"{name}: diagnose error: {e}")
    return problems


def pipeline_logical(ir: LogicalIR) -> str:
    xml = compile_corridor_ir(ir, page_name="Logical")
    xml = assign_logical_shape_types(xml)
    xml = assign_saas_shape_types(xml)
    xml, _ = repair_xml(xml)
    return xml


def pipeline_security() -> str:
    xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
    xml, _ = repair_xml(xml)
    return xml


def pipeline_data_flow() -> str:
    xml = compile_corridor_ir(realistic_data_flow_ir(with_control=True), page_name="Data flow")
    xml, _ = repair_xml(xml)
    return xml


def pipeline_archimate() -> str:
    xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
    xml, _ = repair_xml(xml)
    return xml


def audit_archimate_page(xml: str) -> list[str]:
    """ArchiMate's band -> grouping -> element containment doesn't share
    audit_page's row-corridor assumptions (LEAF_W/H, domain_/zone_/phase_
    prefixes, INTER_CONTAINER_GAP) — those are cloud-view-specific. This
    checks the ArchiMate-appropriate equivalents instead."""
    problems: list[str] = []
    root = ET.fromstring(xml)
    cells = {c.get("id"): c for c in root.iter("mxCell") if c.get("id")}
    layer = cells.get("1")
    if layer is None or layer.get("layout") != "ir":
        problems.append("ArchiMate: missing layout=ir on layer 1")

    band_ids = [cid for cid in cells if cid.startswith("archimate_band_")]
    if len(band_ids) >= 2:
        lefts = {_geo(cells[b])[0] for b in band_ids}
        if len(lefts) > 1:
            problems.append(f"ArchiMate: bands do not share a left edge: {sorted(lefts)}")

    for cid, cell in cells.items():
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        style = cell.get("style") or ""
        if "mxgraph.archimate3" not in style:
            continue  # not an element vertex (band/grouping/panel/badge)
        x, y, w, h = _geo(cell)
        if abs(w - geo.ARCHIMATE_ELEMENT_W) > 1.5 or abs(h - geo.ARCHIMATE_ELEMENT_H) > 1.5:
            problems.append(f"ArchiMate: element {cid} size {w}x{h} != {geo.ARCHIMATE_ELEMENT_W}x{geo.ARCHIMATE_ELEMENT_H}")
        parent = cell.get("parent") or ""
        if parent in cells:
            pw, ph = _geo(cells[parent])[2], _geo(cells[parent])[3]
            if x + w > pw + 1.5 or y + h > ph + 1.5:
                problems.append(f"ArchiMate: element {cid} overflows {parent}")

    diag = diagnose_xml(xml)
    for e in diag.errors:
        problems.append(f"ArchiMate: diagnose error: {e}")
    return problems


def capture_snapshot(path: Path) -> dict:
    """Summarize an on-disk capture (may predate the fix)."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def find_xml(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("xml", "diagram_xml") and isinstance(v, str) and "mxGraphModel" in v:
                    return v
                found = find_xml(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = find_xml(v)
                if found:
                    return found
        return None

    xml = find_xml(data)
    out = {"file": path.name, "has_xml": bool(xml), "pages": {}}
    if not xml:
        return out
    root = ET.fromstring(xml)
    for diagram in root.findall("diagram"):
        name = diagram.get("name") or "?"
        model = diagram.find(".//mxGraphModel")
        if model is None:
            out["pages"][name] = "compressed/missing"
            continue
        cells = list(model.findall(".//mxCell"))
        layout = next((c.get("layout") for c in cells if c.get("id") == "1"), "")
        tops = []
        for c in cells:
            if c.get("parent") != "1" or c.get("edge") == "1":
                continue
            g = c.find("mxGeometry")
            if g is None:
                continue
            tops.append((c.get("id"), float(g.get("x") or 0), float(g.get("y") or 0),
                         float(g.get("width") or 0), float(g.get("height") or 0)))
        domains = [t for t in tops if (t[0] or "").startswith(("domain_", "zone_", "phase_"))]
        rows = [t for t in domains if "saas" not in (t[0] or "") and t[0] != "phase_control"]
        ys = [t[2] for t in rows]
        leaf_overflow = 0
        by_id = {c.get("id"): c for c in cells if c.get("id")}
        for c in cells:
            if c.get("vertex") != "1" or c.get("parent") in {None, "0", "1"}:
                continue
            g = c.find("mxGeometry")
            pg = by_id.get(c.get("parent") or "")
            if g is None or pg is None:
                continue
            pgeom = pg.find("mxGeometry")
            if pgeom is None or "container=1" not in (pg.get("style") or ""):
                continue
            if float(g.get("x") or 0) + float(g.get("width") or 0) > float(pgeom.get("width") or 0) + 1:
                leaf_overflow += 1
        out["pages"][name] = {
            "layout": layout,
            "row_y_range": (max(ys) - min(ys)) if ys else None,
            "leaf_overflow_count": leaf_overflow,
            "row_ids": [t[0] for t in sorted(rows, key=lambda t: t[1])],
        }
    return out


def main() -> int:
    problems: list[str] = []
    print("=== LIVE PIPELINE (post-fix code) ===")
    for label, xml, prefix in (
        ("Logical", pipeline_logical(realistic_logical_ir()), "domain_"),
        ("Security", pipeline_security(), "zone_"),
        ("Data flow", pipeline_data_flow(), "phase_"),
    ):
        probs = audit_page(label, xml, row_prefix=prefix)
        problems.extend(probs)
        print(f"  {label}: {'OK' if not probs else probs}")

    archimate_probs = audit_archimate_page(pipeline_archimate())
    problems.extend(archimate_probs)
    print(f"  ArchiMate: {'OK' if not archimate_probs else archimate_probs}")

    # Dense Logical that previously staggered
    dense = LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[Leaf(id="portal", shape_type="logical_portal", label="Portal")]),
            Corridor(id="domain_core", label="Core", leaves=[
                Leaf(id=f"c{i}", shape_type="logical_capability", label=f"C{i}") for i in range(4)
            ]),
            Corridor(id="domain_data", label="Data", leaves=[Leaf(id="store", shape_type="logical_datastore", label="Store")]),
        ],
        anchored=[AnchoredCorridor(id="domain_saas", label="SaaS", anchor_id="domain_data",
                                   leaves=[Leaf(id="saas", shape_type="logical_saas", label="SaaS")])],
        external_actors=[ExternalActor(id="persona", kind="users", label="Persona")],
        flows=[
            Flow(source="portal", target="c0"), Flow(source="c0", target="c1"),
            Flow(source="c1", target="c2"), Flow(source="c2", target="c3"),
            Flow(source="c3", target="store"), Flow(source="portal", target="store"),
            Flow(source="store", target="saas"),
        ],
    )
    dense_xml = pipeline_logical(dense)
    dense_probs = audit_page("Logical-dense", dense_xml, row_prefix="domain_")
    problems.extend(dense_probs)
    print(f"  Logical-dense: {'OK' if not dense_probs else dense_probs}")

    print("\n=== ON-DISK CAPTURES (historical; may predate fix) ===")
    captures = sorted(Path("data/captures").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    for path in captures:
        snap = capture_snapshot(path)
        print(f"  {snap['file']}")
        for pname, info in snap.get("pages", {}).items():
            print(f"    {pname}: {info}")

    print("\n=== SUMMARY ===")
    if problems:
        print(f"FAIL {len(problems)} live-pipeline problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS live pipeline invariants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
