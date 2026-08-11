"""Compile a CorridorIR (Logical / Security / Data-flow) into draw.io XML.

Geometry is authoritative: ``layout_corridor_ir``
(``adapters/outbound/diagram/logical_layout.py``) computes final
corridor/shelf/leaf geometry directly from this IR's typed graph once at
compile time. The ``layout="ir"`` marker skips ``fit_containers_to_children``
reflow; ``repair_xml`` still applies structural hygiene, edge ports, and
overview/legend placement (no corridor re-layout). Surgical edits clear the
marker so post-edit fit can run.

**Logical** keeps ``domain_*`` corridors in JSON as L→R ranking metadata, but
compiles to a **flat storyboard** (no Experience/Core/Data boxes, no
``actors_left`` band) — optional one dashed ``cloud_boundary`` when cloud
``shape_type`` leaves exist. Security / Data-flow still draw corridor boxes.
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
    _row_rank_key,
    grid_fill,
    layout_corridor_ir,
)
from app.domain.ir.base import Leaf
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

CLOUD_BOUNDARY_ID = "cloud_boundary"
_CLOUD_SHAPE_PREFIXES = ("aws_", "gcp_", "azure_")
_CLOUD_PAD_X = 28.0
_CLOUD_PAD_Y = 36.0  # room for title band
_CLOUD_PAD_BOTTOM = 24.0


def compile_corridor_ir(ir: CorridorIR, *, page_name: str, animated_connectors: bool = False) -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _CorridorCompiler(ir, page_name, animated_connectors).run_layered()


def _is_cloud_shape(shape_type: str) -> bool:
    st = (shape_type or "").lower()
    return any(st.startswith(p) for p in _CLOUD_SHAPE_PREFIXES)


def _cloud_boundary_label(leaves: list[Leaf]) -> tuple[str, str]:
    """Return (label, platform) for the optional Logical cloud group."""
    counts = {"aws": 0, "gcp": 0, "azure": 0}
    for leaf in leaves:
        st = (leaf.shape_type or "").lower()
        if st.startswith("aws_"):
            counts["aws"] += 1
        elif st.startswith("gcp_"):
            counts["gcp"] += 1
        elif st.startswith("azure_"):
            counts["azure"] += 1
    platform = max(counts, key=counts.get) if any(counts.values()) else "aws"
    labels = {"aws": "AWS Cloud", "gcp": "GCP Cloud", "azure": "Azure Cloud"}
    if sum(1 for v in counts.values() if v) > 1:
        return "Cloud", "multi"
    return labels[platform], platform


def _security_zones_present(ir: CorridorIR) -> str:
    """Deterministic, not LLM-authored — the zone/shelf ids actually present
    in a Security IR are structural fact, not judgment. Reusing whatever
    label the model already gave each corridor avoids asking it to describe,
    in a second free-text field, the same thing it already named."""
    labels = [c.label for c in ir.corridors] + [s.label for s in ir.anchored]
    return ", ".join(labels)


def _overview_notes(ir: CorridorIR) -> list[str]:
    """The bullets ``CellWriter.set_business_notes`` stamps onto the
    compiled page.

    Logical's own ``business_notes`` is model-authored free text, used as
    is. Security/Data-flow instead ask the model for named, independently-
    optional fields (see ``config/xml_gen/ir/security.txt``/``data_flow.txt``)
    — formatted into "Label: text" bullets here, in one fixed order, so the
    panel always presents them the same way regardless of what order the
    model filled them in, and a genuinely-empty field (e.g. no applicable
    threat model) just produces no bullet instead of the model having to
    write "N/A" itself.
    """
    if ir.view == "security":
        fields = [
            ("Purpose", getattr(ir, "purpose", "")),
            ("Scope included", getattr(ir, "scope_included", "")),
            ("Scope excluded", getattr(ir, "scope_excluded", "")),
            ("Security zones", _security_zones_present(ir)),
            ("Threat model", getattr(ir, "threat_model_reference", "")),
            ("Data classification", getattr(ir, "data_classification", "")),
            ("Compliance frameworks", getattr(ir, "compliance_frameworks", "")),
            ("Control types", getattr(ir, "control_types", "")),
            ("Key assumptions", getattr(ir, "key_assumptions", "")),
        ]
        return [f"{label}: {text}" for label, text in fields if text]
    if ir.view == "data_flow":
        fields = [
            ("Data classification", getattr(ir, "data_classification", "")),
            ("Compliance frameworks", getattr(ir, "compliance_frameworks", "")),
            ("Trust zones referenced", getattr(ir, "trust_zones_referenced", "")),
            ("Key assumptions", getattr(ir, "key_assumptions", "")),
        ]
        return [f"{label}: {text}" for label, text in fields if text]
    return list(ir.business_notes)


class _CorridorCompiler:
    def __init__(self, ir: CorridorIR, page_name: str, animated_connectors: bool = False) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)
        self.animated_connectors = animated_connectors

    def run_layered(self) -> str:
        """Geometrically-authoritative path — every coordinate comes from
        ``layout_corridor_ir``, computed once. Marks ``layout="ir"`` so
        ``fit_containers_to_children`` does not reflow compiler geometry;
        ``repair_xml`` still runs structural + edge-port polish only.

        Logical is an exception: ranks stay in JSON, but emission is a compact
        flat storyboard so sibling icon pitch stays clean.
        """
        geos, routes = layout_corridor_ir(self.ir)
        self._actor_geos = geos
        all_corridors = [*self.ir.corridors, *self.ir.anchored]
        flat = getattr(self.ir, "view", None) == "logical"

        if flat:
            # The non-flat path below looks up geometry by corridor id
            # (geos[corridor.id]), so JSON declaration order never mattered
            # for it. _emit_logical_flat has no such indirection — it builds
            # its left-to-right leaf order directly from list order — so an
            # LLM that emits corridors out of canonical rank order (nothing
            # validates or normalizes this; only membership in the fixed id
            # set is checked) would silently invert the journey read
            # direction. Sort by the same rank table the ELK path already
            # uses so declaration order can never affect the visual result.
            all_corridors = sorted(all_corridors, key=lambda c: _row_rank_key("logical", c.id, c.label))
            content_right, content_bottom, row_left, row_top = self._emit_logical_flat(
                all_corridors,
            )
            routes = {}  # ELK corridor routes don't match compact flat positions
        else:
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
            row_ids = {c.id for c in self.ir.corridors}
            row_left = min((g.x for cid, g in geos.items() if cid in row_ids), default=_ROW_ORIGIN_X)
            content_right = max(
                (g.x + g.w for cid, g in geos.items() if cid in row_ids),
                default=_ROW_ORIGIN_X,
            )
            row_top = min((g.y for cid, g in geos.items() if cid in row_ids), default=_ROW_TOP_Y)
            content_bottom = max(
                (g.y + g.h for cid, g in geos.items() if cid in {c.id for c in all_corridors}),
                default=_ROW_TOP_Y,
            )

        # This page's own layout is authoritative — mark it so repair_xml
        # skips re-deriving/overwriting it (see xml_diagnostics.py).
        self.w.mark_layout_authoritative()

        self._emit_actors(
            row_left=row_left,
            row_right=content_right,
            top_y=row_top,
            flat_actors=flat,
        )
        emit_flows(self.w, self.ir.flows, routes=routes, animated_connectors=self.animated_connectors)
        self.w.set_business_notes(_overview_notes(self.ir))

        legend_body = build_legend(self.ir)
        if legend_body is not None:
            # Placeholder geometry only — xml_diagnostics.ensure_legend_position
            # (runs unconditionally in repair_xml) pins this below the overview
            # panel once the panel's real position is known; this call site
            # owns content, not placement.
            self.w.vertex(
                id=LEGEND_ID, label=legend_body, style=LEGEND_STYLE,
                x=content_right + 40, y=_ROW_TOP_Y,
                w=LEGEND_PLACEHOLDER_W, h=LEGEND_PLACEHOLDER_H,
                parent="1",
            )

        page_w = content_right + 400
        page_h = content_bottom + 200
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    def _emit_logical_flat(
        self,
        all_corridors: list,
    ) -> tuple[float, float, float, float]:
        """Compact L→R storyboard: no domain boxes; optional one dashed cloud.

        Leaf order follows corridor/shelf JSON ranks. Cloud-shaped leaves are
        packed into one dashed group (≤4/row); non-cloud leaves stay outside
        that group. Returns (content_right, content_bottom, row_left, row_top).
        """
        ordered: list[Leaf] = []
        for corridor in all_corridors:
            ordered.extend(corridor.leaves)

        before: list[Leaf] = []
        cloud_leaves: list[Leaf] = []
        after: list[Leaf] = []
        seen_cloud = False
        for leaf in ordered:
            if _is_cloud_shape(leaf.shape_type):
                seen_cloud = True
                cloud_leaves.append(leaf)
            elif not seen_cloud:
                before.append(leaf)
            else:
                after.append(leaf)

        pitch = geo.DATA_CANONICAL_PITCH
        leaf_y0 = _ROW_TOP_Y + _CLOUD_PAD_Y
        abs_by_id: dict[str, tuple[float, float, float, float]] = {}

        def _place_row(leaves: list[Leaf], *, x0: float, y0: float) -> float:
            """Place leaves in a ≤4-col grid; return x for the next block."""
            if not leaves:
                return x0
            cols = min(4, len(leaves))
            for i, leaf in enumerate(leaves):
                col, row = i % cols, i // cols
                abs_by_id[leaf.id] = (
                    x0 + col * pitch,
                    y0 + row * geo.ROW_PITCH_Y,
                    geo.LEAF_W,
                    geo.LEAF_H,
                )
            width = (cols - 1) * pitch + geo.LEAF_W
            return x0 + width + (pitch - geo.LEAF_W)

        next_x = _place_row(before, x0=_ROW_ORIGIN_X, y0=leaf_y0)

        cloud_origin = (0.0, 0.0)
        bw = bh = 0.0
        cloud_ids = {leaf.id for leaf in cloud_leaves}
        if cloud_leaves:
            cloud_x0 = next_x if before else _ROW_ORIGIN_X
            _place_row(cloud_leaves, x0=cloud_x0, y0=leaf_y0)
            xs = [abs_by_id[lid][0] for lid in cloud_ids]
            ys = [abs_by_id[lid][1] for lid in cloud_ids]
            rights = [abs_by_id[lid][0] + abs_by_id[lid][2] for lid in cloud_ids]
            bottoms = [abs_by_id[lid][1] + abs_by_id[lid][3] for lid in cloud_ids]
            bx = min(xs) - _CLOUD_PAD_X
            by = min(ys) - _CLOUD_PAD_Y
            bw = max(rights) - bx + _CLOUD_PAD_X
            bh = max(bottoms) - by + _CLOUD_PAD_BOTTOM
            label, platform = _cloud_boundary_label(cloud_leaves)
            self.w.vertex(
                id=CLOUD_BOUNDARY_ID,
                label=label,
                style=theme.cloud_boundary_style(platform),
                x=bx, y=by, w=bw, h=bh,
                parent="1",
            )
            cloud_origin = (bx, by)
            next_x = bx + bw + _CLOUD_PAD_X

        if after:
            _place_row(after, x0=next_x if (before or cloud_leaves) else _ROW_ORIGIN_X, y0=leaf_y0)

        for leaf in ordered:
            ax, ay, aw, ah = abs_by_id[leaf.id]
            if leaf.id in cloud_ids:
                parent = CLOUD_BOUNDARY_ID
                x, y = ax - cloud_origin[0], ay - cloud_origin[1]
            else:
                parent = "1"
                x, y = ax, ay
            self.w.vertex(
                id=leaf.id,
                label=leaf_label(leaf),
                style=theme.leaf_style(leaf.criticality),
                x=x, y=y, w=aw, h=ah,
                parent=parent,
                shape_type=leaf.shape_type,
                criticality=leaf.criticality,
            )

        self._flat_leaf_cy = {
            lid: ay + ah / 2.0 for lid, (_ax, ay, _aw, ah) in abs_by_id.items()
        }

        if abs_by_id:
            row_left = min(ax for ax, _ay, _aw, _ah in abs_by_id.values())
            row_top = min(ay for _ax, ay, _aw, _ah in abs_by_id.values())
            content_right = max(ax + aw for ax, _ay, aw, _ah in abs_by_id.values())
            content_bottom = max(ay + ah for _ax, ay, _aw, ah in abs_by_id.values())
            if cloud_leaves:
                content_right = max(content_right, cloud_origin[0] + bw)
                content_bottom = max(content_bottom, cloud_origin[1] + bh)
                row_left = min(row_left, cloud_origin[0])
                row_top = min(row_top, cloud_origin[1])
        else:
            row_left, row_top = _ROW_ORIGIN_X, _ROW_TOP_Y
            content_right, content_bottom = _ROW_ORIGIN_X, _ROW_TOP_Y
        return content_right, content_bottom, row_left, row_top

    def _emit_actors(
        self,
        *,
        row_left: float,
        row_right: float,
        top_y: float,
        flat_actors: bool = False,
    ) -> None:
        """Left actors: loose on Logical; band container on Security/Data-flow.

        Right actors (saas_egress) always render as loose leaves.
        """
        from app.adapters.outbound.diagram.logical_layout import actor_sort_key_by_endpoint_y

        left = [a for a in self.ir.external_actors if a.kind in ("users", "saas_ingress", "on_prem")]
        right = [a for a in self.ir.external_actors if a.kind == "saas_egress"]

        # Absolute leaf centers for primary-port alignment.
        endpoint_cy: dict[str, float] = {}
        band_hi = top_y + 400.0
        flat_cy = getattr(self, "_flat_leaf_cy", None)
        if flat_cy:
            endpoint_cy.update(flat_cy)
            if endpoint_cy:
                band_hi = max(band_hi, max(endpoint_cy.values()) + geo.LEAF_H)
        else:
            for corridor in self.ir.corridors:
                cg = self._actor_geos.get(corridor.id)
                if cg is None:
                    continue
                for leaf in corridor.leaves:
                    lg = self._actor_geos.get(leaf.id)
                    if lg is not None:
                        endpoint_cy[leaf.id] = cg.y + lg.y + lg.h / 2.0
                band_hi = max(band_hi, cg.y + cg.h)

        if left:
            left = sorted(
                enumerate(left),
                key=lambda ia: actor_sort_key_by_endpoint_y(
                    ia[1].id, self.ir.flows, endpoint_cy,
                    band_lo=top_y, band_hi=band_hi,
                    fallback_index=ia[0],
                ),
            )
            left = [a for _, a in left]

            if flat_actors:
                placed_y = top_y
                actor_x = max(20.0, row_left - geo.LEAF_W - _ACTOR_GAP_X)
                for i, actor in enumerate(left):
                    pref, _, _ = actor_sort_key_by_endpoint_y(
                        actor.id, self.ir.flows, endpoint_cy,
                        band_lo=top_y, band_hi=band_hi, fallback_index=i,
                    )
                    y = max(placed_y, pref - geo.LEAF_H / 2.0) if endpoint_cy else placed_y
                    self.w.vertex(
                        id=actor.id, label=actor.label, style=theme.actor_style(actor.kind),
                        x=actor_x, y=y, w=geo.LEAF_W, h=geo.LEAF_H, parent="1",
                    )
                    placed_y = y + geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y
            else:
                _cols, _rows, content_w, content_h = grid_fill(len(left), cols=1)
                band_w = max(CONTAINER_MIN_W, content_w + CONTAINER_PADDING_X * 2)
                # + geo.LABEL_BAND_H: actors render with verticalLabelPosition=
                # bottom too (theme.actor_style()) — same reasoning as
                # layout_corridor_ir's _place_leaves.
                band_h = max(
                    CONTAINER_MIN_H,
                    content_h + geo.LABEL_BAND_H + CONTAINER_LABEL_HEIGHT + CONTAINER_PADDING_Y,
                )
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

        if right:
            right = sorted(
                enumerate(right),
                key=lambda ia: actor_sort_key_by_endpoint_y(
                    ia[1].id, self.ir.flows, endpoint_cy,
                    band_lo=top_y, band_hi=band_hi,
                    fallback_index=ia[0],
                ),
            )
            right = [a for _, a in right]
        placed_y = top_y
        for i, actor in enumerate(right):
            pref, _, _ = actor_sort_key_by_endpoint_y(
                actor.id, self.ir.flows, endpoint_cy,
                band_lo=top_y, band_hi=band_hi, fallback_index=i,
            )
            y = max(placed_y, pref - geo.LEAF_H / 2.0) if endpoint_cy else placed_y
            self.w.vertex(
                id=actor.id, label=actor.label, style=theme.actor_style(actor.kind),
                x=row_right + _ACTOR_GAP_X, y=y, w=geo.LEAF_W, h=geo.LEAF_H, parent="1",
            )
            placed_y = y + geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y
