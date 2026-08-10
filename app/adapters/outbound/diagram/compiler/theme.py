"""Container and leaf style strings, by platform and container kind.

Styling is entirely the compiler's job — the IR carries no style data at all.
That is the whole point of the split: the model cannot emit a malformed style
because it never emits styles.

Leaf styles are deliberately *placeholders* carrying ``shape_type``. The MCP
layer later merges official icon stencils into them, preserving the label
attributes. This preserves the LLM/MCP ownership split documented in
docs/ENV_AND_MCP.md — the compiler is simply where the placeholder now comes
from, instead of the model.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import palette
from app.domain.ir.network import GroupKind, SubnetTier

_BASE_CONTAINER = (
    "rounded=1;arcSize=2;whiteSpace=wrap;html=1;container=1;dropTarget=1;"
    "collapsible=0;recursiveResize=0;fillColor=none;"
)

#: Per-platform accent colours for the Cloud/Region/VPC chain.
_PLATFORM_COLORS: dict[str, dict[str, str]] = {
    "aws": {"cloud": "#232F3E", "region": "#8C4FFF", "vpc": "#248814"},
    "gcp": {"cloud": "#1A73E8", "region": "#4285F4", "vpc": "#34A853"},
    "azure": {"cloud": "#0078D4", "region": "#50E6FF", "vpc": "#0078D4"},
    "multi": {"cloud": "#3B4A5A", "region": "#6B7280", "vpc": "#248814"},
}

_SUBNET_COLORS: dict[SubnetTier, str] = {
    "public": "#7AA116",
    "app": "#00A4A6",
    "data": "#C7131F",
}

_GROUP_COLORS: dict[GroupKind, str] = {
    "security": "#DD344C",
    "monitoring": "#147EBA",
    "integration": "#E7157B",
    "management": "#6B7280",
}

_ZONE_COLOR = "#6B7280"
_EDGE_LAYER_COLOR = "#E7157B"


def _palette(platform: str) -> dict[str, str]:
    return _PLATFORM_COLORS.get((platform or "aws").lower(), _PLATFORM_COLORS["aws"])


def _titled(color: str, *, dashed: bool, width: int = 1, indent: int = 40, bold: bool = False) -> str:
    """Container style with its title in the top band, beside the group icon."""
    style = (
        f"{_BASE_CONTAINER}strokeColor={color};strokeWidth={width};"
        f"{'dashed=1;' if dashed else ''}"
        f"align=left;verticalAlign=top;spacingLeft={indent};spacingTop=4;"
    )
    if bold:
        style += "fontStyle=1;"
    return style


def cloud_style(platform: str) -> str:
    return _titled(_palette(platform)["cloud"], dashed=False, width=2, bold=True)


def region_style(platform: str) -> str:
    return _titled(_palette(platform)["region"], dashed=True)


def vpc_style(platform: str) -> str:
    return _titled(_palette(platform)["vpc"], dashed=False)


def zone_style() -> str:
    return (
        f"{_BASE_CONTAINER}strokeColor={_ZONE_COLOR};strokeWidth=1;dashed=1;"
        f"align=left;verticalAlign=top;spacingLeft=12;spacingTop=8;"
        f"fontStyle=1;fontSize=12;fontColor={_ZONE_COLOR};"
    )


def subnet_style(tier: SubnetTier) -> str:
    color = _SUBNET_COLORS[tier]
    return (
        f"{_BASE_CONTAINER}strokeColor={color};strokeWidth=1;"
        f"align=left;verticalAlign=top;spacingLeft=12;spacingTop=8;"
        f"fontSize=11;fontColor={color};"
    )


def group_style(kind: GroupKind) -> str:
    color = _GROUP_COLORS[kind]
    return (
        f"{_BASE_CONTAINER}strokeColor={color};strokeWidth=1;dashed=1;"
        f"align=left;verticalAlign=top;spacingLeft=12;spacingTop=8;"
        f"fontStyle=1;fontSize=12;fontColor={color};"
    )


def edge_layer_style() -> str:
    return (
        f"{_BASE_CONTAINER}strokeColor={_EDGE_LAYER_COLOR};strokeWidth=1;dashed=1;"
        f"align=left;verticalAlign=top;spacingLeft=12;spacingTop=8;"
        f"fontStyle=1;fontSize=12;fontColor={_EDGE_LAYER_COLOR};"
    )


def _corridor_container(corridor_id: str, *, shelf: bool) -> str:
    """Domain/zone/phase container style, coloured from ``palette``.

    ``palette`` / this compiler are authoritative for corridor appearance —
    colours are emitted correctly here from the shared ``palette`` table, with
    no downstream color-normalization pass.
    """
    colors = palette.corridor_colors(corridor_id)
    if colors is None:
        return _titled("#6B7280", dashed=shelf)
    fill, stroke = colors
    return (
        f"{_BASE_CONTAINER.replace('fillColor=none;', f'fillColor={fill};')}"
        f"strokeColor={stroke};strokeWidth={2 if shelf else 1};"
        f"{'dashed=1;' if shelf else 'dashed=0;'}"
        f"align=left;verticalAlign=top;spacingLeft=12;spacingTop=8;"
        f"fontStyle=1;fontSize=12;fontColor={stroke};"
    )


def corridor_style(corridor_id: str = "") -> str:
    """Main-row domain/zone/phase container style."""
    return _corridor_container(corridor_id, shelf=False)


def shelf_style(corridor_id: str = "") -> str:
    """Anchored-shelf container style (SaaS/consume/control shelves).

    Heavier dashed border than a main-row corridor, so "sits outside the main
    journey" reads without relying on position alone.
    """
    return _corridor_container(corridor_id, shelf=True)


def cloud_boundary_style(platform: str = "aws") -> str:
    """Single dashed cloud group for flat Logical (sparse process-storyboard look).

    Not a domain column — one optional boundary around cloud-shaped leaves.
    """
    return _titled(_palette(platform)["cloud"], dashed=True, width=2, bold=True)


def leaf_style(criticality: str = "standard") -> str:
    """Grey placeholder awaiting MCP icon resolution.

    ``verticalLabelPosition=bottom`` is what puts the label in the band below
    the icon, which geometry_spec.LABEL_BAND_H accounts for when sizing
    parents. A ``critical`` leaf gets a heavier, coloured border — the one
    mark this app's "clean canvas" visual system adds directly on a leaf,
    since a business-critical/single-point-of-failure component is exactly
    the kind of fact a reviewer should see without opening the panel.
    """
    if criticality == "critical":
        border = f"strokeWidth=3;strokeColor={palette.CRITICAL_LEAF_STROKE};"
    else:
        border = "strokeWidth=1;strokeColor=#B3B3B3;"
    return (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;{border}"
        "verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
        "fontSize=11;fontColor=#333333;"
    )


def actor_style(kind: str = "users") -> str:
    """External-actor style, by ``ActorKind``.

    Only ``users`` is an actual person — the AWS person stencil is reserved
    for that case. ``saas_ingress`` (something that hands you a token or
    identity — Okta, an upstream IdP), ``saas_egress`` (something the
    architecture calls out to — a downstream SaaS integration) and
    ``on_prem`` (a legacy/on-prem system) are three different kinds of
    non-human system and get three distinct shapes, so they no longer render
    identically.
    """
    if kind == "users":
        return (
            "shape=mxgraph.aws4.user;sketch=0;outlineConnect=0;fontColor=#232F3E;"
            "strokeColor=none;fillColor=#232F3E;html=1;verticalLabelPosition=bottom;"
            "verticalAlign=top;align=center;fontSize=11;"
        )
    if kind == "on_prem":
        return (
            "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#666666;"
            "dashed=1;verticalLabelPosition=bottom;verticalAlign=top;align=center;"
            "fontSize=11;fontColor=#333333;"
        )
    if kind == "saas_ingress":
        return (
            "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;"
            "fillColor=#FFF2CC;strokeColor=#D6B656;verticalLabelPosition=bottom;"
            "verticalAlign=top;align=center;fontSize=11;fontColor=#333333;"
        )
    # saas_egress and any future kind: the original generic ellipse.
    return (
        "ellipse;whiteSpace=wrap;html=1;fillColor=#FFF2CC;strokeColor=#D6B656;"
        "verticalLabelPosition=bottom;verticalAlign=top;align=center;fontSize=11;fontColor=#333333;"
    )


# ── ArchiMate ──────────────────────────────────────────────────────────
#
# Unlike every other view's leaf, an ArchiMate element's style is emitted
# *complete* here rather than as a grey placeholder awaiting MCP resolution.
# That is not a break with the LLM/MCP ownership split — it is the same choice
# ``actor_style()`` above already makes for the AWS person stencil. The
# reasoning: an ArchiMate glyph is notation, not a vendor product icon. The
# vocabulary is closed (25 types), draw.io ships the official stencil, and
# there is exactly one correct answer per type — nothing for a fuzzy shape
# search to add, and a miss would silently degrade a *standards-compliant*
# diagram into a wrong one. The element still carries its ``shape_type``, and
# ``drawio_mcp_client._ARCHIMATE_BUILTIN_STYLES`` resolves it to the identical
# string, so the MCP pass is a confirmed no-op instead of a risk.
#
# Style strings verified against draw.io's own ``Sidebar-ArchiMate3.js`` and
# ``shapes/mxArchiMate3.js``: the stencil is ``mxgraph.archimate3.application``
# parameterised by ``appType`` (the corner glyph) and ``archiType``
# (square | rounded corners).

_ARCHIMATE_ELEMENT_BASE = (
    "html=1;outlineConnect=0;whiteSpace=wrap;shape=mxgraph.archimate3.application;"
    "align=center;verticalAlign=top;labelPosition=center;verticalLabelPosition=middle;"
    "fontSize=11;fontColor=#1A1A1A;spacingTop=6;spacingLeft=6;spacingRight=20;"
)


def archimate_element_style(glyph: str, shape: str, layer: str) -> str:
    """One ArchiMate element box.

    ``spacingRight`` leaves the top-right corner clear for the notation glyph
    so a long name never runs underneath it — the one presentation detail the
    draw.io stencil does not handle itself.
    """
    fill, stroke = palette.archimate_layer_colors(layer)
    archi_type = "rounded" if shape == "rounded" else "square"
    return (
        f"{_ARCHIMATE_ELEMENT_BASE}appType={glyph};archiType={archi_type};"
        f"fillColor={fill};strokeColor={stroke};"
    )


def archimate_band_style(layer: str) -> str:
    """The layer band that the layered viewpoint stacks elements into.

    A plain titled rectangle, not the ArchiMate Grouping stencil: a band is
    this app's own reading aid ("everything here is the business layer"), not
    an ArchiMate Grouping element, and drawing it as one would assert a model
    element that does not exist.
    """
    fill, stroke = palette.archimate_band_colors(layer)
    return (
        "rounded=0;whiteSpace=wrap;html=1;container=1;dropTarget=0;collapsible=0;"
        f"recursiveResize=0;fillColor={fill};strokeColor={stroke};strokeWidth=1;"
        "align=left;verticalAlign=top;spacingLeft=14;spacingTop=8;"
        f"fontStyle=1;fontSize=13;fontColor={stroke};"
    )


def archimate_grouping_style() -> str:
    """A domain grouping nested inside a band — ArchiMate's dashed, unfilled
    Grouping convention, kept unfilled so band > grouping > element does not
    stack three washes of the same hue."""
    return (
        "rounded=0;whiteSpace=wrap;html=1;container=1;dropTarget=0;collapsible=0;"
        f"recursiveResize=0;fillColor=none;strokeColor={palette.ARCHIMATE_GROUPING_STROKE};"
        "strokeWidth=1;dashed=1;dashPattern=6 4;align=left;verticalAlign=top;"
        f"spacingLeft=12;spacingTop=6;fontSize=12;fontColor={palette.ARCHIMATE_GROUPING_STROKE};"
    )


#: Line notation per relationship type, verbatim from draw.io's ArchiMate 3
#: shape library. This is the part a reader who knows ArchiMate reads *instead*
#: of the label, so it has to be exactly right: a filled diamond that should be
#: hollow changes composition into aggregation.
_ARCHIMATE_RELATION_STYLES: dict[str, str] = {
    "composition": "startArrow=diamondThin;startFill=1;startSize=12;endArrow=none;endFill=0;",
    "aggregation": "startArrow=diamondThin;startFill=0;startSize=12;endArrow=none;endFill=0;",
    "assignment": "startArrow=oval;startFill=1;startSize=8;endArrow=block;endFill=1;",
    "realization": "endArrow=block;endFill=0;dashed=1;dashPattern=8 4;",
    "serving": "endArrow=open;endFill=1;",
    "access": "endArrow=openThin;endFill=0;dashed=1;dashPattern=1 4;",
    "influence": "endArrow=open;endFill=0;dashed=1;dashPattern=6 4;",
    "triggering": "endArrow=block;endFill=1;",
    "flow": "endArrow=block;endFill=1;dashed=1;dashPattern=6 4;",
    "specialization": "endArrow=block;endFill=0;",
    "association": "endArrow=none;endFill=0;",
}


def archimate_relation_style(rel_type: str, *, labelled: bool = True) -> str:
    """Edge style for one ArchiMate relationship type."""
    notation = _ARCHIMATE_RELATION_STYLES.get(rel_type, _ARCHIMATE_RELATION_STYLES["association"])
    style = (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;jettySize=auto;orthogonalLoop=1;"
        f"strokeWidth=1;strokeColor={palette.ARCHIMATE_RELATION_STROKE};{notation}"
    )
    if labelled:
        style += "labelBackgroundColor=#FFFFFF;fontSize=10;fontColor=#4D4D4D;"
    return style


def flow_style(*, primary: bool, labelled: bool, classification: str = "none", animated: bool = False) -> str:
    """Two independent channels, not one overloaded one: ``primary`` controls
    weight/dash (main path vs secondary/async); ``classification`` controls
    colour (what the flow carries). A flow can be both non-primary and carry
    PII, so neither channel may collapse into the other. ``animated`` mirrors
    the direct-XML prompt's own instruction ("For edges that represent
    primary request/data flows, append flowAnimation=1") for the IR +
    compiler path, which owns edge styling deterministically and never sees
    that prompt — only ``primary`` edges animate, matching that convention."""
    style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=auto;orthogonalLoop=1;"
    color = palette.CLASSIFICATION_COLORS.get(classification, palette.CLASSIFICATION_COLORS["none"])
    if primary:
        style += f"strokeWidth=2;strokeColor={color};"
    else:
        style += f"dashed=1;strokeWidth=1;strokeColor={color};"
    if animated and primary:
        style += "flowAnimation=1;"
    if labelled:
        style += "labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;"
    return style
