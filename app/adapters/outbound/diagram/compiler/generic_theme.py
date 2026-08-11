"""Style strings for the generic (non-cloud) image-replication compiler.

Kept separate from ``theme.py`` deliberately: that module's helpers are keyed
to cloud concepts (platform accent colours, ``shape_type`` icon placeholders,
``SubnetTier``/``GroupKind``) that don't apply here. The generic path maps a
closed, small shape vocabulary directly to plain draw.io style keywords.

Icon resolution *does* run on this path, contrary to what this docstring used
to say: ``compiler/generic.py`` stamps a transient ``shape_type`` from
``GenericNode.icon_hint`` and ``xml_pipeline._run_generic_image_pipeline``
resolves it via MCP with ``platform="any"``. It is deliberately narrowed
though — see ``generic._resolvable_icon_hint``, which skips resolution when
it would overwrite a colour traced from the source image or produce a broken
double-``shape`` style on a keyword-styled shape.

When the source image gave an actual fill/stroke/line colour (``GenericNode.
fill_color``/``stroke_color``, ``GenericEdge.color``), it's used verbatim —
this is the one view in the app where colour is an as-is fidelity concern,
not a design-system choice. Falling back to the same neutral placeholder
palette ``theme.leaf_style`` uses (``#F5F5F5`` fill / ``#B3B3B3`` stroke /
``#333333`` font) when the image genuinely had no distinct colour there.
"""

from __future__ import annotations

from app.domain.ir.generic import GenericNodeShape

_FILL = "#F5F5F5"
_STROKE = "#B3B3B3"
_FONT = "#333333"

_SHAPE_PREFIX: dict[GenericNodeShape, str] = {
    "rectangle": "rounded=0;",
    "rounded_rectangle": "rounded=1;arcSize=12;",
    "ellipse": "ellipse;",
    "diamond": "rhombus;",
    "parallelogram": "shape=parallelogram;perimeter=parallelogramPerimeter;",
    "hexagon": "shape=hexagon;perimeter=hexagonPerimeter2;",
    "cylinder": "shape=cylinder;boundedLbl=1;",
    "cloud": "ellipse;shape=cloud;",
    "actor": (
        "shape=umlActor;outlineConnect=0;"
        "verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
    ),
    "text": "text;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;",
}


def _channel(hex_color: str, start: int) -> int:
    return int(hex_color[start:start + 2], 16)


def _luminance(hex_color: str) -> float:
    """Relative luminance in [0, 1] (0=black, 1=white) for a '#RRGGBB' string."""
    r, g, b = (_channel(hex_color, i) / 255.0 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _darker(hex_color: str, factor: float = 0.7) -> str:
    """A darker shade of ``hex_color`` — used as an auto-derived border when
    the image gave a fill colour but no distinct stroke colour of its own."""
    r, g, b = (max(0, int(_channel(hex_color, i) * factor)) for i in (1, 3, 5))
    return f"#{r:02X}{g:02X}{b:02X}"


def node_style(shape: GenericNodeShape, *, fill_color: str | None = None, stroke_color: str | None = None) -> str:
    """Draw.io style string for a closed generic-shape key.

    ``fill_color``/``stroke_color`` (validated 6-digit hex from the IR) are
    used verbatim when the source image showed one; font colour is then
    chosen for contrast against whichever fill ends up in use, so a dark
    source colour never produces unreadable dark-on-dark text.
    """
    prefix = _SHAPE_PREFIX.get(shape, _SHAPE_PREFIX["rectangle"])
    if shape == "text":
        # A text shape has no fill/border box to colour — the image's colour
        # becomes the label's own font colour instead.
        return f"{prefix}fontColor={fill_color or _FONT};fontSize=11;"
    fill = fill_color or _FILL
    stroke = stroke_color or (_darker(fill) if fill_color else _STROKE)
    font = ("#000000" if _luminance(fill) > 0.6 else "#FFFFFF") if fill_color else _FONT
    return f"{prefix}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1;fontColor={font};fontSize=11;"


def edge_style(*, line_style: str, dashed: bool, arrow: str, color: str | None = None) -> str:
    """Style string for a ``GenericEdge`` — matches what was actually drawn,
    not a fixed convention (unlike cloud flows, generic edges carry no
    classification/protection channel to encode as colour)."""
    if line_style == "straight":
        base = "edgeStyle=none;rounded=0;"
    elif line_style == "curved":
        base = "edgeStyle=none;curved=1;rounded=0;"
    else:  # "orthogonal"
        base = "edgeStyle=orthogonalEdgeStyle;rounded=1;jettySize=auto;orthogonalLoop=1;"
    stroke = color or _STROKE
    style = f"{base}html=1;strokeColor={stroke};fontColor={_FONT};fontSize=11;labelBackgroundColor=#ffffff;"
    if dashed:
        style += "dashed=1;"
    if arrow == "none":
        style += "startArrow=none;endArrow=none;"
    elif arrow == "both":
        style += "startArrow=classic;startFill=1;endArrow=classic;endFill=1;"
    return style


def group_style(boundary: str) -> str:
    """Container style for a ``GenericGroup`` — only rendered with a visible
    border when the source image actually showed one."""
    base = (
        "rounded=0;whiteSpace=wrap;html=1;container=1;dropTarget=1;collapsible=0;"
        "recursiveResize=0;fillColor=none;align=left;verticalAlign=top;"
        "spacingLeft=12;spacingTop=8;fontStyle=1;fontSize=12;"
    )
    if boundary == "solid":
        return f"{base}strokeColor={_STROKE};strokeWidth=1;fontColor={_FONT};"
    if boundary == "dashed":
        return f"{base}strokeColor={_STROKE};strokeWidth=1;dashed=1;fontColor={_FONT};"
    return f"{base}strokeColor=none;fontColor={_FONT};"
