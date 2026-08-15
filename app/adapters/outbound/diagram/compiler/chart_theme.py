"""Palette and style strings for the chart compiler (bar / pie / gantt).

Third theme module alongside ``theme.py`` (cloud views) and ``generic_theme.py``
(as-is image replication), for the same reason those two are separate: the
vocabulary doesn't overlap. Cloud styling is keyed to platform accents and
``shape_type`` icon placeholders; generic styling reproduces colours traced
from a source image. Neither concept exists on a chart, where colour carries
*series identity* and every other mark is deliberately recessive.

## Palette provenance

``SERIES`` is the validated categorical order from the data-viz reference
palette, assigned in fixed slot order and never cycled — which is why
``domain/ir/simple.py`` caps series at 6 and pie slices at ``MAX_PIE_SLICES``
rather than wrapping around to slot 1 and silently drawing two different
things in the same colour.

Validated against this app's actual chart surface — pure white, not the
reference's off-white, because ``static/js/diagram.js`` pins the embed canvas
to ``#ffffff`` regardless of editor theme:

    node scripts/validate_palette.js \\
      "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300" --mode light --surface "#ffffff"

    [PASS] Lightness band      all 6 inside L 0.43-0.77
    [PASS] Chroma floor        all 6 >= 0.1
    [PASS] CVD separation      worst adjacent aqua<->yellow dE 9.1 (protan)
    [PASS] Normal-vision floor worst adjacent magenta<->yellow dE 19.6
    [WARN] Contrast vs surface aqua/yellow/magenta below 3:1 - relief required

That WARN is discharged, not ignored: every chart this module styles ships
visible text identity — a legend row per series and a direct value label per
bar/slice — so no reader has to separate those three hues from the white
background to read the chart. Removing those labels would re-open it.

Only a light palette is defined, deliberately: the draw.io canvas is forced
white, so there is no dark surface for a second set of steps to target.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.compiler import palette

#: Re-exported from ``palette`` so existing call sites and tests keep one
#: import, while the hues themselves live in one place for every view.
SERIES = palette.SERIES
PALETTE_SLOTS = palette.PALETTE_SLOTS
series_color = palette.series_color

# Ink and furniture. Text never wears a series colour (the mark beside it
# carries identity); grid and axis stay recessive so the data reads first.
TEXT_PRIMARY = "#0B0B0B"
TEXT_SECONDARY = "#52514E"
AXIS = "#B3B3B3"
GRID = "#E6E6E6"


def title_style() -> str:
    return (
        f"text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;"
        f"fontSize=16;fontStyle=1;fontColor={TEXT_PRIMARY};"
    )


def axis_caption_style(*, vertical: bool = False) -> str:
    """Caption for an axis. ``vertical`` rotates the text for the value axis —
    ``horizontal=0`` is draw.io's own vertical-text flag, not a rotation
    transform, so the cell's geometry stays axis-aligned and readable."""
    rotation = "horizontal=0;" if vertical else ""
    return (
        f"text;html=1;align=center;verticalAlign=middle;whiteSpace=wrap;{rotation}"
        f"fontSize=11;fontColor={TEXT_SECONDARY};"
    )


def tick_label_style(*, align: str = "right") -> str:
    return (
        f"text;html=1;align={align};verticalAlign=middle;whiteSpace=wrap;"
        f"fontSize=10;fontColor={TEXT_SECONDARY};"
    )


def value_label_style() -> str:
    """Direct label on a bar or slice — ink, never the series colour."""
    return (
        f"text;html=1;align=center;verticalAlign=middle;whiteSpace=nowrap;"
        f"fontSize=10;fontColor={TEXT_PRIMARY};"
    )


def bar_style(color: str) -> str:
    """A bar. No border: a stroke on a filled bar reads as a second encoding,
    and the 2px surface gap between neighbours already separates them."""
    return f"rounded=0;whiteSpace=wrap;html=1;fillColor={color};strokeColor=none;"


def segment_style(color: str, angle_degrees: float) -> str:
    """One segment of a line series.

    A thin rectangle turned about its own centre by draw.io's ``rotation=``,
    which is what lets a *sloped* mark stay a vertex. Drawing it as an edge
    instead would hand it to ``remove_dangling_edges`` (it connects no cells)
    and ``fix_edge_anchors_from_geometry``; see ``rule_style`` for the same
    reasoning applied to gridlines.
    """
    return (
        f"rounded=0;html=1;fillColor={color};strokeColor=none;"
        f"rotation={round(angle_degrees, 2)};"
    )


def marker_style(color: str) -> str:
    """A point marker on a line series — a filled dot with a surface ring, so
    two series crossing at a point stay separable."""
    return f"ellipse;html=1;fillColor={color};strokeColor=#FFFFFF;strokeWidth=2;"


def pie_slice_style(color: str, start_fraction: float, end_fraction: float) -> str:
    """One wedge of ``mxgraph.basic.pie``.

    Every slice cell spans the *whole* circle's bounding box and differs only
    in its start/end angle, which is why slices carry no label of their own —
    all of them would centre on the same point (verified in the embed: three
    labels stacked on the pie's centre). Identity lives in the legend instead.
    """
    return (
        f"shape=mxgraph.basic.pie;startAngle={round(start_fraction, 4)};"
        f"endAngle={round(end_fraction, 4)};"
        f"fillColor={color};strokeColor=#FFFFFF;strokeWidth=2;html=1;"
    )


def legend_swatch_style(color: str) -> str:
    return f"rounded=1;arcSize=40;whiteSpace=wrap;html=1;fillColor={color};strokeColor=none;"


def legend_text_style() -> str:
    return (
        f"text;html=1;align=left;verticalAlign=middle;whiteSpace=nowrap;"
        f"fontSize=11;fontColor={TEXT_PRIMARY};"
    )


def rule_style(color: str) -> str:
    """A hairline rule — gridline, axis, or baseline.

    Emitted as a 1px-thin *rectangle* rather than an edge, which is the whole
    reason a chart page contains no edges at all. Two repair passes act on
    every edge in a document and both would wreck a chart:
    ``remove_dangling_edges`` deletes edges without resolvable endpoints (a
    gridline connects nothing, so every one of them would vanish), and
    ``fix_edge_anchors_from_geometry`` recomputes endpoints from surrounding
    geometry. A rectangle is invisible to both.
    """
    return f"rounded=0;html=1;fillColor={color};strokeColor=none;"


def gridline_style() -> str:
    return rule_style(GRID)


def axis_line_style() -> str:
    return rule_style(AXIS)
