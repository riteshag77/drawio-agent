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

from app.domain.ir.base import DiagramIR, ExternalActor, Flow, Identified, Leaf


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
    # Declared after the structural fields on purpose: models emit JSON fields
    # in schema order, so corridors/leaves (the nodes) must exist before the
    # edges that reference them. See DiagramIR's docstring.
    external_actors: list[ExternalActor] = Field(
        default_factory=list,
        description="Everyone outside the diagram's own boundary (personas, IdPs, SaaS endpoints).",
    )
    flows: list[Flow] = Field(
        default_factory=list,
        description=(
            "REQUIRED whenever 2+ leaves exist — never empty. The directed edges that tell "
            "this view's story, referencing only leaf/actor ids already declared above."
        ),
    )

    # ── id collection ──────────────────────────────────────────────────

    def all_corridors(self) -> list[Corridor]:
        return [*self.corridors, *self.anchored]

    def leaf_ids(self) -> set[str]:
        return {leaf.id for corridor in self.all_corridors() for leaf in corridor.leaves}

    def structural_ids(self) -> set[str]:
        return {c.id for c in self.all_corridors()}

    # ── validation ─────────────────────────────────────────────────────

    @classmethod
    def _relocate_vocabulary(
        cls,
        data: object,
        *,
        row_ids: frozenset[str],
        shelf_anchors: dict[str, str | None],
    ) -> object:
        """Deterministically fix row-vs-shelf mis-slotting in raw LLM output.

        Models reliably decide *what* exists but sometimes put a fixed-vocabulary
        corridor in the wrong list — a live capture showed ``zone_detect``
        (a main-row zone) emitted as an anchored shelf, which hard-failed the
        whole Security view over to direct XML. Row/shelf membership and each
        shelf's anchor are fixed vocabulary ("this is fixed, not a judgment
        call" per the prompt packs), so relocation is unambiguous and belongs
        in code, not in a corrective LLM round-trip.

        ``shelf_anchors`` maps each legal shelf id to its required ``anchor_id``
        (``None`` = below the whole row). The anchor is only forced when its
        target corridor is present in the row — otherwise the value is left
        for the normal validators to report.
        """
        if not isinstance(data, dict):
            return data
        corridors = data.get("corridors")
        anchored = data.get("anchored")
        if not isinstance(corridors, list) and not isinstance(anchored, list):
            return data
        corridors = list(corridors) if isinstance(corridors, list) else []
        anchored = list(anchored) if isinstance(anchored, list) else []

        # Only raw-dict entries (LLM JSON) are relocated; model instances built
        # in code pass through untouched.
        new_row: list[object] = [
            c for c in corridors if not (isinstance(c, dict) and c.get("id") in shelf_anchors)
        ]
        new_shelves: list[object] = []
        for shelf in anchored:
            if isinstance(shelf, dict) and shelf.get("id") in row_ids:
                # Row-vocabulary corridor emitted as a shelf — back to the row.
                new_row.append({k: v for k, v in shelf.items() if k != "anchor_id"})
            else:
                new_shelves.append(dict(shelf) if isinstance(shelf, dict) else shelf)
        # Shelf-vocabulary corridors emitted in the row become shelves.
        new_shelves.extend(dict(c) for c in corridors if isinstance(c, dict) and c.get("id") in shelf_anchors)

        present_row_ids = {
            c.get("id") if isinstance(c, dict) else getattr(c, "id", None) for c in new_row
        }
        for shelf in new_shelves:
            if not isinstance(shelf, dict) or shelf.get("id") not in shelf_anchors:
                continue
            required = shelf_anchors[shelf["id"]]
            if required is None:
                shelf["anchor_id"] = None
            elif required in present_row_ids:
                shelf["anchor_id"] = required

        data["corridors"] = new_row
        data["anchored"] = new_shelves
        return data

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

    # ── shared helpers for subclasses' semantic_warnings() ──────────────

    def corridor_by_id(self, corridor_id: str) -> Corridor | None:
        """The row or anchored corridor with this id, or ``None`` — a
        judgment-call check reads this to ask "does X even exist" before it
        asks "does X follow the required-companion rule"."""
        return next((c for c in self.all_corridors() if c.id == corridor_id), None)

    @staticmethod
    def leaves_mention(leaves: list[Leaf], *needles: str) -> bool:
        """Whether any leaf's label contains one of ``needles`` (case-
        insensitive) — the label-substring heuristic every "required
        companion present?" warning is built from, since leaves have no
        dedicated "kind" field to check structurally instead."""
        return any(any(n in leaf.label.lower() for n in needles) for leaf in leaves)

    def semantic_errors(self) -> list[str]:
        """Cross-field rules common to every corridor view.

        Starts from ``DiagramIR._base_flow_errors()`` (dangling refs and
        self-loops) and adds one rule specific to these three views: unlike
        Network, which conveys relationships through container nesting,
        Logical/Security/Data-flow have *no other way* to express a
        relationship between components — ``flows`` is the whole point of
        the view. ``flows`` defaults to an empty list, so Pydantic never
        flags a diagram that omits every edge; a real capture showed the
        model returning a fully-formed, schema-valid IR with no ``flows`` key
        at all, which is exactly the failure mode this method exists to
        catch. Subclasses that need more would extend this list rather than
        override it outright, so the shared checks can never silently
        disappear.
        """
        errors = self._base_flow_errors()
        if not self.flows and len(self.leaf_ids()) >= 2:
            errors.append(
                "flows is empty but there are 2+ leaves — add the request/data-flow edges "
                "between them; a diagram with no relationships is not useful"
            )
        return errors
