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


def flow_style(*, primary: bool, labelled: bool, classification: str = "none") -> str:
    """Two independent channels, not one overloaded one: ``primary`` controls
    weight/dash (main path vs secondary/async); ``classification`` controls
    colour (what the flow carries). A flow can be both non-primary and carry
    PII, so neither channel may collapse into the other."""
    style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=auto;orthogonalLoop=1;"
    color = palette.CLASSIFICATION_COLORS.get(classification, palette.CLASSIFICATION_COLORS["none"])
    if primary:
        style += f"strokeWidth=2;strokeColor={color};"
    else:
        style += f"dashed=1;strokeWidth=1;strokeColor={color};"
    if labelled:
        style += "labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;"
    return style
