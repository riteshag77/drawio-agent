"""Convert architecture JSON into Draw.io XML."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.diagram.layout import build_children_map, build_layout, edge_sort_key
from app.diagram.xml_render import append_edge, append_flow_notes, append_vertex
from app.diagram.xml_tools import validate_and_fix_xml_legacy, wrap_with_mxfile
from app.models import ArchitectureDiagram
from app.patterns.template_merge import apply_three_tier_scaffold

if TYPE_CHECKING:
    from app.core.logging import StepLogger

DEFAULT_PAGE_W, DEFAULT_PAGE_H = 1169, 827

_PLATFORM_META = {
    "aws": ("Amazon Web Services", "#ED7100"),
    "gcp": ("Google Cloud Platform", "#4285F4"),
    "azure": ("Microsoft Azure", "#0078D4"),
    "multi": ("Multi-Cloud / Hybrid", "#8C4FFF"),
}


def sanitize_layout_hints(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Strip LLM-supplied coordinates so layout is computed server-side."""
    cleaned = [
        node.model_copy(update={"x": None, "y": None, "width": None, "height": None})
        for node in architecture.nodes
    ]
    return architecture.model_copy(update={"nodes": cleaned})


def _platform_meta(platform: str) -> tuple[str, str, str]:
    key = (platform or "aws").lower()
    label, accent = _PLATFORM_META.get(key, _PLATFORM_META["aws"])
    return key, label, accent


def _build_flow_notes(architecture) -> list[tuple[int, str, str]]:
    if steps := getattr(architecture, "flow_steps", None):
        ordered = sorted(steps, key=lambda step: step.number)
        return [(step.number, (step.title or "").strip(), (step.summary or "").strip()) for step in ordered]

    numbered = sorted(
        (node for node in architecture.nodes if getattr(node, "step_number", None) is not None),
        key=lambda node: node.step_number,
    )
    return [(node.step_number, (node.label or "").strip(), (node.description or "").strip()) for node in numbered]


def _prepare_architecture(architecture, logger: StepLogger | None) -> ArchitectureDiagram:
    if not isinstance(architecture, ArchitectureDiagram):
        return architecture
    prepared = sanitize_layout_hints(architecture)
    prepared = apply_three_tier_scaffold(prepared)
    if logger and prepared.pattern_id == "three_tier_web":
        logger.log("template", "Applied three_tier_web container scaffold", level="info")
    return prepared


def _open_graph_model(page_width: int, page_height: int) -> list[str]:
    return [
        f'<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{page_width}" pageHeight="{page_height}" math="0" shadow="0">',
        "  <root>",
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]


def generate_xml(architecture, platform: str = "aws", logger: StepLogger | None = None) -> str:
    """Render architecture nodes/edges as Draw.io XML."""
    architecture = _prepare_architecture(architecture, logger)
    platform_key, platform_label, accent = _platform_meta(platform)

    children_map, roots, nodes_by_id = build_children_map(architecture.nodes)
    layout = build_layout(nodes_by_id, children_map, roots)
    flow_notes = _build_flow_notes(architecture)

    page_width = DEFAULT_PAGE_W
    page_height = DEFAULT_PAGE_H
    xml = _open_graph_model(page_width, page_height)

    for node in architecture.nodes:
        parent_cell = node.parent_id if node.parent_id in nodes_by_id else "1"
        append_vertex(xml, node, layout, parent_cell, accent)

    cell_id = 100
    exit_counts: dict[str, int] = {}
    entry_counts: dict[str, int] = {}
    for edge in sorted(architecture.edges, key=lambda item: edge_sort_key(item, nodes_by_id)):
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue
        exit_slot = exit_counts.get(edge.source_id, 0)
        entry_slot = entry_counts.get(edge.target_id, 0)
        exit_counts[edge.source_id] = exit_slot + 1
        entry_counts[edge.target_id] = entry_slot + 1
        cell_id += 1
        append_edge(
            xml,
            edge,
            cell_id=cell_id,
            layout=layout,
            nodes_by_id=nodes_by_id,
            default_edge_color=accent,
            platform_key=platform_key,
            exit_slot=exit_slot,
            entry_slot=entry_slot,
        )

    if flow_notes:
        title = (getattr(architecture, "title", None) or "").strip()
        page_height = max(page_height, append_flow_notes(xml, flow_notes, accent, title))

    xml.extend(["  </root>", "</mxGraphModel>"])
    graph_xml = "\n".join(xml)
    graph_xml, fixes = validate_and_fix_xml_legacy(graph_xml)
    wrapped = wrap_with_mxfile(graph_xml, f"{platform_label} Architecture")

    if logger:
        page_match = re.search(r'pageWidth="(\d+)"\s+pageHeight="(\d+)"', graph_xml)
        details = {
            "platform": platform_key,
            "mx_cells": graph_xml.count("<mxCell"),
            "edges": len(architecture.edges),
            "xml_chars": len(wrapped),
            "xml_fixes": fixes,
        }
        if page_match:
            details["page_width"] = int(page_match.group(1))
            details["page_height"] = int(page_match.group(2))
        logger.log("xml", "Draw.io XML generated", level="success", details=details)

    return wrapped
