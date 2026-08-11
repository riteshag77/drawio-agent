"""Canonical draw.io geometry constants — the single source of truth.

Both the IR compiler (which *produces* geometry) and xml_diagnostics (which
*checks* geometry) read these. Keeping them in one module is what makes
"the compiler emits diagrams the diagnostics engine considers clean" a
guarantee rather than a coincidence: a spacing rule can only be changed in one
place, and both sides move together.

Values are the product spacing contracts (density tiers, network gaps) shared
by the compiler and diagnostics — formerly mirrored in deleted XML prompt packs.
"""

from __future__ import annotations

# ── Leaf icons ─────────────────────────────────────────────────────────
LEAF_W = 78.0
LEAF_H = 78.0
#: Under-icon label band added to a leaf's effective footprint when the style
#: renders its label below the icon.
LABEL_BAND_H = 28.0

LEAF_GRID_MAX_COLS = 4
LEAF_MIN_GAP = 20.0

# ── Pitch (left-edge to left-edge spacing between adjacent icons) ──────
#: Data-path rows (subnets, edge layer) prefer 200; 170-230 is accepted.
DATA_PITCH_RANGE = (170.0, 230.0)
DATA_CANONICAL_PITCH = 200.0
#: Cross-cutting shelves are denser: 150-190 accepted, 170 canonical.
GROUP_PITCH_RANGE = (150.0, 190.0)
GROUP_CANONICAL_PITCH = 170.0
#: Row-to-row spacing when a grid wraps.
ROW_PITCH_Y = 130.0
#: Rows are clustered by top-y proximity within this tolerance.
ROW_TOL = 20.0

# ── Container padding ──────────────────────────────────────────────────
#: Left/right/bottom padding inside any container (network.txt: 20-28px).
PAD = 24.0
#: Top padding, leaving room for the container's title band (network.txt: >=48px).
TITLE_PAD = 48.0

# ── Network-view specific gaps ─────────────────────────────────────────
#: Vertical clear space between one subnet tier's *box* and the next tier's box.
#: network.txt's "100-140px between subnet tiers" is a tier-to-tier figure that
#: already includes each box's own padding, and applying it box-to-box on top of
#: that padding stacked ~192px of dead canvas between every pair of tiers
#: (24px bottom pad + gap + 48px title band). 56px box-to-box lands the
#: icon-to-icon distance back inside the intended range while leaving a clear
#: corridor for app->data edges and their labels.
SUBNET_TIER_GAP = 56.0
#: Clear space between the lowest zone's bottom and the cross-cutting shelves
#: (network.txt: minimum 70-80px).
GROUP_GAP_BELOW_ZONES = 80.0
#: Clear space between the VPC gateway's row and the zones row below it.
GATEWAY_GAP_BELOW = 60.0
#: Horizontal gap between peer availability zones (network.txt: 20-30px).
ZONE_GAP_X = 24.0
#: Horizontal gap between peer VPCs inside a region.
VPC_GAP_X = 32.0
#: Horizontal gap between peer regions (regions sit side by side, same as
#: peer zones/VPCs — not stacked).
REGION_GAP_X = 32.0
#: Gap between the edge/global layer, the regions row, and the connectivity
#: hub shelf — the three vertically-stacked bands inside Cloud.
EDGE_LAYER_GAP_Y = 32.0

# ── ArchiMate view ─────────────────────────────────────────────────────
#: ArchiMate elements are labelled *inside* the box (the notation puts its type
#: glyph in the top-right corner and the name in the body), so they cannot use
#: the square LEAF_W/LEAF_H icon footprint the cloud views use — a 78px-wide
#: box cannot hold "Policy Administration System" at a readable size. These are
#: the standard ArchiMate proportions and, like every other constant here, they
#: are compiler-owned: the model never sizes anything.
ARCHIMATE_ELEMENT_W = 170.0
ARCHIMATE_ELEMENT_H = 70.0
#: Pitch between adjacent elements (left edge to left edge / top to top).
ARCHIMATE_ELEMENT_PITCH_X = ARCHIMATE_ELEMENT_W + 30.0
ARCHIMATE_ELEMENT_PITCH_Y = ARCHIMATE_ELEMENT_H + 40.0
#: Elements per row inside a band or grouping before wrapping. Lower than the
#: cloud views' 4-per-row grid because each box is more than twice as wide.
ARCHIMATE_MAX_COLS = 4
#: Vertical clear space between two layer bands. Generous on purpose: the
#: cross-layer serving/realization relationships that give a layered view its
#: meaning all run through these gaps, and they need room for a label.
ARCHIMATE_BAND_GAP_Y = 70.0
#: Horizontal gap between peer groupings inside one band.
ARCHIMATE_GROUP_GAP_X = 36.0
#: Groupings per row inside a band before wrapping to a second row.
ARCHIMATE_GROUPS_PER_ROW = 3
#: An element count above which a band is crowded enough that domain groupings
#: earn their visual cost. Below it, groupings still appear in the overview
#: panel but not as boxes — see compiler/archimate.py.
ARCHIMATE_GROUPING_THRESHOLD = 8

# ── Root-layer placement ───────────────────────────────────────────────
#: Cloud boundary's origin. Non-zero so external actors placed to its left
#: still land at positive coordinates (draw.io tolerates negatives, but the
#: diagnostics engine flags them and they complicate export bounds).
CLOUD_ORIGIN_X = 360.0
CLOUD_ORIGIN_Y = 40.0
#: Horizontal clear space between an external actor column and the Cloud box.
ACTOR_GAP_X = 80.0
#: Vertical spacing between stacked external actors.
ACTOR_GAP_Y = 40.0

# ── Defaults for malformed input (used by diagnostics' repair fixers) ──
DEFAULT_LEAF_SIZE = (LEAF_W, LEAF_H)
DEFAULT_CONTAINER_SIZE = (160.0, 120.0)


def pitch_for(is_group: bool) -> float:
    """Canonical horizontal pitch for a row of icons."""
    return GROUP_CANONICAL_PITCH if is_group else DATA_CANONICAL_PITCH


def grid_size(count: int, *, is_group: bool) -> tuple[float, float, int, int]:
    """Return ``(width, height, cols, rows)`` of a ``count``-icon grid's content box.

    Width spans left edge of the first icon to right edge of the last, so the
    caller adds its own padding. Height includes the under-icon label band.
    """
    if count <= 0:
        return 0.0, 0.0, 0, 0
    cols = min(LEAF_GRID_MAX_COLS, count)
    rows = (count + cols - 1) // cols
    pitch = pitch_for(is_group)
    width = (cols - 1) * pitch + LEAF_W
    height = (rows - 1) * ROW_PITCH_Y + LEAF_H + LABEL_BAND_H
    return width, height, cols, rows
