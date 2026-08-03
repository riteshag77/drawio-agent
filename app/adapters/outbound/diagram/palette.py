"""Canonical diagram colours — the single source of truth.

Same role for colour that ``geometry_spec.py`` plays for geometry: the module
that *produces* styles (``compiler/theme.py``) reads these tables, so the
compiler's output is already the final corridor appearance (palette + theme
are authoritative; there is no downstream color-normalization pass).

Values mirror each corridor view's section colours (Logical domains / Security
zones / Data-flow phases), with three deliberate corrections documented at
``CORRIDOR_COLORS`` below.

One visual language across all four views: a colour means the same thing on
Logical, Security, Data-flow and Network alike, so a reader learns it once.
"""

from __future__ import annotations

# ── Corridor containers (Logical domains / Security zones / Data-flow phases) ──
#
#: ``{corridor_id: (fillColor, strokeColor)}``.
#:
#: Three corrections against the raw pack tables, each fixing a case where two
#: semantically different containers were rendering identically:
#:
#: 1. ``zone_public`` was byte-identical to ``zone_dmz`` (both #FFF2CC/#D6B656)
#:    — the internet-facing surface and the perimeter looked like one zone.
#: 2. ``zone_detect`` shared #DAE8FC with ``zone_private``.
#: 3. ``phase_saas_producer``/``phase_saas_consumer`` had no entries of their
#:    own, so both fell through prefix-matching to ``phase_saas`` and rendered
#:    the same despite being opposite ends of a data exchange.
#:
#: Security's trust axis reads as a *hue* journey — yellow/exposed through
#: blue/internal to red/regulated — not a luminance ramp. Deliberate: a strict
#: light-to-dark ramp across four different hues would force muddy in-between
#: colours and destroy the categorical meaning each hue already carries. What
#: the axis does guarantee is that its four fills are mutually distinct and
#: that ``zone_restricted`` is the visually strongest (see
#: ``SECURITY_TRUST_ORDER``). Zones that are not ranks (identity, partner,
#: detect) stay categorical on purpose: giving them axis positions would imply
#: an ordering they do not have.
#:
#: Some same-category peers *do* share a fill, on purpose — see
#: ``INTENTIONAL_COLOR_TWINS``.
CORRIDOR_COLORS: dict[str, tuple[str, str]] = {
    # ── logical: channel → application → data, with identity/platform aside ──
    "domain_experience": ("#F5F5F5", "#666666"),
    "domain_identity": ("#E1D5E7", "#9673A6"),
    "domain_core": ("#DAE8FC", "#6C8EBF"),
    "domain_commerce": ("#DAE8FC", "#6C8EBF"),
    "domain_platform": ("#E8EEF7", "#147EBA"),
    "domain_data": ("#D5E8D4", "#82B366"),
    "domain_saas": ("#FFF2CC", "#D6B656"),
    # ── security: monotone trust axis + categorical off-axis zones ──
    "zone_public": ("#FFE6CC", "#D79B00"),
    "zone_dmz": ("#FFF2CC", "#D6B656"),
    "zone_identity": ("#E1D5E7", "#9673A6"),
    "zone_partner": ("#D5E8D4", "#82B366"),
    "zone_private": ("#DAE8FC", "#6C8EBF"),
    "zone_restricted": ("#F8CECC", "#B85450"),
    "zone_detect": ("#E8EEF7", "#147EBA"),
    "zone_external_saas": ("#FFF2CC", "#D6B656"),
    # ── data flow: ingest → process → store, with consume/control/SaaS shelves ──
    "phase_ingest": ("#FFF2CC", "#D6B656"),
    "phase_process": ("#DAE8FC", "#6C8EBF"),
    "phase_store": ("#D5E8D4", "#82B366"),
    "phase_consume": ("#D5E8D4", "#82B366"),
    "phase_control": ("#E8EEF7", "#147EBA"),
    "phase_saas_producer": ("#FFF2CC", "#D6B656"),
    "phase_saas_consumer": ("#FFE6CC", "#D79B00"),
    "phase_saas": ("#FFF2CC", "#D6B656"),
}

