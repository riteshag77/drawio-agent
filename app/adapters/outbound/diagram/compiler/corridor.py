"""Compile a CorridorIR (Logical / Security / Data-flow) into draw.io XML.

**Security and Data-flow** (``_LAYERED_VIEWS`` below) are geometrically
authoritative, the same way Network's compiler already is: ``layout_corridor_ir``
(``adapters/outbound/diagram/logical_layout.py``) computes final corridor/
shelf/leaf geometry directly from this IR's own typed graph, once, here — the
XML-based ``apply_layered_corridor_layout``/``enforce_view_journey_columns``
pass (invoked unconditionally by ``xml_diagnostics.repair_xml`` for these page
names) is skipped for pages this compiler produced (see
``xml_diagnostics.py``'s ``layout="ir"`` marker check), since it would
otherwise re-derive the exact graph this compiler already had as typed Python
objects moments earlier by parsing this module's own XML output back into
cells. Verified equivalent to (and, in two documented, pre-existing-bug cases,
more correct than) the old XML-based pass's output — see
``tests/test_corridor_ir_layout.py``.

**Logical** is not yet on this path: ``domain_experience`` gets bespoke
"journey spine" treatment (``placement_fixers._layout_logical_journey_spine``)
that ``layout_corridor_ir`` does not implement — a structurally different,
already-flagged-as-deferred piece of work (a journey spine, not a side band).
Logical keeps the original placeholder-geometry-then-XML-repair path below
until that's ported too.
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
from app.adapters.outbound.diagram.logical_layout import (
    CONTAINER_LABEL_HEIGHT,
    CONTAINER_MIN_H,
    CONTAINER_MIN_W,
    CONTAINER_PADDING_X,
    CONTAINER_PADDING_Y,
    INTER_CONTAINER_GAP,
    grid_fill,
    layout_corridor_ir,
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

#: Views whose geometry `layout_corridor_ir` computes authoritatively — see
#: the module docstring for why Logical isn't here yet.
_LAYERED_VIEWS = frozenset({"security", "data_flow"})


def compile_corridor_ir(ir: CorridorIR, *, page_name: str) -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    if ir.view in _LAYERED_VIEWS:
        return _CorridorCompiler(ir, page_name).run_layered()
    return _CorridorCompiler(ir, page_name).run()


class _CorridorCompiler:
    def __init__(self, ir: CorridorIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)

    def run_layered(self) -> str:
        """Geometrically-authoritative path (Security / Data-flow) — every
        coordinate comes from ``layout_corridor_ir``, computed once. No later
        repair pass needs to (or does) touch this page's corridor/shelf/leaf
        geometry; see ``xml_diagnostics.repair_xml``'s ``layout="ir"`` skip.
        """
        geos = layout_corridor_ir(self.ir)
        all_corridors = [*self.ir.corridors, *self.ir.anchored]
        for corridor in all_corridors:
            cg = geos[corridor.id]
            is_shelf = isinstance(corridor, AnchoredCorridor)
            self.w.vertex(
                id=corridor.id,
                label=corridor.label,
                style=theme.shelf_style() if is_shelf else theme.corridor_style(),
                x=cg.x, y=cg.y, w=cg.w, h=cg.h,
                parent="1",
            )
            for leaf in corridor.leaves:
                lg = geos[leaf.id]
                self.w.vertex(
                    id=leaf.id,
                    label=leaf.label if not leaf.annotation else f"{leaf.label} [{leaf.annotation}]",
                    style=theme.leaf_style(),
                    x=lg.x, y=lg.y, w=lg.w, h=lg.h,
                    parent=corridor.id,
                    shape_type=leaf.shape_type,
                )
        # This page's own layout is authoritative — mark it so repair_xml
        # skips re-deriving/overwriting it (see xml_diagnostics.py).
        self.w.mark_layout_authoritative()

        row_ids = {c.id for c in self.ir.corridors}
        row_left = min((g.x for cid, g in geos.items() if cid in row_ids), default=_ROW_ORIGIN_X)
        row_right = max((g.x + g.w for cid, g in geos.items() if cid in row_ids), default=_ROW_ORIGIN_X)
        content_bottom = max((g.y + g.h for cid, g in geos.items() if cid in {c.id for c in all_corridors}), default=_ROW_TOP_Y)

        self._emit_actors(row_left=row_left, row_right=row_right, top_y=_ROW_TOP_Y)
        emit_flows(self.w, self.ir.flows)

        page_w = row_right + 400
        page_h = content_bottom + 200
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    def _emit_actors(self, *, row_left: float, row_right: float, top_y: float) -> None:
        """Left actors (users/saas_ingress/on_prem) wrap in a grid-sized band
        container just left of the leftmost row corridor, mirroring Part A's
        Fix 2c (`placement_fixers._containerize_corridor_actors`) — needed
        here too now that this path skips the fixer that used to do it. Right
        actors (saas_egress) render as loose leaves, matching that same
        fixer's scope (it never containerized the right side either).
        """
        left = [a for a in self.ir.external_actors if a.kind in ("users", "saas_ingress", "on_prem")]
        right = [a for a in self.ir.external_actors if a.kind == "saas_egress"]

        if left:
            _cols, _rows, content_w, content_h = grid_fill(len(left), cols=1)
            band_w = max(CONTAINER_MIN_W, content_w + CONTAINER_PADDING_X * 2)
            # + geo.LABEL_BAND_H: actors render with verticalLabelPosition=
            # bottom too (theme.actor_style()) — same reasoning as
            # layout_corridor_ir's _place_leaves.
            band_h = max(CONTAINER_MIN_H, content_h + geo.LABEL_BAND_H + CONTAINER_LABEL_HEIGHT + CONTAINER_PADDING_Y)
            band_x = max(20.0, row_left - band_w - INTER_CONTAINER_GAP)
            self.w.vertex(
                id="actors_left", label="External Actors", style=theme.corridor_style(),
                x=band_x, y=top_y, w=band_w, h=band_h, parent="1",
            )
            for i, actor in enumerate(left):
                self.w.vertex(
                    id=actor.id, label=actor.label, style=theme.actor_style(),
                    x=CONTAINER_PADDING_X, y=CONTAINER_LABEL_HEIGHT + i * (geo.LEAF_H + geo.LABEL_BAND_H),
                    w=geo.LEAF_W, h=geo.LEAF_H, parent="actors_left",
                )

        y = top_y
        for actor in right:
            self.w.vertex(
                id=actor.id, label=actor.label, style=theme.actor_style(),
                x=row_right + _ACTOR_GAP_X, y=y, w=geo.LEAF_W, h=geo.LEAF_H, parent="1",
            )
            y += geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y

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
