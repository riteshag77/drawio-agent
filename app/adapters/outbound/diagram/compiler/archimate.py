"""Compile an ArchimateIR into draw.io XML.

One entry point, ``compile_archimate_ir``, dispatching internally on
``ir.viewpoint`` — the same shape ``compiler/corridor.py`` already uses to
branch for Logical's flat-storyboard special case, rather than three
near-identical public compilers.

* **layered** — three stacked bands (business over application over
  technology), each holding either a flat grid of elements or, once a band is
  crowded, a row of domain-grouping boxes. This is the viewpoint that needs
  real containment, which is why ``ArchimateIR`` subclasses ``DiagramIR`` and
  reuses Network's hierarchical-ELK approach.
* **application_cooperation** — one band, no layer stacking: the question it
  answers is "how do these components interact", and a band per layer would
  add a vertical axis that carries no information.
* **technology** — the same flat treatment, scoped to the technology layer.

Everything visual is decided here: geometry from ``geometry_spec``'s ArchiMate
constants via ``elk_layout_archimate``, colour from ``palette``, notation from
``theme``. The IR carries none of it.

The overview panel is assembled here too (``_overview_notes``) and this is
where the LLM-authored / compiler-derived split is actually enforced: the model's
prose fields are used as written, and every list or count is recomputed from the
IR. See that function for the field-by-field breakdown.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.elk_layout_archimate import (
    ArchimateLayoutPlan,
    PlannedBand,
    PlannedGroup,
    grid_size,
    layout_archimate_plan,
)
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.adapters.outbound.diagram.logical_layout import Geo, order_leaves_by_flow
from app.domain.ir.archimate import ArchimateElement, ArchimateIR
from app.domain.ir.archimate_types import (
    LAYER_ORDER,
    element_spec,
    relationship_label,
)

__all__ = ["ARCHIMATE_BAND_ID_PREFIX", "compile_archimate_ir"]

#: Band containers are compiler-invented (the IR never names them), so their
#: ids need a namespace that cannot collide with a model-chosen element or
#: grouping id. ``Identified``'s id rule forbids nothing here, so the prefix is
#: checked against the IR's own ids at build time rather than assumed unique.
ARCHIMATE_BAND_ID_PREFIX = "archimate_band_"

_LAYER_LABELS: dict[str, str] = {
    "business": "Business Layer",
    "application": "Application Layer",
    "technology": "Technology Layer",
}

_VIEWPOINT_LABELS: dict[str, str] = {
    "layered": "Layered",
    "application_cooperation": "Application cooperation",
    "technology": "Technology",
}


def compile_archimate_ir(
    ir: ArchimateIR, *, page_name: str = "ArchiMate", animated_connectors: bool = False,
) -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid — same contract as
    every other view's compiler.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _ArchimateCompiler(ir, page_name, animated_connectors).run()


class _ArchimateCompiler:
    def __init__(self, ir: ArchimateIR, page_name: str, animated_connectors: bool) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)
        # Accepted for call-site symmetry with the other compilers and
        # deliberately unused: ArchiMate relationships are a static structural
        # statement, not a request path, so animating them would assert
        # movement the notation does not claim.
        del animated_connectors
        self._band_ids: dict[str, str] = {}

    # ── planning: what boxes exist, before any geometry ─────────────────

    def _band_id(self, layer: str) -> str:
        """A band id guaranteed not to collide with a model-chosen id."""
        taken = self.ir.leaf_ids() | self.ir.structural_ids()
        base = f"{ARCHIMATE_BAND_ID_PREFIX}{layer}"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _ordered(self, elements: list[ArchimateElement]) -> list[ArchimateElement]:
        """Relationship-aware order, so related boxes end up near each other.

        ``order_leaves_by_flow`` only reads ``.id`` off each item and
        ``.source``/``.target`` off each edge, so ArchiMate relationships drop
        straight into the ordering the cloud views already use — one
        implementation of "put connected things next to each other" rather than
        a second one for this view.
        """
        return order_leaves_by_flow(list(elements), self.ir.relationships)

    def _plan(self) -> ArchimateLayoutPlan:
        if self.ir.viewpoint == "layered":
            layers = [layer for layer in LAYER_ORDER if self.ir.elements_in_layer(layer)]
            bands = [self._plan_band(layer, self.ir.elements_in_layer(layer)) for layer in layers]
            return ArchimateLayoutPlan(bands)

        # Flat viewpoints: one band holding everything, labelled for what it
        # actually shows rather than for a layer it may span.
        elements = list(self.ir.elements)
        label = _VIEWPOINT_LABELS.get(self.ir.viewpoint, "ArchiMate")
        layer = elements[0].layer if elements else "application"
        band = self._plan_band(layer, elements, label=f"{label} view")
        return ArchimateLayoutPlan([band])

    def _plan_band(
        self, layer: str, elements: list[ArchimateElement], *, label: str | None = None,
    ) -> PlannedBand:
        """Decide whether this band draws grouping boxes or a flat grid.

        Grouping boxes cost a nesting level and a lot of whitespace, and they
        only pay for themselves once a band has enough elements that a reader
        cannot take it in at a glance. Below ``ARCHIMATE_GROUPING_THRESHOLD``
        the groupings still reach the reader — via the overview panel's "Domain
        groupings" section, which lists every one of them with its rationale
        regardless of whether it was drawn.
        """
        band_id = self._band_id(layer)
        self._band_ids[layer] = band_id
        band_label = label or _LAYER_LABELS.get(layer, layer.title())
        ordered = self._ordered(elements)

        grouped_ids = {e.group for e in ordered if e.group}
        use_groups = (
            len(ordered) > geo.ARCHIMATE_GROUPING_THRESHOLD
            and len(grouped_ids) >= 2
            and all(e.group for e in ordered)
        )
        if not use_groups:
            return PlannedBand(band_id, band_label, layer, element_ids=[e.id for e in ordered])

        # Grouping order follows the IR's own `groupings` order (the model's
        # narrative order), not the element order — the walkthrough in the
        # overview panel reads in that same order.
        by_group: dict[str, list[str]] = {}
        for element in ordered:
            by_group.setdefault(element.group or "", []).append(element.id)
        groups = [
            PlannedGroup(g.id, g.label, by_group[g.id])
            for g in self.ir.groupings
            if g.id in by_group
        ]
        # A grouping referenced by an element but absent from `groupings` is
        # already a semantic error, so this can only be the empty-tail case.
        return PlannedBand(band_id, band_label, layer, groups=groups)

    # ── emission ────────────────────────────────────────────────────────

    def run(self) -> str:
        plan = self._plan()
        geos = layout_archimate_plan(plan)
        _align_bands(plan, geos)
        by_id = {e.id: e for e in self.ir.elements}

        content_right = content_bottom = 0.0
        for band in plan.bands:
            band_geo = geos[band.id]
            if band.groups:
                for group in band.groups:
                    group_geo = geos[group.id]
                    self._emit_elements(
                        [by_id[eid] for eid in group.element_ids], parent=group.id,
                    )
                    self.w.vertex(
                        id=group.id, label=group.label, style=theme.archimate_grouping_style(),
                        x=group_geo.x, y=group_geo.y, w=group_geo.w, h=group_geo.h,
                        parent=band.id,
                    )
            else:
                self._emit_elements(
                    [by_id[eid] for eid in band.element_ids], parent=band.id,
                )
            self.w.vertex(
                id=band.id, label=band.label, style=theme.archimate_band_style(band.layer),
                x=band_geo.x, y=band_geo.y, w=band_geo.w, h=band_geo.h, parent="1",
            )
            content_right = max(content_right, band_geo.x + band_geo.w)
            content_bottom = max(content_bottom, band_geo.y + band_geo.h)

        self._emit_relationships()
        self.w.set_business_notes(_overview_notes(self.ir))
        # Compiler-final geometry, same as Network/Corridor: repair_xml keeps
        # its structural hygiene and edge ports but must not reflow this.
        self.w.mark_layout_authoritative()
        return self.w.to_xml(page_width=content_right + 400, page_height=content_bottom + 200)

    def _emit_elements(self, elements: list[ArchimateElement], *, parent: str) -> None:
        """Grid ``elements`` into ``parent``.

        Positions depend only on the grid, never on the parent's final size —
        same property ``shared.emit_leaf_grid`` relies on — so this can run
        before the container itself is emitted.
        """
        _content_w, _content_h, cols, _rows = grid_size(len(elements))
        for index, element in enumerate(elements):
            col, row = index % cols, index // cols
            spec = element_spec(element.archimate_type)
            # element_spec cannot be None here: archimate_type is a Literal the
            # IR already validated against the same table.
            assert spec is not None
            self.w.vertex(
                id=element.id,
                label=element.name,
                style=theme.archimate_element_style(spec.glyph, spec.shape, element.layer),
                x=geo.PAD + col * geo.ARCHIMATE_ELEMENT_PITCH_X,
                y=geo.TITLE_PAD + row * geo.ARCHIMATE_ELEMENT_PITCH_Y,
                w=geo.ARCHIMATE_ELEMENT_W,
                h=geo.ARCHIMATE_ELEMENT_H,
                parent=parent,
                shape_type=f"archimate_{element.archimate_type.replace('-', '_')}",
            )

    def _emit_relationships(self) -> None:
        """Every relationship as a root-layer edge, labelled with its type.

        The line notation (arrowheads, diamonds, dash patterns) is the
        authoritative statement of the relationship type for a reader fluent in
        ArchiMate; the label is there for everyone else, and matches this app's
        existing "define the term inline the first time" convention. See
        ``archimate_types.relationship_label``.
        """
        for relationship in self.ir.relationships:
            self.w.edge(
                id=relationship.id,
                source=relationship.source,
                target=relationship.target,
                label=relationship_label(relationship.type),
                style=theme.archimate_relation_style(relationship.type),
            )


def _align_bands(plan: ArchimateLayoutPlan, geos: dict[str, "Geo"]) -> None:
    """Give every band the same left edge and width.

    ELK centres siblings, which leaves three bands of different widths
    staggered horizontally — and a staggered stack does not read as *layers*,
    which is the one thing this viewpoint exists to show. Widening each band to
    the widest and sharing a left edge costs nothing (contents keep their
    parent-relative positions, so nothing inside moves) and is what turns the
    stack into the familiar layered picture.

    Vertical positions are untouched: those carry the band *order*, which is
    real information ELK computed correctly.
    """
    band_ids = [band.id for band in plan.bands if band.id in geos]
    if len(band_ids) < 2:
        return
    left = min(geos[bid].x for bid in band_ids)
    width = max(geos[bid].x + geos[bid].w for bid in band_ids) - left
    for bid in band_ids:
        # Geo is frozen (it is shared, cached layout output elsewhere), so
        # replace the entry rather than mutating it in place.
        geos[bid] = Geo(x=left, y=geos[bid].y, w=width, h=geos[bid].h)


# ── overview panel ─────────────────────────────────────────────────────


def _overview_notes(ir: ArchimateIR) -> list[str]:
    """The eleven overview-panel bullets, in one fixed order.

    Which half of each field comes from where is the whole point of this
    function, so it is spelled out:

    * **LLM-authored, used verbatim** — panel summary, viewpoint rationale,
      layers-excluded reason, walkthrough steps, compliance notes, coaching
      note, and each grouping's rationale. All short and qualitative: exactly
      the things a model is good at and a compiler cannot produce.
    * **Compiler-derived, never trusted from the model** — the layers-included
      list, the inferred/assumed list, the auto-corrections list, the
      open-questions list, and the confidence tally. All countable or
      enumerable, and therefore all things a model can get subtly wrong in a
      way no reader would catch. Security's "Security zones" bullet is derived
      the same way and for the same reason (see
      ``compiler/corridor.py::_security_zones_present``).

    A genuinely empty field produces no bullet rather than the string "N/A" —
    same convention the Security/Data-flow panels already follow.
    """
    notes: list[str] = []

    if ir.panel_summary:
        notes.append(f"What this shows: {ir.panel_summary}")

    viewpoint = _VIEWPOINT_LABELS.get(ir.viewpoint, ir.viewpoint)
    if ir.viewpoint_rationale:
        notes.append(f"Viewpoint: {viewpoint} — {ir.viewpoint_rationale}")
    else:
        notes.append(f"Viewpoint: {viewpoint}")

    # Derived: which layers actually have elements on the canvas.
    present = ir.layers_present()
    layers = [_LAYER_LABELS.get(layer, layer).replace(" Layer", "") for layer in present]
    included = f"Layers included: {', '.join(layers)}" if layers else ""
    # Only append the exclusion reason when a layer is genuinely missing — the
    # prompt tells the model this field is for that case alone ("ONLY when a
    # layer genuinely has no elements"); appending it unconditionally would
    # let a model note ride along on a diagram that excludes nothing.
    missing = [layer for layer in LAYER_ORDER if layer not in present]
    if included and missing and ir.layers_excluded_reason:
        included += f" (others excluded: {ir.layers_excluded_reason})"
    if included:
        notes.append(included)

    if ir.walkthrough:
        steps = " ".join(f"{i}. {step}" for i, step in enumerate(ir.walkthrough, start=1))
        notes.append(f"Walkthrough: {steps}")

    # Derived: every declared grouping with its rationale, whether or not the
    # band was crowded enough for the box to be drawn.
    groupings = [
        f"{g.label} — {g.rationale}" if g.rationale else g.label for g in ir.groupings
    ]
    if groupings:
        notes.append(f"Domain groupings: {'; '.join(groupings)}")

    # Derived: everything not explicitly stated in the brief.
    inferred = [
        f"{e.name} ({e.source_confidence}{': ' + e.notes if e.notes else ''})"
        for e in ir.elements
        if e.source_confidence != "explicit"
    ]
    if inferred:
        notes.append(f"What we inferred: {'; '.join(inferred)}")

    # Derived: relationships validation rewrote, with the reason it gave.
    corrections = [
        r.correction_note or f"{r.source} -> {r.target} corrected to {r.type}"
        for r in ir.relationships
        if r.auto_corrected
    ]
    if corrections:
        notes.append(f"What we auto-corrected: {'; '.join(corrections)}")

    # Derived: including the questions raised by dropped relationships.
    if ir.open_questions:
        notes.append(
            "Open questions for you: "
            + "; ".join(q.question for q in ir.open_questions)
        )

    if ir.compliance_notes:
        notes.append(f"Compliance notes: {ir.compliance_notes}")

    if ir.coaching_note:
        notes.append(f"How to use this: {ir.coaching_note}")

    # Derived: the tally. Last on purpose — it is a footnote to everything
    # above, and putting it there means a reader has already seen the specifics
    # it summarizes.
    counts = ir.confidence_counts()
    total = sum(counts.values())
    notes.append(
        f"Confidence summary: {total} element(s) — {counts['explicit']} stated in the brief, "
        f"{counts['inferred']} inferred, {counts['assumed']} assumed"
    )
    return notes


