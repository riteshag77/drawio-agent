"""Shared IR for the three "corridor" views: Logical, Security, Data-flow.

These three are structurally identical, unlike Network's strict container
nesting: a row of same-rank corridors (domains / trust zones / data phases)
placed left-to-right by journey order, each holding leaves, with an optional
single "shelf" corridor anchored *below* one of the row corridors (a SaaS
shelf under Data domain / Restricted zone / Store phase — the exact same
pattern in all three prompt packs, just different names). This module is the
one place that shape gets defined.

Crucially: **geometry has never been this app's job for these three views.**
`adapters/outbound/diagram/logical_layout.py`'s rank-and-order layered layout
already recomputes every corridor's and leaf's x/y/width/height downstream
(`placement_fixers.enforce_view_journey_columns`, called unconditionally by
`xml_diagnostics.repair_xml` for these three page names) — confirmed by
reading that call chain, not assumed. So unlike Network, this IR doesn't need
to out-compete a "the model does the geometry" failure mode; it needs to
out-compete a *grouping* failure mode (a leaf in the wrong domain/zone/phase,
a business partner folded into the SaaS shelf, an edge into a zone it must
never reach). That is what the validators here check.

What is genuinely a per-view judgment call (which corridor kinds exist, which
companion services are required when a trigger condition is met, spine/shelf
placement rules) stays in the ``config/xml_gen/ir/{view}.txt`` packs and each
view's Pydantic subclass — this module only owns what all three have in
common.
"""

from __future__ import annotations

from collections import Counter
from typing import ClassVar

from pydantic import Field, model_validator

from app.domain.ir.base import DiagramIR, Identified, Leaf


class Corridor(Identified):
    """One rank in the left-to-right journey: a domain, a trust zone, a data phase.

    Never empty (``leaves`` requires >=1) — network.txt's "omit the empty
    shell rather than emit one" rule, made structural instead of a reminder.
    """

    label: str
    leaves: list[Leaf] = Field(min_length=1)


class AnchoredCorridor(Corridor):
    """A corridor deliberately excluded from the main row and anchored below another.

    All three views have at least one of these (Logical's SaaS shelf under
    Data, Security's under Restricted) and Data-flow has three at once
    (Consume *and* SaaS both stacked under Store, plus Control below the
    entire row) — so this is a list, not a single optional field. Two
    anchored corridors sharing the same ``anchor_id`` stack in list order;
    ``anchor_id=None`` means "below the whole row" rather than under one
    named corridor (Data-flow's Control shelf).
    """

    anchor_id: str | None = Field(
        default=None,
        description="Id of the row corridor this is anchored below, or None for 'below the whole row'.",
    )


class CorridorIR(DiagramIR):
    """Common shape shared by LogicalIR / SecurityIR / DataFlowIR.

    ``corridors`` are the same-rank row (domains / zones / phases), always
    ``parent="1"`` in the compiled output, ordered left-to-right by journey
    role. ``anchored`` holds every corridor deliberately excluded from that
    row and anchored below another instead — the recurring "shelf" pattern
    each view's prompt pack describes with different names for the same
    mechanism.
    """

    #: Id prefix every corridor (row and anchored) must use — set by each
    #: subclass so "the diagram is laid out by a deterministic engine that
    #: finds corridors by this exact id" is enforced once, not copy-pasted
    #: three times.
    id_prefix: ClassVar[str] = ""

    corridors: list[Corridor] = Field(min_length=1)
    anchored: list[AnchoredCorridor] = Field(
        default_factory=list,
        description="Shelves excluded from the main row (e.g. external-SaaS), each anchored below a row corridor or the whole row.",
    )

    # ── id collection ──────────────────────────────────────────────────

    def all_corridors(self) -> list[Corridor]:
        return [*self.corridors, *self.anchored]

    def leaf_ids(self) -> set[str]:
        return {leaf.id for corridor in self.all_corridors() for leaf in corridor.leaves}

    def structural_ids(self) -> set[str]:
        return {c.id for c in self.all_corridors()}

    # ── validation ─────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _check_prefixes_and_uniqueness(self) -> "CorridorIR":
        prefix = self.id_prefix
        if prefix:
            for corridor in self.all_corridors():
                if not corridor.id.startswith(prefix):
                    raise ValueError(
                        f"corridor id {corridor.id!r} must start with {prefix!r} — the "
                        "layout engine finds corridors by this exact prefix, not by label"
                    )

        counts: Counter[str] = Counter()
        for corridor in self.all_corridors():
            counts[corridor.id] += 1
            for leaf in corridor.leaves:
                counts[leaf.id] += 1
        for actor in self.external_actors:
            counts[actor.id] += 1
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate ids: {', '.join(dupes)} — every cell id must be unique")

        row_ids = {c.id for c in self.corridors}
        for shelf in self.anchored:
            if shelf.anchor_id is not None and shelf.anchor_id not in row_ids:
                raise ValueError(
                    f"anchored corridor {shelf.id!r} has anchor_id {shelf.anchor_id!r}, which is "
                    "not one of the main-row corridors — anchoring is one level deep only"
                )
        return self

    def semantic_errors(self) -> list[str]:
        """Cross-field rules common to every corridor view.

        Just ``DiagramIR._base_flow_errors()`` — dangling refs and self-loops
        are the only cross-field rule the three corridor views share (they
        have no Network-style no-edge zones). Subclasses that need more
        (none currently do) would extend this list rather than override it
        outright, so the shared checks can never silently disappear.
        """
        return self._base_flow_errors()
