"""Post-compile visual QA for IR diagrams (``DIAGRAM_VLM_QA``).

Fail-open: never blocks shipping a diagram. When enabled, runs the same
layout checklist a vision critic would care about (overlap, edge-through-node,
missing ports) via ``diagnose_xml`` / ``validate_layout``, then applies at most
one corrective pass (edge re-anchor) per page. Operates on every ``<diagram>``
page in an mxfile (not only the first).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from app.adapters.outbound.diagram.edge_routing import fix_edge_anchors_from_geometry
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, validate_layout
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document


def _page_xml(diagram: ET.Element) -> str:
    name = diagram.get("name") or "Architecture"
    model = diagram.find("mxGraphModel")
    if model is None:
        return ""
    return ensure_diagram_document(ET.tostring(model, encoding="unicode"), name)


def _qa_one_page(xml: str) -> tuple[str, list[str]]:
    issues: list[str] = []
    try:
        issues.extend(validate_layout(xml))
        diag = diagnose_xml(xml)
        issues.extend(f"error: {e}" for e in diag.errors)
        issues.extend(f"warn: {w}" for w in diag.warnings)
    except Exception as exc:
        return xml, [f"inspect failed: {exc}"]
    if not issues:
        return xml, []
    try:
        return fix_edge_anchors_from_geometry(xml), issues
    except Exception as exc:
        return xml, issues + [f"corrective pass failed: {exc}"]


def run_visual_qa(xml: str, *, logger: Any = None) -> str:
    """Inspect every page in ``xml``; optionally re-route edges once; always ship."""
    try:
        tree = ET.fromstring(xml if xml.lstrip().startswith("<") else ensure_diagram_document(xml, "Architecture"))
    except ET.ParseError as exc:
        if logger:
            logger.log("vlm_qa", f"Visual QA parse failed (fail-open): {exc}", level="warning")
        return xml

    if tree.tag != "mxfile":
        out, issues = _qa_one_page(xml)
        if logger:
            if issues:
                logger.log(
                    "vlm_qa",
                    f"Visual QA found {len(issues)} issue(s); one edge re-anchor pass",
                    level="warning",
                    details={"issues": issues[:12]},
                )
            else:
                logger.log("vlm_qa", "Visual QA clean — no layout issues", level="success")
        return out

    diagrams = list(tree.findall("diagram"))
    if not diagrams:
        return xml

    total_issues = 0
    for diagram in diagrams:
        page = _page_xml(diagram)
        if not page:
            continue
        fixed, issues = _qa_one_page(page)
        total_issues += len(issues)
        try:
            fixed_tree = ET.fromstring(fixed)
        except ET.ParseError:
            continue
        model = fixed_tree.find(".//mxGraphModel")
        if model is None:
            continue
        old = diagram.find("mxGraphModel")
        if old is not None:
            diagram.remove(old)
        diagram.append(model)

    if logger:
        if total_issues:
            logger.log(
                "vlm_qa",
                f"Visual QA found {total_issues} issue(s) across {len(diagrams)} page(s); "
                "applied one edge re-anchor pass where needed",
                level="warning",
            )
        else:
            logger.log("vlm_qa", "Visual QA clean — no layout issues", level="success")
    return ET.tostring(tree, encoding="unicode")
