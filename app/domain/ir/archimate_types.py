"""The closed ArchiMate vocabulary — element types, their fixed properties, and
which relationships are legal between them.

This module is the enforcement point the ArchiMate spec calls "the MCP server's
lookup tables". In this codebase MCP is an external *icon-search client*, not a
component we own (see ``adapters/outbound/diagram/drawio_mcp_client.py``), so
the closed vocabulary lives here instead — the same layer that already enforces
Logical/Security/Data-flow's fixed corridor-id vocabulary
(``domain/ir/{logical,security}.py``).

Three properties make this table the single source of truth:

* ``ArchimateElementType`` is a Pydantic ``Literal``, so an element type outside
  the vocabulary is a **hard parse error** — no coercion, no "closest match".
  That is deliberate per the spec: a hallucinated element type must fail loudly
  into the existing corrective-retry path, not silently become something else.
* Every type's ``layer``/``aspect``/``shape`` come from ``ARCHIMATE_ELEMENT_TABLE``
  and are never taken from the model (see ``ArchimateIR._align_layers_to_table``).
* Relationship legality is a compact **rule function** over
  ``(aspect, aspect, relationship)`` rather than a 7-layer x 4-aspect
  cross-product matrix — cheaper to maintain and consistent with this
  codebase's existing preference for rule functions over giant static tables.

v1 scope is Business / Application / Technology only. Motivation, Strategy,
Physical and Implementation are genuinely part of ArchiMate 3.x but no v1
viewpoint renders them, so no type from those layers is in the vocabulary yet.
Adding them later is additive (new rows here, new ``layer`` values) and does not
break any diagram authored against this version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

__all__ = [
    "ARCHIMATE_ELEMENT_TABLE",
    "ARCHIMATE_ELEMENT_TYPES",
    "ARCHIMATE_RELATIONSHIP_TYPES",
    "ArchimateAspect",
    "ArchimateElementType",
    "ArchimateLayer",
    "ArchimateRelationshipType",
    "ArchimateShape",
    "ArchimateTypeSpec",
    "element_spec",
    "is_legal_relationship",
    "nearest_legal_substitute",
    "relationship_label",
]

#: The full ArchiMate 3.x layer/aspect set this app models. ``motivation`` and
#: ``implementation`` are strictly *aspects* rather than layers in the
#: standard's own framework, but they are rendered as bands here for the same
#: reason the rest are: a reader follows one top-to-bottom story, and "why →
#: what → who → app → tech → how we get there" is that story.
ArchimateLayer = Literal[
    "motivation", "strategy", "business", "application", "technology", "implementation",
]

#: ArchiMate's aspects — the column axis of the standard's own framework grid.
#: Almost every relationship rule below is expressible purely in terms of
#: these, which is why they, not the element types, are what the legality
#: function reasons over. ``motivation`` is its own aspect: motivation
#: elements are neither structure nor behaviour, and the standard gives them
#: their own relationship rules (see ``influence``).
ArchimateAspect = Literal["active-structure", "behavior", "passive-structure", "motivation"]

#: Corner treatment. ArchiMate draws behaviour with rounded corners, structure
#: with square ones, and motivation elements with cut (diagonal) corners.
ArchimateShape = Literal["square", "rounded", "diagonal"]

ArchimateElementType = Literal[
    # ── business layer ────────────────────────────────────────────────
    "business-actor",
    "business-role",
    "business-collaboration",
    "business-interface",
    "business-process",
    "business-function",
    "business-event",
    "business-service",
    "business-object",
    # ── application layer ─────────────────────────────────────────────
    "application-component",
    "application-collaboration",
    "application-interface",
    "application-function",
    "application-process",
    "application-service",
    "data-object",
    # ── technology layer ──────────────────────────────────────────────
    "technology-node",
    "technology-device",
    "technology-system-software",
    "technology-collaboration",
    "technology-interface",
    "technology-service",
    "artifact",
    "communication-network",
    "path",
    # ── strategy layer ────────────────────────────────────────────────
    "resource",
    "capability",
    "course-of-action",
    "value-stream",
    # ── motivation aspect ─────────────────────────────────────────────
    "stakeholder",
    "driver",
    "assessment",
    "goal",
    "outcome",
    "principle",
    "requirement",
    "constraint",
    "meaning",
    "value",
    # ── implementation & migration ────────────────────────────────────
    "work-package",
    "deliverable",
    "implementation-event",
    "plateau",
    "gap",
]

#: ``influence`` is now present: it is only meaningful against motivation
#: elements, and the motivation aspect has arrived, so it finally has legal
#: endpoints. The note below records why it was held back until now.
#:
#: (Historical) ``influence`` was deliberately NOT here. It is a real ArchiMate 3.x
#: relationship, but it is only defined against motivation elements, and v1's
#: vocabulary has no motivation layer — so it could never be legal between any
#: two types in ``ARCHIMATE_ELEMENT_TABLE``. Leaving it in the Literal put it
#: in the JSON schema handed to the model as a selectable value that the
#: prompt never described and that was silently rewritten or dropped every
#: single time it was chosen. It returns with the motivation layer.
ArchimateRelationshipType = Literal[
    "composition",
    "aggregation",
    "assignment",
    "realization",
    "serving",
    "access",
    "triggering",
    "flow",
    "specialization",
    "association",
    "influence",
]


@dataclass(frozen=True)
class ArchimateTypeSpec:
    """Everything about an element type that is *fixed by the notation*.

    None of these is a judgment call, which is exactly why none of them is a
    field the LLM gets to fill in: ``layer`` is overwritten from this table if
    the model disagrees, and ``aspect``/``shape``/``glyph`` are never in the IR
    schema at all — the compiler reads them from here.

    ``glyph`` is draw.io's ``appType`` value for the ``mxgraph.archimate3.application``
    stencil (verified against draw.io's own ``Sidebar-ArchiMate3.js`` and
    ``shapes/mxArchiMate3.js``) — the small notation icon in an element box's
    top-right corner. It is a *rendering* detail, so it stays out of the IR and
    is resolved by ``compiler/theme.py`` / ``drawio_mcp_client.py``.
    """

    layer: ArchimateLayer
    aspect: ArchimateAspect
    shape: ArchimateShape
    glyph: str
    #: Human-readable notation name, used in the overview panel and in
    #: correction messages ("business-actor" reads badly in prose).
    display: str


def _spec(layer: str, aspect: str, shape: str, glyph: str, display: str) -> ArchimateTypeSpec:
    return ArchimateTypeSpec(
        layer=layer,  # type: ignore[arg-type]
        aspect=aspect,  # type: ignore[arg-type]
        shape=shape,  # type: ignore[arg-type]
        glyph=glyph,
        display=display,
    )


#: One row per element type. Order matters only for readability; lookups are
#: by key. Adding a type means adding a row here *and* a member to
#: ``ArchimateElementType`` — the consistency check at the bottom of this
#: module fails the import if the two ever drift apart.
ARCHIMATE_ELEMENT_TABLE: dict[str, ArchimateTypeSpec] = {
    # ── business ──────────────────────────────────────────────────────
    "business-actor": _spec("business", "active-structure", "square", "actor", "Business Actor"),
    "business-role": _spec("business", "active-structure", "square", "role", "Business Role"),
    "business-collaboration": _spec(
        "business", "active-structure", "square", "collab", "Business Collaboration"
    ),
    "business-interface": _spec(
        "business", "active-structure", "square", "interface", "Business Interface"
    ),
    "business-process": _spec("business", "behavior", "rounded", "proc", "Business Process"),
    "business-function": _spec("business", "behavior", "rounded", "func", "Business Function"),
    "business-event": _spec("business", "behavior", "rounded", "event", "Business Event"),
    "business-service": _spec("business", "behavior", "rounded", "serv", "Business Service"),
    "business-object": _spec(
        "business", "passive-structure", "square", "passive", "Business Object"
    ),
    # ── application ───────────────────────────────────────────────────
    "application-component": _spec(
        "application", "active-structure", "square", "comp", "Application Component"
    ),
    "application-collaboration": _spec(
        "application", "active-structure", "square", "collab", "Application Collaboration"
    ),
    "application-interface": _spec(
        "application", "active-structure", "square", "interface", "Application Interface"
    ),
    "application-function": _spec(
        "application", "behavior", "rounded", "func", "Application Function"
    ),
    "application-process": _spec(
        "application", "behavior", "rounded", "proc", "Application Process"
    ),
    "application-service": _spec(
        "application", "behavior", "rounded", "serv", "Application Service"
    ),
    "data-object": _spec("application", "passive-structure", "square", "passive", "Data Object"),
    # ── technology ────────────────────────────────────────────────────
    "technology-node": _spec("technology", "active-structure", "square", "node", "Node"),
    "technology-device": _spec("technology", "active-structure", "square", "device", "Device"),
    "technology-system-software": _spec(
        "technology", "active-structure", "square", "sysSw", "System Software"
    ),
    "technology-collaboration": _spec(
        "technology", "active-structure", "square", "collab", "Technology Collaboration"
    ),
    "technology-interface": _spec(
        "technology", "active-structure", "square", "interface", "Technology Interface"
    ),
    "technology-service": _spec(
        "technology", "behavior", "rounded", "serv", "Technology Service"
    ),
    "artifact": _spec("technology", "passive-structure", "square", "artifact", "Artifact"),
    "communication-network": _spec(
        "technology", "active-structure", "square", "netw", "Communication Network"
    ),
    "path": _spec("technology", "active-structure", "square", "path", "Path"),
    # ── strategy ──────────────────────────────────────────────────────
    # A resource is what the organisation *has*; capability/course-of-action/
    # value-stream are what it *does* with it — so the same
    # structure/behaviour split as every other layer.
    "resource": _spec("strategy", "active-structure", "square", "resource", "Resource"),
    "capability": _spec("strategy", "behavior", "rounded", "capability", "Capability"),
    "course-of-action": _spec(
        "strategy", "behavior", "rounded", "courseOfAction", "Course of Action"
    ),
    "value-stream": _spec("strategy", "behavior", "rounded", "valueStream", "Value Stream"),
    # ── motivation ────────────────────────────────────────────────────
    # All one aspect and all diagonal-cornered: motivation elements are
    # neither structure nor behaviour, they are the reasons behind both.
    "stakeholder": _spec("motivation", "motivation", "diagonal", "stakeholder", "Stakeholder"),
    "driver": _spec("motivation", "motivation", "diagonal", "driver", "Driver"),
    "assessment": _spec("motivation", "motivation", "diagonal", "assessment", "Assessment"),
    "goal": _spec("motivation", "motivation", "diagonal", "goal", "Goal"),
    "outcome": _spec("motivation", "motivation", "diagonal", "outcome", "Outcome"),
    "principle": _spec("motivation", "motivation", "diagonal", "principle", "Principle"),
    "requirement": _spec("motivation", "motivation", "diagonal", "requirement", "Requirement"),
    "constraint": _spec("motivation", "motivation", "diagonal", "constraint", "Constraint"),
    "meaning": _spec("motivation", "motivation", "diagonal", "meaning", "Meaning"),
    "value": _spec("motivation", "motivation", "diagonal", "value", "Value"),
    # ── implementation & migration ────────────────────────────────────
    # Work packages and events are things that happen (behaviour); a
    # deliverable is the passive thing they produce. A plateau is a *state* of
    # the architecture and a gap is the difference between two of them — both
    # sit closest to passive structure, which is what keeps "work package
    # realizes deliverable/plateau" legal under the realization rule below.
    "work-package": _spec("implementation", "behavior", "rounded", "workpackage", "Work Package"),
    "implementation-event": _spec("implementation", "behavior", "rounded", "event", "Implementation Event"),
    "deliverable": _spec(
        "implementation", "passive-structure", "square", "deliverable", "Deliverable"
    ),
    "plateau": _spec("implementation", "passive-structure", "square", "plateau", "Plateau"),
    "gap": _spec("implementation", "passive-structure", "square", "gap", "Gap"),
}

ARCHIMATE_ELEMENT_TYPES: frozenset[str] = frozenset(get_args(ArchimateElementType))
ARCHIMATE_RELATIONSHIP_TYPES: tuple[str, ...] = get_args(ArchimateRelationshipType)

#: Band render order, top to bottom. Extends the standard ArchiMate reading
#: order (business drives application, application runs on technology) at both
#: ends, so one page answers the whole question: *why* (motivation), *what we
#: must be able to do* (strategy), *who does it* (business), *what supports
#: them* (application), *what it runs on* (technology), and *how we get there*
#: (implementation & migration).
LAYER_ORDER: tuple[str, ...] = (
    "motivation", "strategy", "business", "application", "technology", "implementation",
)

#: The three layers a `layered` viewpoint must actually populate. Motivation,
#: strategy and implementation are genuinely optional — plenty of good layered
#: diagrams have no roadmap on them — so the balance rule in ``ArchimateIR``
#: only ever holds the core stack to account.
CORE_LAYER_ORDER: tuple[str, ...] = ("business", "application", "technology")

_RELATIONSHIP_DISPLAY: dict[str, str] = {
    "composition": "composed of",
    "aggregation": "aggregates",
    "assignment": "assigned to",
    "realization": "realizes",
    "serving": "serves",
    "access": "accesses",
    "influence": "influences",
    "triggering": "triggers",
    "flow": "flow",
    "specialization": "specializes",
    "association": "associated with",
}


def element_spec(archimate_type: str) -> ArchimateTypeSpec | None:
    """The fixed notation properties of ``archimate_type``, or ``None``.

    Returns ``None`` rather than raising so pre-parse validators (which run
    before the ``Literal`` has rejected an unknown type) can ask about a value
    they have not yet vetted.
    """
    return ARCHIMATE_ELEMENT_TABLE.get(archimate_type)


def relationship_label(rel_type: str) -> str:
    """Short verb phrase for a relationship, used as its on-canvas edge label.

    Strict ArchiMate leaves relationships unlabeled and relies on line
    notation alone. This app renders for mixed audiences (see the "define any
    technical term inline" rule its other views already follow), so the type is
    also spelled out on the line — the notation still carries it, the label
    just means a reader who does not know ArchiMate by heart can still follow
    the diagram.
    """
    return _RELATIONSHIP_DISPLAY.get(rel_type, rel_type)


# ── relationship legality ──────────────────────────────────────────────
#
# Expressed as one predicate per relationship type over the two endpoints'
# *aspects* (plus layer where the standard genuinely cares). This is a
# deliberately conservative reading of ArchiMate 3.x's derivation rules: it
# accepts the relationships an architect actually draws and rejects the
# combinations that are meaningless, without attempting to encode the full
# derived-relationship algebra — which would be a large amount of machinery to
# police output that a human still reviews.

_ACTIVE = "active-structure"
_BEHAVIOR = "behavior"
_PASSIVE = "passive-structure"
_MOTIVATION = "motivation"


def is_legal_relationship(source_type: str, rel_type: str, target_type: str) -> bool:
    """Whether ``source -[rel]-> target`` is a legal ArchiMate relationship.

    Unknown element types return ``True``: an unknown type is already a hard
    Pydantic failure elsewhere, and reporting it a second time as a bogus
    "illegal relationship" would bury the real error.
    """
    src = element_spec(source_type)
    tgt = element_spec(target_type)
    if src is None or tgt is None:
        return True

    # ── motivation ────────────────────────────────────────────────────
    # Motivation elements answer "why", so they relate to the rest of the
    # model differently: a driver/goal/requirement does not *serve* or
    # *trigger* an application component, it influences it or is realized by
    # it. Handling the aspect up front keeps the structural rules below from
    # having to special-case it in five places.
    if _MOTIVATION in (src.aspect, tgt.aspect):
        if rel_type == "influence":
            # The motivation-specific relationship: something affects the
            # achievement of a motivation element (positively or otherwise).
            return tgt.aspect == _MOTIVATION
        if rel_type == "realization":
            # A requirement realizes a goal; anything in the core stack can
            # realize the requirement/goal/outcome it was built for.
            return tgt.aspect == _MOTIVATION
        if rel_type in ("aggregation", "composition", "specialization"):
            # Decomposing motivation into motivation: a goal aggregates
            # outcomes, a requirement specializes another requirement.
            return src.aspect == _MOTIVATION and tgt.aspect == _MOTIVATION
        if rel_type == "association":
            # Stakeholder <-> driver, and anything else the standard leaves
            # deliberately loose.
            return True
        # serving / access / triggering / flow / assignment have no meaning
        # against a motivation element.
        return False

    if rel_type == "influence":
        # Outside the motivation aspect, influence has no defined endpoints.
        return False

    # ── implementation & migration ────────────────────────────────────
    # Two canonical relationships here break the general rules below, so they
    # are stated explicitly rather than by weakening a rule the other layers
    # depend on:
    #   * "work package realizes deliverable" crosses behaviour -> passive,
    #     which the realization rule otherwise reserves for passive -> passive.
    #   * a plateau aggregates the elements that exist at that point in time,
    #     which is deliberately cross-layer — the whole point of a plateau.
    if src.layer == "implementation" or tgt.layer == "implementation":
        if rel_type == "realization" and src.aspect == _BEHAVIOR and tgt.aspect == _PASSIVE:
            return True
        if rel_type == "aggregation" and source_type == "plateau":
            return True

    if rel_type in ("composition", "aggregation"):
        # Structural decomposition: a whole and its parts are the same kind of
        # thing in the same layer. "A business process is composed of a
        # database" is the failure mode this rejects.
        return src.aspect == tgt.aspect and src.layer == tgt.layer

    if rel_type == "assignment":
        # Who/what performs or hosts something: an active-structure element is
        # assigned to behaviour it performs, or to a passive element it holds
        # (a node hosting an artifact).
        return src.aspect == _ACTIVE and tgt.aspect in (_BEHAVIOR, _PASSIVE)

    if rel_type == "realization":
        # Something concrete makes something more abstract real: internal
        # behaviour or a component realizes a service; an artifact realizes a
        # data object.
        if tgt.aspect == _BEHAVIOR:
            return src.aspect in (_ACTIVE, _BEHAVIOR)
        if tgt.aspect == _PASSIVE:
            return src.aspect == _PASSIVE
        return False

    if rel_type == "serving":
        # The one relationship that crosses layers freely — that is the point
        # of it, and of the layered viewpoint.
        return src.aspect in (_ACTIVE, _BEHAVIOR) and tgt.aspect in (_ACTIVE, _BEHAVIOR)

    if rel_type == "access":
        # Only ever touches data: behaviour (or the element performing it)
        # reads/writes a passive element.
        return src.aspect in (_ACTIVE, _BEHAVIOR) and tgt.aspect == _PASSIVE

    if rel_type in ("triggering", "flow"):
        # Temporal/informational sequence between things that *happen*.
        return src.aspect == _BEHAVIOR and tgt.aspect == _BEHAVIOR

    if rel_type == "specialization":
        # "Is a kind of" — only ever between two of the same kind of thing.
        return src.layer == tgt.layer and src.aspect == tgt.aspect

    if rel_type == "association":
        # Always legal by definition: it asserts only that two elements are
        # related somehow.
        return True

    return False


#: Ordered substitution preferences, most-meaning-preserving first.
#:
#: ``association`` is deliberately absent from every chain even though it is
#: always legal. Substituting it would "fix" any illegal relationship by
#: turning it into a claim with no content ("these two are related somehow"),
#: which reads as a real modeling decision the architect never made. When
#: nothing meaningful fits, the relationship is dropped and turned into an
#: open question instead — see ``ArchimateIR._enforce_relationship_legality``.
#:
#: **A candidate can only ever fire if its predicate is strictly weaker than
#: the one that just failed.** That is not obvious, and getting it wrong is
#: silent: the first version of this table paired ``composition`` with
#: ``aggregation`` and vice versa, but those two share a byte-identical
#: predicate (same aspect AND same layer), so *no illegal whole-part
#: relationship could ever be corrected* — every one was dropped, while the
#: prompt told the model such relationships would be auto-corrected. Eight
#: more entries were dead the same way (``flow``/``triggering`` are strictly
#: narrower than ``serving``; ``aggregation`` is identical to
#: ``specialization``). ``tests/test_ir_archimate.py`` now asserts every entry
#: below is reachable for at least one element pair, so a dead chain fails CI
#: instead of quietly costing edges.
#:
#: The chains below run structural → behavioural → data → generic, i.e. the
#: correction that keeps the most of the author's original intent first:
#: an ``application-component`` "composed of" a ``data-object`` becomes
#: ``assignment`` ("this component holds that data"), which is what the
#: modeller meant, rather than vanishing.
_SUBSTITUTIONS: dict[str, tuple[str, ...]] = {
    "composition": ("assignment", "access", "realization", "serving"),
    "aggregation": ("assignment", "access", "realization", "serving"),
    "assignment": ("realization", "access", "serving"),
    "realization": ("assignment", "access", "serving"),
    "serving": ("access", "realization"),
    "access": ("serving", "realization"),
    "triggering": ("serving", "access"),
    "flow": ("serving", "access"),
    "specialization": ("realization", "serving"),
    "association": (),
    # An influence aimed at a non-motivation target is usually a realization
    # ("this component realizes that requirement") pointed the wrong way, or a
    # plain serving relationship. Both are strictly weaker than influence's
    # "target must be motivation" predicate, so both can actually fire.
    "influence": ("realization", "serving"),
}


def nearest_legal_substitute(source_type: str, rel_type: str, target_type: str) -> str | None:
    """The closest legal relationship to ``rel_type`` for this pair, or ``None``.

    ``None`` means "there is no honest correction" — the caller drops the
    relationship and raises an open question rather than inventing one.
    """
    for candidate in _SUBSTITUTIONS.get(rel_type, ()):
        if is_legal_relationship(source_type, candidate, target_type):
            return candidate
    return None


# Import-time consistency guard: the Literal and the table must describe the
# same vocabulary, or a type could be accepted by Pydantic and then blow up in
# the compiler with a KeyError at render time.
_missing = ARCHIMATE_ELEMENT_TYPES - set(ARCHIMATE_ELEMENT_TABLE)
_extra = set(ARCHIMATE_ELEMENT_TABLE) - ARCHIMATE_ELEMENT_TYPES
if _missing or _extra:  # pragma: no cover - a developer error, caught at import
    raise RuntimeError(
        "ArchimateElementType and ARCHIMATE_ELEMENT_TABLE disagree: "
        f"missing rows {sorted(_missing)}, unknown rows {sorted(_extra)}"
    )
_bad_substitutes = {
    rel: tuple(c for c in chain if c not in ARCHIMATE_RELATIONSHIP_TYPES)
    for rel, chain in _SUBSTITUTIONS.items()
}
_bad_substitutes = {k: v for k, v in _bad_substitutes.items() if v}
if _bad_substitutes:  # pragma: no cover - a developer error, caught at import
    raise RuntimeError(f"_SUBSTITUTIONS names unknown relationship types: {_bad_substitutes}")
