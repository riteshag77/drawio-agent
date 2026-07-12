"""Convert architecture JSON into Draw.io XML — minimal pipeline.

Flow: normalize → layout → build XML → quality gates (advisory)
"""

from __future__ import annotations

import html as html_module
import re
from datetime import date
from typing import TYPE_CHECKING

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.layout import (
    absolute_layout_bounds,
    build_children_map,
    build_layout,
    edge_sort_key,
)
from app.adapters.outbound.diagram.layout_settings import layout_settings, platform_meta
from app.adapters.outbound.diagram.xml_render import append_edge, append_flow_notes, append_vertex
from app.adapters.outbound.diagram.xml_tools import validate_and_fix_xml_legacy, wrap_with_mxfile
from app.domain.entities import ArchitectureDiagram

if TYPE_CHECKING:
    from app.core.logging import StepLogger


def html_escape(text: str) -> str:
    return html_module.escape(str(text or ""), quote=True)


def _build_flow_notes(architecture) -> list[tuple[int, str, str]]:
    if steps := getattr(architecture, "flow_steps", None):
        ordered = sorted(steps, key=lambda step: step.number)
        return [(step.number, (step.title or "").strip(), (step.summary or "").strip()) for step in ordered]
    numbered = sorted(
        (node for node in architecture.nodes if getattr(node, "step_number", None) is not None),
        key=lambda node: node.step_number,
    )
    return [(node.step_number, (node.label or "").strip(), (node.description or "").strip()) for node in numbered]


def generate_xml(
    architecture,
    platform: str = "aws",
    logger: StepLogger | None = None,
    *,
    prepare: bool = True,
    style_overrides: dict[str, str] | None = None,
) -> str:
    settings = layout_settings()
    if prepare:
        if not isinstance(architecture, ArchitectureDiagram):
            return str(architecture)
        architecture = normalize_architecture(architecture)
    platform_key, platform_label, accent = platform_meta(platform)

    children_map, roots, nodes_by_id = build_children_map(architecture.nodes)
    layout = build_layout(nodes_by_id, children_map, roots)
    flow_notes = _build_flow_notes(architecture)

    min_x, min_y, max_x, max_y = absolute_layout_bounds(layout, nodes_by_id, children_map, roots)

    notes_y = max_y + settings.flow_notes_gap
    notes_height = (52 + len(flow_notes) * settings.flow_notes_row_h) if flow_notes else 0
    content_bottom = notes_y + notes_height if flow_notes else max_y

    diagram_width = max(max_x + settings.page_margin, settings.default_page_w)
    page_width = max(diagram_width, settings.default_page_w)
    page_height = max(content_bottom + settings.page_margin, settings.default_page_h)

    xml = [
        f'<mxGraphModel dx="2800" dy="1600" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{page_width}" pageHeight="{page_height}" math="0" shadow="0">',
        "  <root>",
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
        f'    <mxCell id="canvas_bg" value="" style="rounded=0;whiteSpace=wrap;html=1;'
        f'fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
        f'<mxGeometry x="0" y="0" width="{page_width}" height="{page_height}" as="geometry" /></mxCell>',
    ]

    heading = (getattr(architecture, "title", None) or "").strip() or "Architecture"
    meta = f"Draw.io AI | {date.today().isoformat()} | v1"
    title_value = (
        f"&lt;font style=&quot;font-size: 16px;&quot;&gt;&lt;b&gt;{html_escape(heading)}"
        f"&lt;/b&gt;&lt;/font&gt;&lt;div&gt;&lt;font style=&quot;font-size: 10px;&quot;&gt;"
        f"{html_escape(meta)}&lt;/font&gt;&lt;/div&gt;"
    )
    xml.append(
        f'    <mxCell id="diagram_title" value="{title_value}" '
        f'style="text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;rounded=0;'
        f'fontFamily=Helvetica;spacing=4;fontColor=#232F3E;" '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="40" y="20" width="560" height="48" as="geometry" /></mxCell>'
    )

    LAYER_ORDER = {"background": 0, "default": 1, "foreground": 2}

    def _node_depth(node_id: str) -> int:
        depth = 0
        seen: set[str] = set()
        current = nodes_by_id.get(node_id)
        while current and current.parent_id and current.parent_id in nodes_by_id:
            if current.parent_id in seen:
                break
            seen.add(current.parent_id)
            depth += 1
            current = nodes_by_id[current.parent_id]
        return depth

    for node in sorted(architecture.nodes, key=lambda n: (LAYER_ORDER.get(n.layer, 1), _node_depth(n.id), n.id)):
        parent_cell = node.parent_id if node.parent_id in nodes_by_id else "1"
        append_vertex(xml, node, layout, parent_cell, accent, style_overrides=style_overrides)

    cell_id = 100
    for edge in sorted(architecture.edges, key=lambda item: edge_sort_key(item, nodes_by_id)):
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue
        cell_id += 1
        append_edge(
            xml, edge, cell_id=cell_id, layout=layout, nodes_by_id=nodes_by_id,
            default_edge_color=accent, platform_key=platform_key,
        )

    if flow_notes:
        notes_page_w, notes_bottom = append_flow_notes(
            xml, flow_notes, accent, heading,
            notes_y=notes_y, notes_width=min(settings.flow_notes_max_w, max(settings.flow_notes_min_w, max_x - settings.flow_notes_x)),
        )
        page_width = max(page_width, notes_page_w)
        page_height = max(page_height, notes_bottom + settings.page_margin, max_y + settings.page_margin)

    page_width = max(page_width, max_x + settings.page_margin)
    page_height = max(page_height, max_y + settings.page_margin)

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
        })

    return wrapped


def prepare_and_generate_xml(
    architecture,
    platform: str = "aws",
    logger: StepLogger | None = None,
    *,
    style_overrides: dict[str, str] | None = None,
) -> tuple[ArchitectureDiagram, str]:
    prepared = normalize_architecture(architecture)
    xml = generate_xml(prepared, platform, logger, prepare=False, style_overrides=style_overrides)
    return prepared, xml
