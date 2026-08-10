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

#: v1 layers. The wider ArchiMate 3.x set (motivation/strategy/physical/
#: implementation) is deliberately absent — see the module docstring.
ArchimateLayer = Literal["business", "application", "technology"]

#: ArchiMate's three "aspects" — the column axis of the standard's own
#: framework grid. Almost every relationship rule below is expressible purely
#: in terms of these, which is why they, not the element types, are what the
#: legality function reasons over.
ArchimateAspect = Literal["active-structure", "behavior", "passive-structure"]

#: Corner treatment. ArchiMate draws behaviour with rounded corners and
#: structure with square ones; that is the whole distinction at v1 scope
#: (the diagonal-corner motivation shapes arrive with the motivation layer).
ArchimateShape = Literal["square", "rounded"]

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
]

ArchimateRelationshipType = Literal[
    "composition",
    "aggregation",
    "assignment",
    "realization",
    "serving",
    "access",
    "influence",
    "triggering",
    "flow",
    "specialization",
    "association",
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
}

ARCHIMATE_ELEMENT_TYPES: frozenset[str] = frozenset(get_args(ArchimateElementType))
ARCHIMATE_RELATIONSHIP_TYPES: tuple[str, ...] = get_args(ArchimateRelationshipType)

#: Band render order, top to bottom — the standard ArchiMate reading order
#: (business drives application, application runs on technology).
LAYER_ORDER: tuple[str, ...] = ("business", "application", "technology")

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

    if rel_type == "influence":
        # Influence is defined only against motivation elements, which v1's
        # vocabulary does not include — so it is never legal here yet, and
        # always gets substituted (see the chains below). Kept in the
        # relationship enum so diagrams authored now stay valid when the
        # motivation layer lands.
        return False

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
_SUBSTITUTIONS: dict[str, tuple[str, ...]] = {
    "composition": ("aggregation",),
    "aggregation": ("composition",),
    "assignment": ("realization", "serving"),
    "realization": ("serving", "assignment"),
    "serving": ("flow", "triggering", "realization"),
    "access": ("serving", "flow"),
    "influence": ("serving", "triggering"),
    "triggering": ("flow", "serving"),
    "flow": ("triggering", "serving"),
    "specialization": ("realization", "aggregation"),
    "association": (),
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
