"""Compile a ``GenericDiagramIR`` into draw.io XML.

No ELK, no MCP icon resolution, no cloud-platform styling, no multi-view
fan-out — a generic (as-is) diagram is always a single page. The LLM's
``row``/``col`` hints, when present, are trusted as coarse grid-cell
ordinals (never pixels); when absent, nodes fall back to the same
deterministic ``<=4``-per-row grid every other view uses.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import generic_theme as theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.domain.ir.generic import GenericDiagramIR, GenericNode

_ORIGIN_X = 40.0
_ORIGIN_Y = 40.0
_BLOCK_GAP_X = 60.0
_PITCH = geo.DATA_CANONICAL_PITCH


def compile_generic_ir(ir: GenericDiagramIR, *, page_name: str = "Diagram") -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _GenericCompiler(ir, page_name).run()


def _place_nodes(nodes: list[GenericNode]) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Return ``{node_id: (x, y)}`` relative to a (0, 0) content origin, plus
    the content box's ``(width, height)`` — mirrors ``geometry_spec.grid_size``'s
    formula so container sizing stays consistent with every other view.
    """
    if not nodes:
        return {}, 0.0, 0.0
    hinted = [n for n in nodes if n.row is not None or n.col is not None]
    occupied: set[tuple[int, int]] = set()
    cells: dict[str, tuple[int, int]] = {}

    def _place(row: int, col: int) -> tuple[int, int]:
        while (row, col) in occupied:
            col += 1
        occupied.add((row, col))
        return row, col

    if hinted:
        next_row = 0
        for node in nodes:
            row = node.row if node.row is not None else next_row
            col = node.col if node.col is not None else 0
            row, col = _place(row, col)
            cells[node.id] = (row, col)
            next_row = max(next_row, row + 1)
    else:
        cols = min(geo.LEAF_GRID_MAX_COLS, len(nodes))
        for index, node in enumerate(nodes):
            row, col = _place(index // cols, index % cols)
            cells[node.id] = (row, col)

    max_row = max(r for r, _c in cells.values())
    max_col = max(c for _r, c in cells.values())
    positions = {nid: (col * _PITCH, row * geo.ROW_PITCH_Y) for nid, (row, col) in cells.items()}
    content_w = max_col * _PITCH + geo.LEAF_W
    content_h = max_row * geo.ROW_PITCH_Y + geo.LEAF_H + geo.LABEL_BAND_H
    return positions, content_w, content_h


class _GenericCompiler:
    def __init__(self, ir: GenericDiagramIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)

    def run(self) -> str:
        by_group: dict[str, list[GenericNode]] = {}
        ungrouped: list[GenericNode] = []
        for node in self.ir.nodes:
            if node.group:
                by_group.setdefault(node.group, []).append(node)
            else:
                ungrouped.append(node)

        cursor_x = _ORIGIN_X
        max_bottom = _ORIGIN_Y

        if ungrouped:
            positions, content_w, content_h = _place_nodes(ungrouped)
            for node in ungrouped:
                rel_x, rel_y = positions[node.id]
                self.w.vertex(
                    id=node.id, label=node.label,
                    style=theme.node_style(node.shape, fill_color=node.fill_color, stroke_color=node.stroke_color),
                    x=cursor_x + rel_x, y=_ORIGIN_Y + rel_y,
                    w=geo.LEAF_W, h=geo.LEAF_H, parent="1",
                )
            cursor_x += content_w + _BLOCK_GAP_X
            max_bottom = max(max_bottom, _ORIGIN_Y + content_h)

        for group in self.ir.groups:
            nodes = by_group.get(group.id, [])
            positions, content_w, content_h = _place_nodes(nodes)
            box_w = max(content_w + geo.PAD * 2, 160.0)
            box_h = max(geo.TITLE_PAD + content_h + geo.PAD, 120.0)
            self.w.vertex(
                id=group.id, label=group.label, style=theme.group_style(group.boundary),
                x=cursor_x, y=_ORIGIN_Y, w=box_w, h=box_h, parent="1",
            )
            for node in nodes:
                rel_x, rel_y = positions[node.id]
                self.w.vertex(
                    id=node.id, label=node.label,
                    style=theme.node_style(node.shape, fill_color=node.fill_color, stroke_color=node.stroke_color),
                    x=geo.PAD + rel_x, y=geo.TITLE_PAD + rel_y,
                    w=geo.LEAF_W, h=geo.LEAF_H, parent=group.id,
                )
            cursor_x += box_w + _BLOCK_GAP_X
            max_bottom = max(max_bottom, _ORIGIN_Y + box_h)

        for index, edge in enumerate(self.ir.edges):
            self.w.edge(
                id=f"edge_{index}", source=edge.source, target=edge.target, label=edge.label,
                style=theme.edge_style(
                    line_style=edge.line_style, dashed=edge.dashed, arrow=edge.arrow, color=edge.color,
                ),
            )

        self.w.mark_layout_authoritative()
        page_w = max(cursor_x - _BLOCK_GAP_X + 200.0, 400.0)
        page_h = max_bottom + 200.0
        return self.w.to_xml(page_width=page_w, page_height=page_h)
