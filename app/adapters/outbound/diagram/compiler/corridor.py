"""Compile a CorridorIR (Logical / Security / Data-flow) into draw.io XML.

Geometry is authoritative: ``layout_corridor_ir``
(``adapters/outbound/diagram/logical_layout.py``) computes final
corridor/shelf/leaf geometry directly from this IR's typed graph once at
compile time. The ``layout="ir"`` marker skips ``fit_containers_to_children``
reflow; ``repair_xml`` still applies structural hygiene, edge ports, and
overview/legend placement (no corridor re-layout). Surgical edits clear the
marker so post-edit fit can run.
Logical's "journey spine" (Persona/IdP external actors flowing into a leaf
inside ``domain_experience``) needs no special layout machinery of its own —
it's exactly ``ExternalActor`` plus ``Flow``, which ``emit_external_actors``/
``emit_flows`` already render generically. Actors render in their own band;
Portal stays nested inside ``domain_experience`` (see
``tests/test_corridor_ir_layout.py``).
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.legend import (
    LEGEND_ID,
    LEGEND_PLACEHOLDER_H,
    LEGEND_PLACEHOLDER_W,
    LEGEND_STYLE,
    build_legend,
)
from app.adapters.outbound.diagram.compiler.shared import (
    CompilerError,
    emit_flows,
    leaf_label,
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
from app.domain.ir.corridor import AnchoredCorridor, CorridorIR

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
    return _CorridorCompiler(ir, page_name).run_layered()


class _CorridorCompiler:
    def __init__(self, ir: CorridorIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)

    def run_layered(self) -> str:
        """Geometrically-authoritative path — every coordinate comes from
        ``layout_corridor_ir``, computed once. Marks ``layout="ir"`` so
        ``fit_containers_to_children`` does not reflow compiler geometry;
        ``repair_xml`` still runs structural + edge-port polish only.
        """
        geos, routes = layout_corridor_ir(self.ir)
        all_corridors = [*self.ir.corridors, *self.ir.anchored]
        for corridor in all_corridors:
            cg = geos[corridor.id]
            is_shelf = isinstance(corridor, AnchoredCorridor)
            self.w.vertex(
                id=corridor.id,
                label=corridor.label,
                style=(theme.shelf_style if is_shelf else theme.corridor_style)(corridor.id),
                x=cg.x, y=cg.y, w=cg.w, h=cg.h,
                parent="1",
            )
            for leaf in corridor.leaves:
                lg = geos[leaf.id]
                self.w.vertex(
                    id=leaf.id,
                    label=leaf_label(leaf),
                    style=theme.leaf_style(leaf.criticality),
                    x=lg.x, y=lg.y, w=lg.w, h=lg.h,
                    parent=corridor.id,
                    shape_type=leaf.shape_type,
                    criticality=leaf.criticality,
                )
        # This page's own layout is authoritative — mark it so repair_xml
        # skips re-deriving/overwriting it (see xml_diagnostics.py).
        self.w.mark_layout_authoritative()

        row_ids = {c.id for c in self.ir.corridors}
        row_left = min((g.x for cid, g in geos.items() if cid in row_ids), default=_ROW_ORIGIN_X)
        row_right = max((g.x + g.w for cid, g in geos.items() if cid in row_ids), default=_ROW_ORIGIN_X)
        # Align actors to the equalized corridor band (layout_corridor_ir
        # snaps every row corridor to _ROW_START_Y), not the pre-ELK
        # placeholder _ROW_TOP_Y=40 — otherwise External Actors floats ~100px
        # above the domain/zone/phase row (visible on every Logical capture).
        row_top = min((g.y for cid, g in geos.items() if cid in row_ids), default=_ROW_TOP_Y)
        content_bottom = max((g.y + g.h for cid, g in geos.items() if cid in {c.id for c in all_corridors}), default=_ROW_TOP_Y)

        self._emit_actors(row_left=row_left, row_right=row_right, top_y=row_top)
        emit_flows(self.w, self.ir.flows, routes=routes)

        legend_body = build_legend(self.ir)
        if legend_body is not None:
            # Placeholder geometry only — xml_diagnostics.ensure_legend_position
            # (runs unconditionally in repair_xml) pins this below the overview
            # panel once the panel's real position is known; this call site
            # owns content, not placement.
            self.w.vertex(
                id=LEGEND_ID, label=legend_body, style=LEGEND_STYLE,
                x=row_right + 40, y=_ROW_TOP_Y,
                w=LEGEND_PLACEHOLDER_W, h=LEGEND_PLACEHOLDER_H,
                parent="1",
            )

        page_w = row_right + 400
        page_h = content_bottom + 200
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    def _emit_actors(self, *, row_left: float, row_right: float, top_y: float) -> None:
        """Left actors (users/saas_ingress/on_prem) wrap in a grid-sized band
        container just left of the leftmost row corridor. Right actors
        (saas_egress) render as loose leaves.
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
                # Match ROW_PITCH_Y so label bands don't collide (band height
                # already assumes grid_fill pitch, not LEAF_H+LABEL alone).
                self.w.vertex(
                    id=actor.id, label=actor.label, style=theme.actor_style(actor.kind),
                    x=CONTAINER_PADDING_X,
                    y=CONTAINER_LABEL_HEIGHT + i * geo.ROW_PITCH_Y,
                    w=geo.LEAF_W, h=geo.LEAF_H, parent="actors_left",
                )

        y = top_y
        for actor in right:
            self.w.vertex(
                id=actor.id, label=actor.label, style=theme.actor_style(actor.kind),
                x=row_right + _ACTOR_GAP_X, y=y, w=geo.LEAF_W, h=geo.LEAF_H, parent="1",
            )
            y += geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y
