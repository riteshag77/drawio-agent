"""Compile a CorridorIR (Logical / Security / Data-flow) into draw.io XML.

Unlike Network, geometry here is genuinely a formality: this repo already has a
deterministic rank-and-order layout engine
(``adapters/outbound/diagram/logical_layout.py``, invoked unconditionally for
these three page names via
``placement_fixers.enforce_view_journey_columns`` <- ``xml_diagnostics.repair_xml``)
that recomputes every corridor's and leaf's x/y/width/height from the
container graph, discarding whatever this compiler emits. That's confirmed by
reading the call chain, not assumed — see the module docstring on
``domain/ir/corridor.py``.

So this compiler's only real job is emitting a *structurally* correct document
(right nesting, right ids, valid edges) — the placeholder geometry only needs
to be sane enough that diagnostics run cleanly on the rare path that inspects
pre-repair output. It reuses the same ``geometry_spec``/``theme``/``emit``
building blocks as the Network compiler rather than inventing its own — that
sharing is the actual DRY payoff of splitting compiler concerns into small
modules instead of one Network-shaped file.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import (
    Box as _Box,
    CompilerError,
    emit_external_actors,
    emit_flows,
    emit_leaf_grid,
)
from app.domain.ir.corridor import AnchoredCorridor, Corridor, CorridorIR

_CORRIDOR_GAP_X = 60.0
_SHELF_GAP_Y = 60.0
_ROW_TOP_Y = 40.0
#: Left margin for the main row, so left-side external actors (rendered to the
#: row's left) still land at non-negative x — same fix as Network's
#: CLOUD_ORIGIN_X, for the same reason: diagnostics flags negative positions,
#: and it's needless noise on the pre-repair pass even though the real layout
#: engine repositions everything anyway.
_ROW_ORIGIN_X = 300.0
#: Horizontal clear space between an external-actor column and the row —
#: mirrors Network's geo.ACTOR_GAP_X but the row's own origin/right-edge
#: differ from Network's Cloud box, so this stays a local constant.
_ACTOR_GAP_X = 80.0


def compile_corridor_ir(ir: CorridorIR, *, page_name: str) -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _CorridorCompiler(ir, page_name).run()


class _CorridorCompiler:
    def __init__(self, ir: CorridorIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)

    def run(self) -> str:
        row_x = _ROW_ORIGIN_X
        row_bottom = _ROW_TOP_Y
        row_boxes: dict[str, tuple[float, _Box]] = {}
        for corridor in self.ir.corridors:
            box = self._emit_corridor(corridor, x=row_x, y=_ROW_TOP_Y)
            row_boxes[corridor.id] = (row_x, box)
            row_x += box.width + _CORRIDOR_GAP_X
            row_bottom = max(row_bottom, _ROW_TOP_Y + box.height)
        row_right = row_x - _CORRIDOR_GAP_X if self.ir.corridors else 0.0

        # Shelves anchored under a specific row corridor stack in list order;
        # shelves anchored under the whole row start below every corridor.
        next_y_under = {cid: _ROW_TOP_Y + box.height + _SHELF_GAP_Y for cid, (_, box) in row_boxes.items()}
        whole_row_y = row_bottom + _SHELF_GAP_Y

        for shelf in self.ir.anchored:
            if shelf.anchor_id is not None:
                x, _ = row_boxes[shelf.anchor_id]
                y = next_y_under[shelf.anchor_id]
                box = self._emit_corridor(shelf, x=x, y=y)
                next_y_under[shelf.anchor_id] = y + box.height + _SHELF_GAP_Y
                row_right = max(row_right, x + box.width)
            else:
                box = self._emit_corridor(shelf, x=0.0, y=whole_row_y)
                whole_row_y += box.height + _SHELF_GAP_Y
                row_right = max(row_right, box.width)

        content_bottom = max(
            [whole_row_y, *next_y_under.values(), row_bottom],
        )
        emit_external_actors(
            self.w, self.ir.external_actors,
            left_x=_ROW_ORIGIN_X - geo.LEAF_W - _ACTOR_GAP_X,
            right_x=row_right + _ACTOR_GAP_X,
            top_y=_ROW_TOP_Y,
        )
        emit_flows(self.w, self.ir.flows)

        page_w = row_right + 400
        page_h = content_bottom + 200
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    def _emit_corridor(self, corridor: Corridor, *, x: float, y: float) -> _Box:
        box = emit_leaf_grid(self.w, corridor.leaves, corridor.id, is_group=False)
        is_shelf = isinstance(corridor, AnchoredCorridor)
        self.w.vertex(
            id=corridor.id,
            label=corridor.label,
            style=theme.shelf_style() if is_shelf else theme.corridor_style(),
            x=x, y=y, w=box.width, h=box.height,
            parent="1",
        )
        return box