#: Security zones along the trust axis, least to most restricted. Only the
#: zones whose colours are meant to read as one journey — the categorical ones
#: (identity/partner/detect) are deliberately absent. Documentation the tests
#: can enforce; nothing reads it at runtime.
SECURITY_TRUST_ORDER: tuple[str, ...] = (
    "zone_public", "zone_dmz", "zone_private", "zone_restricted",
)

#: Corridor pairs that share a fill **on purpose**, because they are peers of
#: the same category — the shared hue is what makes the category readable, and
#: the label (plus, for shelves, the dashed border) tells them apart. Anything
#: NOT listed here must be visually distinct; ``tests/test_palette.py`` enforces
#: that, so an accidental collision like the old ``zone_public``/``zone_dmz``
#: one cannot creep back in unnoticed.
INTENTIONAL_COLOR_TWINS: frozenset[frozenset[str]] = frozenset({
    # Two application domains of equal standing — same category, distinct labels.
    frozenset({"domain_core", "domain_commerce"}),
    # Both are "data at rest"; consume is additionally a dashed shelf.
    frozenset({"phase_store", "phase_consume"}),
    # Both are third-party/untrusted surfaces; the SaaS ones are dashed shelves.
    frozenset({"zone_dmz", "zone_external_saas"}),
    frozenset({"phase_ingest", "phase_saas_producer"}),
    # phase_saas is the prefix fallback for any phase_saas_* id beyond the two
    # known shelves, so it duplicates the producer entry by construction.
    frozenset({"phase_saas", "phase_saas_producer"}),
    frozenset({"phase_saas", "phase_ingest"}),
})

#: Corridors rendered as an anchored shelf rather than a main-row peer. They
#: keep a dashed, heavier border so "this sits outside the main journey" stays
#: visible — ``theme.shelf_style()`` owns that distinction.
SHELF_IDS: frozenset[str] = frozenset({
    "domain_saas",
    "zone_external_saas",
    "phase_consume", "phase_control",
    "phase_saas", "phase_saas_producer", "phase_saas_consumer",
})


# ── Flow classification (edge stroke colour) ────────────────────────────────
#
#: One more visual channel than "primary" already gives (strokeWidth/dashed),
#: applied independently: classification colours the line, primary/secondary
#: still controls its weight and dash. A flow can be both non-primary and
#: carry PII — the two channels must never collapse into one.
CLASSIFICATION_COLORS: dict[str, str] = {
    "none": "#6B7280",
    "internal": "#232F3E",
    "confidential": "#8C4FFF",
    "pii": "#B85450",
    "pci": "#C7131F",
}

#: Leaf border when ``criticality="critical"`` — same red as ``zone_restricted``
#: and the ``pii`` flow colour, on purpose: "this needs your attention" reads
#: as one colour across the whole visual system, not three unrelated reds.
CRITICAL_LEAF_STROKE = "#B85450"


def corridor_colors(corridor_id: str) -> tuple[str, str] | None:
    """``(fillColor, strokeColor)`` for a corridor id, by exact match then by
    longest prefix — so a de-duplicated id like ``domain_core_2`` still
    resolves. Longest-first matters: ``phase_saas_producer`` must not be
    shadowed by the shorter ``phase_saas``."""
    exact = CORRIDOR_COLORS.get(corridor_id)
    if exact is not None:
        return exact
    for prefix in sorted(CORRIDOR_COLORS, key=len, reverse=True):
        if corridor_id.startswith(prefix):
            return CORRIDOR_COLORS[prefix]
    return None


def is_shelf(corridor_id: str) -> bool:
    """Whether ``corridor_id`` is an anchored shelf, matched the same way as
    ``corridor_colors`` so de-duplicated ids behave identically."""
    if corridor_id in SHELF_IDS:
        return True
    return any(corridor_id.startswith(sid) for sid in sorted(SHELF_IDS, key=len, reverse=True))
