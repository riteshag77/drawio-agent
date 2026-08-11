"""Shared IR for the three "corridor" views: Logical, Security, Data-flow.

These three are structurally identical, unlike Network's strict container
nesting: a row of same-rank corridors (domains / trust zones / data phases)
placed left-to-right by journey order, each holding leaves, with an optional
single "shelf" corridor anchored *below* one of the row corridors (a SaaS
shelf under Data domain / Restricted zone / Store phase — the exact same
pattern in all three prompt packs, just different names). This module is the
one place that shape gets defined.

Crucially: **geometry is computed once at compile time for these three views.**
`adapters/outbound/diagram/logical_layout.layout_corridor_ir` (invoked by
`compiler/corridor.compile_corridor_ir`) writes every corridor's and leaf's
x/y/width/height; `repair_xml` does not re-flow the journey row. So unlike
Network, this IR doesn't need to out-compete a "the model does the geometry"
failure mode; it needs to out-compete a *grouping* failure mode (a leaf in
the wrong domain/zone/phase, a business partner folded into the SaaS shelf,
an edge into a zone it must never reach). That is what the validators here
check.

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

    #: Shape-type substrings that are unambiguous cross-cutting infrastructure
    #: (observability/secrets/IAM/edge-security) for this view — empty by
    #: default (Security legitimately hosts these). Set by Logical/Data-flow;
    #: see ``_strip_cross_cutting_infra``.
    _infra_noise_denylist: ClassVar[frozenset[str]] = frozenset()

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
    # Declared last, same reasoning as flows above: only worth summarizing
    # once everything else is decided. Populated on the Logical view (the
    # business journey a request takes); left empty elsewhere — Security/
    # Data-flow's own prompts don't ask for it.
    business_notes: list[str] = Field(
        default_factory=list,
        description=(
            "1-3 short bullets (1-2 sentences each) describing the business journey "
            "in plain language — who does what and why it matters, not just which "
            "services are involved. Define any technical term inline the first time "
            "it's used. Leave empty if this view's own instructions don't ask for it."
        ),
    )
    # Declared last, after business_notes: a distinct narrative ask (why a
    # choice was made, not what it does). Most relevant on Security/Data-flow
    # (control and handling choices genuinely have alternatives); Logical's
    # sparse storyboard rarely does — leave empty there rather than pad.
    design_tradeoffs: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 short bullets (1 sentence each), each naming ONE genuine design "
            "choice in this view and the specific alternative it beat — e.g. "
            "'mTLS over one-way TLS: partner service must also authenticate.' "
            "Only include choices with a real, nameable alternative a competent "
            "architect would have actually considered. Leave empty rather than "
            "invent a tradeoff that wasn't a genuine judgment call, or if this "
            "view's own instructions don't ask for it."
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

        # A corridor id that is neither a row id nor a shelf id is not a
        # mis-slotting (that's the swap above) — it's an invented id outside
        # the fixed vocabulary entirely (a live capture showed a weaker model
        # emitting id="cloud" as a generic wrapper in Logical/Data-flow/
        # Security alike). Its leaves are usually fine; only the grouping id
        # is wrong. Fold them into the last recognized row corridor rather
        # than hard-failing the whole view over one bad id — but only when
        # there is a valid row corridor to fold into; otherwise leave the
        # data alone and let the real structural error surface.
        known = row_ids | shelf_anchors.keys()
        valid_row = [c for c in new_row if isinstance(c, dict) and c.get("id") in row_ids]
        stray = [c for c in (*new_row, *new_shelves) if isinstance(c, dict) and c.get("id") not in known]
        if valid_row and stray:
            target = valid_row[-1]
            merged = list(target["leaves"]) if isinstance(target.get("leaves"), list) else []
            for corridor in stray:
                if isinstance(corridor.get("leaves"), list):
                    merged.extend(corridor["leaves"])
            target["leaves"] = merged
            new_row = [c for c in new_row if not (isinstance(c, dict) and c.get("id") not in known)]
            new_shelves = [c for c in new_shelves if not (isinstance(c, dict) and c.get("id") not in known)]

        data["corridors"] = new_row
        data["anchored"] = new_shelves
        return data

    @classmethod
    def _strip_cross_cutting_infra(cls, data: object) -> object:
        """Deterministically drop leaves that are unambiguous cross-cutting
        infrastructure (CloudWatch, Secrets Manager, KMS, GuardDuty, WAF, IAM,
        ...) for views whose prompt pack excludes them (Logical, Data-flow —
        see each pack's "what belongs vs. noise" section).

        Prompt instructions alone do not reliably keep the model from adding
        these: live captures showed the Logical LLM call putting CloudWatch +
        Secrets Manager under ``domain_platform`` and WAF/CloudFront under
        ``domain_experience`` even with an explicit forbidden-list and
        exclusion section in the prompt. This is the deterministic backstop —
        a no-op (empty denylist) for views that legitimately host these
        (Security). Runs pre-parse like ``_relocate_vocabulary``, so a leaf
        stripped here also has its dangling flows removed in the same pass
        rather than surfacing as a semantic-error correction round-trip.
        """
        denylist = cls._infra_noise_denylist
        if not denylist or not isinstance(data, dict):
            return data

        removed_ids: set[str] = set()

        def _is_denied(leaf: object) -> bool:
            if not isinstance(leaf, dict):
                return False
            shape = str(leaf.get("shape_type") or "").lower()
            return any(term in shape for term in denylist)

        def _filter(corridor_list: object) -> list[object]:
            kept: list[object] = []
            for corridor in corridor_list or []:
                if not isinstance(corridor, dict) or not isinstance(corridor.get("leaves"), list):
                    kept.append(corridor)
                    continue
                keep_leaves = []
                for leaf in corridor["leaves"]:
                    if _is_denied(leaf):
                        if isinstance(leaf, dict) and leaf.get("id"):
                            removed_ids.add(leaf["id"])
                        continue
                    keep_leaves.append(leaf)
                if keep_leaves:
                    kept.append({**corridor, "leaves": keep_leaves})
                # else: corridor emptied entirely by the strip — omit it,
                # same as the "never emit an empty corridor" prompt rule.
            return kept

        data["corridors"] = _filter(data.get("corridors"))
        data["anchored"] = _filter(data.get("anchored"))

        if removed_ids and isinstance(data.get("flows"), list):
            data["flows"] = cls._bridge_removed_leaves(data["flows"], removed_ids)
        return data

    @staticmethod
    def _bridge_removed_leaves(flows: list[object], removed_ids: set[str]) -> list[object]:
        """Reconnect around a stripped leaf instead of just deleting its edges.

        A leaf denylisted by ``_strip_cross_cutting_infra`` is often a
        mid-chain hop (``CDN -> WAF -> API Gateway``) — naively dropping every
        flow that touches it severs the chain and leaves the downstream leaf
        (API Gateway) with no incoming edge at all, which reads as "the line
        to X is missing" even though X itself is fine. For each removed leaf,
        every predecessor is rewired directly to every successor (skipping
        self-loops and pairs already connected) before the leaf's own edges
        are finally dropped. One bridging pass — a chain of two *consecutive*
        removed leaves is a rare residual case the per-view orphan check
        (``LogicalIR.semantic_errors``) catches as a backstop.
        """
        incoming: dict[str, list[dict]] = {}
        outgoing: dict[str, list[dict]] = {}
        kept: list[object] = []
        for f in flows:
            if not isinstance(f, dict):
                kept.append(f)
                continue
            src, tgt = f.get("source"), f.get("target")
            if tgt in removed_ids:
                incoming.setdefault(tgt, []).append(f)
            elif src in removed_ids:
                outgoing.setdefault(src, []).append(f)
            else:
                kept.append(f)

        seen_pairs = {(f.get("source"), f.get("target")) for f in kept if isinstance(f, dict)}
        bridged: list[object] = []
        for removed_id, in_flows in incoming.items():
            for inf in in_flows:
                for outf in outgoing.get(removed_id, []):
                    src, tgt = inf.get("source"), outf.get("target")
                    if not src or not tgt or src == tgt or (src, tgt) in seen_pairs:
                        continue
                    seen_pairs.add((src, tgt))
                    bridge = dict(inf)
                    bridge["source"], bridge["target"] = src, tgt
                    bridge["label"] = outf.get("label") or inf.get("label")
                    bridged.append(bridge)

        return kept + bridged

    @model_validator(mode="after")
    def _check_uniqueness(self) -> "CorridorIR":
        """Cross-field checks with a single owner: exact corridor-id
        vocabulary is each subclass's own job (``_check_domain_vocabulary`` /
        ``_check_zone_vocabulary`` / ``_check_phase_vocabulary``) — this used
        to *also* check ``id_prefix`` here, which only ever duplicated a
        weaker version of the same subclass check (every vocabulary id
        already has the right prefix by construction), so it's gone."""
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

        Checks ``external_actors`` alongside ``leaf_ids()`` — previously
        only leaves counted, so an IR with 1 leaf + 2 external actors + zero
        flows passed cleanly even though ``corridor_core.txt`` already
        documents "flows is REQUIRED whenever 2+ leaves or any external
        actor exist." Strictly additive: widens what counts as a violation,
        never narrows it, so it cannot regress any diagram that was already
        compliant with the shared prompt's own stated contract.
        """
        errors = self._base_flow_errors()
        if not self.flows and (len(self.leaf_ids()) >= 2 or self.external_actors):
            errors.append(
                "flows is empty but there are 2+ leaves or an external actor — add the "
                "request/data-flow edges between them; a diagram with no relationships is not useful"
            )
        return errors

    def _orphan_errors(self) -> list[str]:
        """Every leaf/actor must touch at least one flow — opt-in, not part of
        the shared ``semantic_errors()`` above.

        Logical is flat with no grouping box, so an unconnected leaf/actor is
        genuinely invisible-as-connected there (see ``LogicalIR.semantic_errors``,
        which calls this). Security/Data-flow are deliberately NOT wired to
        this: their zone/phase boxes are real containers, and a passive leaf
        like CloudWatch sitting in the Detect zone with no drawn edge is a
        normal, valid diagram there — trying to require flow-touching for every
        leaf on those two views rejected legitimate fixtures when tried.
        """
        all_leaf_and_actor_ids = self.leaf_ids() | {a.id for a in self.external_actors}
        if len(all_leaf_and_actor_ids) < 2:
            return []
        touched: set[str] = set()
        for flow in self.flows:
            touched.add(flow.source)
            touched.add(flow.target)
        orphans = sorted(all_leaf_and_actor_ids - touched)
        if not orphans:
            return []
        return [
            f"these leaves/actors have no flow connecting them to anything: "
            f"{', '.join(orphans)} — an unconnected node reads as broken, not just "
            "unlabeled; connect each one to the story or remove it"
        ]
