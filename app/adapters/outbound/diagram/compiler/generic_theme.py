"""Style strings for the generic (non-cloud) image-replication compiler.

Kept separate from ``theme.py`` deliberately: that module's helpers are keyed
to cloud concepts (platform accent colours, ``shape_type`` icon placeholders,
``SubnetTier``/``GroupKind``) that don't apply here. The generic path maps a
closed, small shape vocabulary directly to plain draw.io style keywords —
there is no ``shape_type``/MCP icon-resolution step for these cells at all.

Colours match the same neutral palette ``theme.leaf_style`` already uses
(``#F5F5F5`` fill / ``#B3B3B3`` stroke / ``#333333`` font) so a generic
diagram reads as visually consistent with every other view in the app,
rather than introducing a second visual language.
"""

from __future__ import annotations

from app.domain.ir.generic import GenericNodeShape

_FILL = "#F5F5F5"
_STROKE = "#B3B3B3"
_FONT = "#333333"

_BASE = f"whiteSpace=wrap;html=1;fillColor={_FILL};strokeColor={_STROKE};strokeWidth=1;fontColor={_FONT};fontSize=11;"

_SHAPE_STYLES: dict[GenericNodeShape, str] = {
    "rectangle": f"rounded=0;{_BASE}",
    "rounded_rectangle": f"rounded=1;arcSize=12;{_BASE}",
    "ellipse": f"ellipse;{_BASE}",
    "diamond": f"rhombus;{_BASE}",
    "parallelogram": f"shape=parallelogram;perimeter=parallelogramPerimeter;{_BASE}",
    "hexagon": f"shape=hexagon;perimeter=hexagonPerimeter2;{_BASE}",
    "cylinder": f"shape=cylinder;boundedLbl=1;{_BASE}",
    "cloud": f"ellipse;shape=cloud;{_BASE}",
    "actor": (
        f"shape=umlActor;outlineConnect=0;{_BASE}"
        "verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
    ),
    "text": (
        f"text;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontColor={_FONT};fontSize=11;"
    ),
}


def node_style(shape: GenericNodeShape) -> str:
    """Plain draw.io style string for a closed generic-shape key."""
    return _SHAPE_STYLES.get(shape, _SHAPE_STYLES["rectangle"])


def edge_style(*, line_style: str, dashed: bool, arrow: str) -> str:
    """Style string for a ``GenericEdge`` — matches what was actually drawn,
    not a fixed convention (unlike cloud flows, generic edges carry no
    classification/protection channel to encode as colour)."""
    if line_style == "straight":
        base = "edgeStyle=none;rounded=0;"
    elif line_style == "curved":
        base = "edgeStyle=none;curved=1;rounded=0;"
    else:  # "orthogonal"
        base = "edgeStyle=orthogonalEdgeStyle;rounded=1;jettySize=auto;orthogonalLoop=1;"
    style = f"{base}html=1;strokeColor={_STROKE};fontColor={_FONT};fontSize=11;labelBackgroundColor=#ffffff;"
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
