"""Convert architecture JSON into Draw.io XML.

Pipeline:
  sanitize → pattern blueprint → topology repair → scaffold → normalize
  → build_layout → layout_bands → XML vertices/edges → quality gates
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.layout import (
    absolute_layout_bounds,
    build_children_map,
    build_layout,
    edge_sort_key,
)
from app.adapters.outbound.diagram.layout_settings import layout_settings, platform_meta
from app.adapters.outbound.diagram.quality_gates import all_gates_passed, run_quality_gates
from app.adapters.outbound.diagram.service_bands import layout_bands
from app.adapters.outbound.diagram.template_merge import apply_pattern_blueprint, apply_three_tier_scaffold
from app.adapters.outbound.diagram.topology_repair import repair_topology
from app.adapters.outbound.diagram.xml_render import append_edge, append_flow_notes, append_vertex
from app.adapters.outbound.diagram.xml_tools import validate_and_fix_xml_legacy, wrap_with_mxfile
from app.domain.entities import ArchitectureDiagram

if TYPE_CHECKING:
    from app.core.logging import StepLogger


def sanitize_layout_hints(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    cleaned = [
        node.model_copy(update={"x": None, "y": None, "width": None, "height": None})
        for node in architecture.nodes
    ]
    return architecture.model_copy(update={"nodes": cleaned})


def _build_flow_notes(architecture) -> list[tuple[int, str, str]]:
    if steps := getattr(architecture, "flow_steps", None):
        ordered = sorted(steps, key=lambda step: step.number)
        return [(step.number, (step.title or "").strip(), (step.summary or "").strip()) for step in ordered]
    numbered = sorted(
        (node for node in architecture.nodes if getattr(node, "step_number", None) is not None),
        key=lambda node: node.step_number,
    )
    return [(node.step_number, (node.label or "").strip(), (node.description or "").strip()) for node in numbered]


def _prepare_architecture(architecture, platform: str = "aws") -> ArchitectureDiagram:
    if not isinstance(architecture, ArchitectureDiagram):
        return architecture
    prepared = sanitize_layout_hints(architecture)
    prepared = apply_pattern_blueprint(prepared, prepared.pattern_id, platform)
    prepared = repair_topology(prepared)
    prepared = apply_three_tier_scaffold(prepared)
    return normalize_architecture(prepared)


def _finalize_page(
    layout: dict,
    nodes_by_id: dict,
    children_map: dict,
    roots: list[str],
    flow_notes: list,
    *,
    settings,
) -> tuple[list[str], int, int, int, int, int]:
    """Place bands near anchors, then notes below; return page geometry."""
    main_bounds = absolute_layout_bounds(layout, nodes_by_id, children_map, roots)
    layout_bands(layout, nodes_by_id, children_map, main_bounds=main_bounds)

    all_roots = roots + [nid for nid in nodes_by_id if nid.startswith("band_")]
    _, _, max_x, max_y = absolute_layout_bounds(layout, nodes_by_id, children_map, all_roots)

    notes_y = max_y + settings.flow_notes_gap
    notes_height = (52 + len(flow_notes) * settings.flow_notes_row_h) if flow_notes else 0
    content_bottom = notes_y + notes_height if flow_notes else max_y

    diagram_width = max(max_x + settings.page_margin, settings.default_page_w)
    page_width = max(diagram_width, settings.default_page_w)
    page_height = max(content_bottom + settings.page_margin, settings.default_page_h)
    xml = [
        f'<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{page_width}" pageHeight="{page_height}" math="0" shadow="0">',
        "  <root>",
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]
    return xml, page_width, page_height, max_x, max_y, notes_y


def generate_xml(architecture, platform: str = "aws", logger: StepLogger | None = None) -> str:
    settings = layout_settings()
    architecture = _prepare_architecture(architecture, platform=platform)
    platform_key, platform_label, accent = platform_meta(platform)

    children_map, roots, nodes_by_id = build_children_map(architecture.nodes)
    layout = build_layout(nodes_by_id, children_map, roots)
    flow_notes = _build_flow_notes(architecture)

    xml, page_width, page_height, max_x, max_y, notes_y = _finalize_page(
        layout, nodes_by_id, children_map, roots, flow_notes, settings=settings,
    )

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
            xml, edge, cell_id=cell_id, layout=layout, nodes_by_id=nodes_by_id,
            default_edge_color=accent, platform_key=platform_key,
            exit_slot=exit_slot, entry_slot=entry_slot,
        )

    if flow_notes:
        title = (getattr(architecture, "title", None) or "").strip()
        notes_width = min(settings.flow_notes_max_w, max(settings.flow_notes_min_w, max_x - settings.flow_notes_x))
        notes_page_w, notes_bottom = append_flow_notes(
            xml, flow_notes, accent, title, notes_y=notes_y, notes_width=notes_width,
        )
        page_width = max(page_width, notes_page_w)
        page_height = max(page_height, notes_bottom + settings.page_margin, max_y + settings.page_margin)

    page_width = max(page_width, max_x + settings.page_margin)
    page_height = max(page_height, max_y + settings.page_margin)

    gate_results = run_quality_gates(
        architecture, layout, nodes_by_id, children_map,
        page_w=page_width, page_h=page_height, max_x=max_x, max_y=max_y,
    )
    if settings.quality_gates_blocking and not all_gates_passed(gate_results) and logger:
        logger.log("quality_gates", "Quality gate failures", level="warning", details={
            "failed": [{"gate": g.gate, "message": g.message} for g in gate_results if not g.passed],
        })

    xml[0] = re.sub(
        r'pageWidth="\d+"\s+pageHeight="\d+"',
        f'pageWidth="{page_width}" pageHeight="{page_height}"',
        xml[0],
    )
    xml.extend(["  </root>", "</mxGraphModel>"])
    graph_xml = "\n".join(xml)
    graph_xml, fixes = validate_and_fix_xml_legacy(graph_xml)
    wrapped = wrap_with_mxfile(graph_xml, f"{platform_label} Architecture")

    if logger:
        logger.log("xml", "Draw.io XML generated", level="success", details={
            "platform": platform_key,
            "mx_cells": graph_xml.count("<mxCell"),
            "edges": len(architecture.edges),
            "xml_chars": len(wrapped),
            "xml_fixes": fixes,
            "quality_gates": {g.gate: g.passed for g in gate_results},
        })

    return wrapped
