"""ArchiMate view IR — the fifth view, and the first non-cloud one.

A ``DiagramIR`` subclass rather than a ``CorridorIR`` one, on purpose: the
layered viewpoint needs *real hierarchical containment* (layer band -> domain
grouping -> element), which is the shape ``NetworkIR`` already has and
``compiler/elk_layout_network.py`` already solves with hierarchical ELK.
Corridor's flat "row of same-rank boxes plus an anchored shelf" cannot express
a band that contains groups that contain elements without pretending one of
those levels doesn't exist.

**Three validation behaviours, each deliberately different** (the spec is
explicit that these must not be uniform):

1. An unknown ``archimate_type`` is a **hard parse error**. That is free —
   ``ArchimateElementType`` is a Pydantic ``Literal`` — and it routes straight
   into the existing corrective-retry loop in
   ``core/services/agent._generate_ir_with_corrections``. No coercion, no
   nearest-match: a hallucinated element type is exactly the thing the closed
   vocabulary exists to catch.
2. A ``layer`` inconsistent with the type's table-defined layer is **silently
   overwritten** from ``ARCHIMATE_ELEMENT_TABLE`` (a ``mode="before"``
   validator). The layer of an element type is notation, not judgment — the
   model does not get to override the table, and being told about it would
   waste a correction round-trip on something with exactly one right answer.
3. An **illegal relationship** is auto-corrected to the nearest legal
   substitute when one exists (recording ``auto_corrected`` +
   ``correction_note`` so the overview panel can disclose it), and otherwise
   **dropped entirely** with an ``open_questions`` entry naming both endpoints.
   Same mutate-and-log shape as ``CorridorIR._relocate_vocabulary`` /
   ``_bridge_removed_leaves``: deterministic fixes belong in code, and what
   code cannot honestly fix becomes a question for the human.

**What the LLM authors vs. what the compiler derives.** The overview panel has
eleven fields; only the short, qualitative ones are IR fields here
(``panel_summary``, ``viewpoint_rationale``, ``layers_excluded_reason``,
``walkthrough``, ``compliance_notes``, ``coaching_note``, and each grouping's
``rationale``). Everything countable or enumerable — which layers are present,
what was inferred, what was auto-corrected, what is still open, the confidence
tally — is computed from this structure by ``compiler/archimate.py``. That is
the same reason Security's "Security zones" bullet is compiler-derived
(``compiler/corridor.py::_security_zones_present``): a number the model writes
by hand can drift from the data it is supposed to describe, and a panel that
miscounts its own diagram is worse than one that says less.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from app.domain.ir.archimate_types import (
    LAYER_ORDER,
    ArchimateElementType,
    ArchimateLayer,
    ArchimateRelationshipType,
    element_spec,
    is_legal_relationship,
    nearest_legal_substitute,
)
from app.domain.ir.base import DiagramIR, ExternalActor, Flow, Identified, IRModel

__all__ = [
    "ArchimateElement",
    "ArchimateGrouping",
    "ArchimateIR",
    "ArchimateRelationship",
    "ArchimateViewpoint",
    "OpenQuestion",
    "SourceConfidence",
]

ArchimateViewpoint = Literal["layered", "application_cooperation", "technology"]

#: Where a fact in this diagram came from. The whole anti-hallucination story
#: rests on this being answered per element rather than as one blanket
#: disclaimer at the bottom of the panel.
SourceConfidence = Literal["explicit", "inferred", "assumed"]


class ArchimateElement(Identified):
    """One ArchiMate element. No coordinates, no colours, no stencil names.

    ``layer`` is present in the schema (it makes the model state its intent,
    and a mismatch is a useful signal in captures) but is **not authoritative**
    — see ``ArchimateIR._align_layers_to_table``.
    """

    archimate_type: ArchimateElementType = Field(
        description=(
            "One of the fixed ArchiMate element types. This list is closed: a value "
            "outside it is rejected outright, never mapped to something similar. If "
            "nothing fits, pick the closest type that IS listed and explain the choice "
            "in `notes`."
        )
    )
    layer: ArchimateLayer = Field(
        description=(
            "business | application | technology. Must match the layer the chosen "
            "archimate_type belongs to — it is corrected from the notation table if not, "
            "so never use it to try to place an element in a different layer."
        )
    )
    name: str = Field(description="Short business-readable element name, e.g. 'Quote & Bind'.")
    description: str = Field(
        default="",
        description="One sentence, only when the name alone is genuinely ambiguous. Usually empty.",
    )
    group: str | None = Field(
        default=None,
        description=(
            "Optional id of an ArchimateGrouping this element belongs to (a domain, a "
            "capability area, a deployment target). Leave null when the element does not "
            "belong to a named grouping — do not invent one per element."
        ),
    )
    source_confidence: SourceConfidence = Field(
        description=(
            "'explicit' when the brief actually says this exists; 'inferred' when it "
            "follows necessarily from something the brief says; 'assumed' when you added "
            "it because the picture would be incomplete without it. Be honest — the "
            "overview panel discloses every non-explicit element to the reader."
        )
    )
    notes: str = Field(
        default="",
        description=(
            "REQUIRED whenever source_confidence is not 'explicit': one short sentence "
            "saying what you inferred or assumed and why. Also used to record a "
            "cross-layer tie-breaker decision."
        ),
    )


class ArchimateRelationship(IRModel):
    """A typed ArchiMate relationship between two element ids.

    ``auto_corrected``/``correction_note`` are **outputs**, not inputs — they
    are written by ``ArchimateIR._enforce_relationship_legality`` when it
    rewrites an illegal type, and read back by the compiler for the overview
    panel's "What we auto-corrected" section. A model that fills them in is
    harmless; a model that doesn't (the normal case) loses nothing.
    """

    id: str = Field(description="Unique id for this relationship.")
    type: ArchimateRelationshipType = Field(
        description=(
            "The ArchiMate relationship type. Illegal combinations are corrected to the "
            "nearest legal one, or dropped and raised as an open question — so choose the "
            "one you actually mean rather than defaulting to 'association'."
        )
    )
    source: str = Field(description="id of the source element")
    target: str = Field(description="id of the target element")
    auto_corrected: bool = Field(
        default=False,
        description="Internal — set by validation, never by you.",
    )
    correction_note: str | None = Field(
        default=None,
        description="Internal — set by validation, never by you.",
    )


class ArchimateGrouping(Identified):
    """A domain / capability area / deployment target that elements cluster into.

    Rendered as a real nested container once a band is crowded enough to need
    the structure (see ``compiler/archimate.py``); below that threshold the
    grouping still appears in the overview panel's "Domain groupings" section,
    because the *reasoning* is useful even when the box would be visual noise.
    """

    label: str = Field(description="Short grouping name, e.g. 'Underwriting'.")
    layer_hint: ArchimateLayer | None = Field(
        default=None,
        description=(
            "Optional: the layer this grouping lives in. Every member must be in that one "
            "layer — a grouping is drawn inside a single layer band, so one spanning two "
            "layers is rejected. Split it into one grouping per layer instead."
        ),
    )
    rationale: str = Field(
        default="",
        description="One short sentence on why these elements belong together.",
    )


class OpenQuestion(IRModel):
    """Something the brief genuinely does not answer, surfaced to the reader.

    Also the destination for relationships that validation could not honestly
    repair — a dropped edge becomes a question rather than disappearing
    silently.
    """

    id: str = Field(description="Unique id for this question.")
    question: str = Field(
        description="A direct question to the reader, e.g. 'Which system owns the customer record?'"
    )
    blocks: list[str] = Field(
        default_factory=list,
        description="Ids of elements/relationships this question leaves unresolved.",
    )


class ArchimateIR(DiagramIR):
    """The ArchiMate view's IR.

    ``external_actors`` and ``flows`` are declared for ``DiagramIR`` surface
    compatibility (``all_ids``/``_base_flow_errors`` read them) and stay empty:
    ArchiMate has its own richer, typed ``relationships``, and an external actor
    is just a ``business-actor`` element — modelling it twice would let the two
    disagree.
    """

    view: Literal["archimate"] = Field(
        default="archimate",
        description="Must be exactly the lowercase string 'archimate'.",
    )
    viewpoint: ArchimateViewpoint = Field(
        description=(
            "'layered' for the full business-over-application-over-technology story; "
            "'application_cooperation' for how application components interact; "
            "'technology' for the technology layer alone. Pick the one that answers the "
            "question the brief actually asks."
        )
    )
    elements: list[ArchimateElement] = Field(
        min_length=1,
        description="Every element in this view. Never empty.",
    )
    groupings: list[ArchimateGrouping] = Field(
        default_factory=list,
        description="Domain/capability/deployment groupings elements can belong to.",
    )
    # Declared after the structural fields on purpose: models emit JSON fields
    # in schema order, so the elements must exist before the relationships
    # that reference them. See DiagramIR's docstring for the captures behind
    # this ordering rule.
    relationships: list[ArchimateRelationship] = Field(
        default_factory=list,
        description=(
            "REQUIRED whenever 2+ elements exist — never empty. Typed ArchiMate "
            "relationships between element ids already declared above."
        ),
    )
    open_questions: list[OpenQuestion] = Field(
        default_factory=list,
        description=(
            "What the brief does not answer. Raise a question rather than inventing "
            "a fact — an assumed element plus an honest question beats a confident guess."
        ),
    )
    # ── overview panel: LLM-authored fields only ───────────────────────
    # Everything countable (layers present, inferred/assumed list, corrections
    # list, open-question list, confidence tally) is derived by the compiler
    # from the structure above — see the module docstring.
    panel_summary: str = Field(
        default="",
        description="1-2 sentences: what this diagram shows and who it is for.",
    )
    viewpoint_rationale: str = Field(
        default="",
        description="One sentence: why this viewpoint answers the question that was asked.",
    )
    layers_excluded_reason: str = Field(
        default="",
        description=(
            "One sentence, ONLY when a layer is absent from the diagram: why it is out of "
            "scope. Leave empty when every layer is present — never write 'N/A'."
        ),
    )
    walkthrough: list[str] = Field(
        default_factory=list,
        description=(
            "3-6 short numbered steps guiding a reader through the diagram in order. "
            "Write them as prose steps; do not number them yourself."
        ),
    )
    compliance_notes: str = Field(
        default="",
        description=(
            "One sentence on regulatory/compliance constraints this design must satisfy, "
            "only when the brief raises any. Leave empty otherwise."
        ),
    )
    coaching_note: str = Field(
        default="",
        description=(
            "REQUIRED whenever any element is inferred/assumed or any open question "
            "exists: one short sentence telling the reader what to check first and what "
            "would make the next version sharper."
        ),
    )
    # Declared last, same reasoning as relationships: only worth writing once
    # everything else is decided.
    #
    # This is NOT panel content — it does not appear in `_overview_notes`'s
    # eleven bullets. It flows through `agent.generate_xml_diagram_from_ir` to
    # the API response and renders as the frontend's "Why this design" card
    # (static/js/app.js::appendDesignRationaleCard). An earlier comment here
    # claimed it was "kept empty for ArchiMate" while this description asked
    # the model to fill it — so whether the card appeared at all was luck.
    # It is genuinely optional; the prompt documents when it earns its place.
    design_tradeoffs: list[str] = Field(
        default_factory=list,
        description=(
            "Optional: 0-3 short bullets, each naming one genuine *modelling* choice and the "
            "alternative it beat — e.g. modelling a managed database as a technology-service "
            "rather than an application-component, or collapsing five microservices into one "
            "application-component. Not a summary of the architecture, and not a restatement "
            "of an assumption (that is what `notes` and the panel's inferred list are for). "
            "Leave empty rather than invent one — most diagrams have no genuine fork."
        ),
    )
    external_actors: list[ExternalActor] = Field(
        default_factory=list,
        description="Unused on this view — model people and organizations as business-actor elements instead. Leave empty.",
    )
    flows: list[Flow] = Field(
        default_factory=list,
        description="Unused on this view — use the typed `relationships` list instead. Leave empty.",
    )

    # ── id collection ──────────────────────────────────────────────────

    def leaf_ids(self) -> set[str]:
        return {e.id for e in self.elements}

    def structural_ids(self) -> set[str]:
        return {g.id for g in self.groupings}

    def elements_in_layer(self, layer: str) -> list[ArchimateElement]:
        return [e for e in self.elements if e.layer == layer]

    def layers_present(self) -> list[str]:
        """Layers with at least one element, in canonical top-to-bottom order.

        Compiler-derived overview content starts here rather than from an
        LLM-authored list, so "Layers included" can never disagree with what is
        actually on the canvas.
        """
        present = {e.layer for e in self.elements}
        return [layer for layer in LAYER_ORDER if layer in present]

    def confidence_counts(self) -> dict[str, int]:
        """``{confidence: count}`` over every element — never LLM-authored."""
        counts = Counter(e.source_confidence for e in self.elements)
        return {key: counts.get(key, 0) for key in ("explicit", "inferred", "assumed")}

    # ── validation ─────────────────────────────────────────────────────

    @model_validator(mode="before")
    @classmethod
    def _align_layers_to_table(cls, data: object) -> object:
        """Overwrite each element's ``layer`` from the notation table.

        Runs pre-parse, so it also fills in a ``layer`` the model omitted
        entirely (a required field it would otherwise have failed on for no
        good reason — the type already determines the answer). Unknown
        ``archimate_type`` values are left untouched for the ``Literal`` to
        reject a moment later; "correcting" the layer of a type that does not
        exist would only obscure that error.
        """
        if not isinstance(data, dict):
            return data
        elements = data.get("elements")
        if not isinstance(elements, list):
            return data
        aligned: list[object] = []
        for element in elements:
            if not isinstance(element, dict):
                aligned.append(element)
                continue
            spec = element_spec(str(element.get("archimate_type") or ""))
            if spec is None:
                aligned.append(element)
                continue
            aligned.append({**element, "layer": spec.layer})
        data["elements"] = aligned
        return data

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "ArchimateIR":
        seen: Counter[str] = Counter()
        for element in self.elements:
            seen[element.id] += 1
        for grouping in self.groupings:
            seen[grouping.id] += 1
        for actor in self.external_actors:
            seen[actor.id] += 1
        dupes = sorted(i for i, n in seen.items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate ids: {', '.join(dupes)} — every cell id must be unique")
        return self

    @model_validator(mode="after")
    def _enforce_relationship_legality(self) -> "ArchimateIR":
        """Auto-correct illegal relationships, or drop them and ask instead.

        Deterministic and silent-by-design in the "there is one right answer"
        case, loud in the "there isn't" case — the same split
        ``CorridorIR._relocate_vocabulary`` (silent relocation of fixed
        vocabulary) and ``_bridge_removed_leaves`` (repair, then let the
        residual surface) already draw.

        Relationships with an unknown endpoint are left alone: they are already
        going to be reported by ``semantic_errors()``, and guessing at legality
        with one endpoint missing would produce a misleading correction note.
        """
        by_id = {e.id: e for e in self.elements}
        kept: list[ArchimateRelationship] = []
        dropped: list[tuple[ArchimateRelationship, str, str]] = []

        for rel in self.relationships:
            source = by_id.get(rel.source)
            target = by_id.get(rel.target)
            if source is None or target is None:
                kept.append(rel)
                continue
            if is_legal_relationship(source.archimate_type, rel.type, target.archimate_type):
                kept.append(rel)
                continue
            substitute = nearest_legal_substitute(
                source.archimate_type, rel.type, target.archimate_type
            )
            if substitute is not None:
                rel.correction_note = (
                    f"{rel.type} is not legal from {source.archimate_type} to "
                    f"{target.archimate_type} — corrected to {substitute}"
                )
                rel.type = substitute  # type: ignore[assignment]
                rel.auto_corrected = True
                kept.append(rel)
                continue
            dropped.append((rel, source.name or source.id, target.name or target.id))

        if dropped:
            self.relationships = kept
            existing = {q.id for q in self.open_questions}
            for index, (rel, source_name, target_name) in enumerate(dropped):
                question_id = _unique_question_id(f"q_rel_{rel.id}", existing, index)
                existing.add(question_id)
                self.open_questions.append(
                    OpenQuestion(
                        id=question_id,
                        question=(
                            f"How are '{source_name}' and '{target_name}' actually related? "
                            f"The stated '{rel.type}' relationship is not valid between these "
                            "element types and has no meaningful equivalent, so it was left off "
                            "the diagram."
                        ),
                        blocks=[rel.source, rel.target],
                    )
                )
        else:
            self.relationships = kept
        return self

    def semantic_errors(self) -> list[str]:
        """Cross-field rules Pydantic cannot express, returned rather than raised
        so the correction loop can hand the model an actionable message.

        Note what is *not* here: illegal relationships and wrong layers. Those
        are fixed deterministically above — spending a correction round-trip on
        something with exactly one right answer is waste, and a live-capture
        lesson this codebase already learned on corridor vocabulary.
        """
        errors: list[str] = []
        # Deliberately elements only, NOT `structural_ids()`. A grouping is a
        # visual container, not a model element, so it cannot be a relationship
        # endpoint. Accepting grouping ids here was a silent edge-eater:
        # `_enforce_relationship_legality` resolves endpoints against
        # `elements` alone, so a grouping endpoint skipped legality entirely,
        # got an edge emitted, and was then deleted downstream by
        # `remove_dangling_edges` whenever the grouping box wasn't drawn —
        # no correction note, no open question, the relationship just vanished.
        element_ids = self.leaf_ids()
        grouping_ids = {g.id for g in self.groupings}

        for rel in self.relationships:
            for end, ref in (("source", rel.source), ("target", rel.target)):
                if ref in element_ids:
                    continue
                if ref in grouping_ids:
                    errors.append(
                        f"relationship {rel.id!r} {end} {ref!r} is a grouping, not an element — "
                        "relationships connect elements to each other; point this at the specific "
                        "element inside that grouping"
                    )
                else:
                    errors.append(
                        f"relationship {rel.id!r} {end} {ref!r} is not a known element id"
                    )
            if rel.source == rel.target:
                errors.append(f"relationship {rel.id!r} points {rel.source!r} at itself, which is not meaningful")

        rel_ids = Counter(r.id for r in self.relationships)
        dupes = sorted(i for i, n in rel_ids.items() if n > 1)
        if dupes:
            errors.append(f"duplicate relationship ids: {', '.join(dupes)}")

        for element in self.elements:
            if element.group and element.group not in grouping_ids:
                errors.append(
                    f"element {element.id!r} references grouping {element.group!r}, which is not "
                    "declared in `groupings`"
                )

        # A grouping whose members sit in different layer bands cannot be
        # drawn: the compiler plans one box per band, so a cross-band grouping
        # produced two cells sharing one id, and `resolve_duplicate_ids` (which
        # by contract never rewrites `parent=` refs) then re-parented the first
        # band's elements into the second band's box. Reject it here — the
        # notation has no cross-layer grouping either, so this is a real
        # modelling error, not a rendering limitation.
        layers_by_group: dict[str, set[str]] = {}
        for element in self.elements:
            if element.group:
                layers_by_group.setdefault(element.group, set()).add(element.layer)
        for group_id, layers in sorted(layers_by_group.items()):
            if len(layers) > 1:
                errors.append(
                    f"grouping {group_id!r} has members in more than one layer "
                    f"({', '.join(sorted(layers))}) — a grouping sits inside a single layer band, "
                    "so split it into one grouping per layer"
                )

        if not self.relationships and len(self.elements) >= 2:
            errors.append(
                "relationships is empty but there are 2+ elements — an ArchiMate view exists to "
                "show how elements relate; add the typed relationships between them"
            )

        if self.viewpoint == "technology":
            off_layer = sorted(e.id for e in self.elements if e.layer != "technology")
            if off_layer:
                errors.append(
                    "the technology viewpoint renders only technology-layer elements, but these "
                    f"are not: {', '.join(off_layer[:6])} — remove them or switch viewpoint to 'layered'"
                )
        if self.viewpoint == "application_cooperation":
            business_only = sorted(e.id for e in self.elements if e.layer == "business")
            if business_only and len(business_only) == len(self.elements):
                errors.append(
                    "the application-cooperation viewpoint needs application-layer elements, but "
                    "every element is business-layer — switch viewpoint to 'layered'"
                )
        return errors

    def semantic_warnings(self) -> list[str]:
        """Judgment calls fed to one non-blocking corrective pass, never a gate.

        Same soft-nudge shape Security/Data-flow use for their own overview
        fields: a thin panel is a quality problem, not a structural one, and it
        must never be the reason a diagram fails to render.
        """
        warnings: list[str] = []
        n = len(self.elements)

        if n >= 4 and not self.panel_summary:
            warnings.append(
                "panel_summary is empty — write 1-2 sentences saying what this diagram shows "
                "and who it is for"
            )
        if n >= 4 and not self.walkthrough:
            warnings.append(
                "walkthrough is empty — add 3-6 short steps guiding a reader through the "
                "diagram in order"
            )
        if n >= 4 and not self.viewpoint_rationale:
            warnings.append(
                f"viewpoint_rationale is empty — say in one sentence why the {self.viewpoint!r} "
                "viewpoint answers the question that was asked"
            )

        missing_layers = [layer for layer in LAYER_ORDER if layer not in self.layers_present()]
        if self.viewpoint == "layered" and missing_layers and not self.layers_excluded_reason:
            warnings.append(
                f"the {', '.join(missing_layers)} layer(s) are absent from a layered view but "
                "layers_excluded_reason is empty — say in one sentence why they are out of scope"
            )

        unexplained = [
            e.id for e in self.elements if e.source_confidence != "explicit" and not e.notes
        ]
        if unexplained:
            warnings.append(
                "these elements are inferred/assumed but have no `notes` explaining why: "
                f"{', '.join(sorted(unexplained)[:6])} — the overview panel discloses every one "
                "of them to the reader, so each needs a one-sentence reason"
            )

        needs_coaching = bool(unexplained) or bool(self.open_questions) or any(
            e.source_confidence != "explicit" for e in self.elements
        )
        if needs_coaching and not self.coaching_note:
            warnings.append(
                "this diagram contains assumptions or open questions but coaching_note is empty — "
                "tell the reader in one sentence what to check first"
            )
        return warnings


def _unique_question_id(preferred: str, taken: set[str], salt: int) -> str:
    """An ``open_questions`` id that cannot collide with a model-chosen one.

    ``Identified``'s id rules do not apply to ``OpenQuestion`` (it is never a
    draw.io cell), but keeping ids collision-free still matters: the panel
    lists questions by id when it has to reference one.
    """
    candidate = preferred if preferred not in taken else f"{preferred}_{salt}"
    suffix = salt
    while candidate in taken:
        suffix += 1
        candidate = f"{preferred}_{suffix}"
    return candidate
