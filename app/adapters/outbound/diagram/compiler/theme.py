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


def corridor_style() -> str:
    """Generic domain/zone/phase container style.

    Fill/stroke/font colour are irrelevant here: ``placement_fixers.
    normalize_corridor_colors`` unconditionally overwrites those three keys
    for every ``domain_``/``zone_``/``phase_`` container to its view's
    canonical Section-colors value regardless of what the compiler emits —
    confirmed in that function's own docstring ("Authoritative: overwrites
    existing fill/stroke/font color keys rather than filling gaps"). Only the
    non-colour parts of this style (``arcSize``, ``container=1``) are ours to
    get right.
    """
    return _titled("#6B7280", dashed=False)


def shelf_style() -> str:
    """Anchored-shelf container style (SaaS/consume/control shelves).

    Same colour-is-discarded rule as ``corridor_style`` — dashed is the only
    part that survives normalization, used here to visually distinguish a
    shelf from a main-row corridor before that overwrite runs.
    """
    return _titled("#6B7280", dashed=True)


def leaf_style() -> str:
    """Grey placeholder awaiting MCP icon resolution.

    ``verticalLabelPosition=bottom`` is what puts the label in the band below
    the icon, which geometry_spec.LABEL_BAND_H accounts for when sizing parents.
    """
    return (
        "rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#B3B3B3;"
        "verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
        "fontSize=11;fontColor=#333333;"
    )


def actor_style() -> str:
    return (
        "shape=mxgraph.aws4.user;sketch=0;outlineConnect=0;fontColor=#232F3E;"
        "strokeColor=none;fillColor=#232F3E;html=1;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;fontSize=11;"
    )


def flow_style(*, primary: bool, labelled: bool) -> str:
    style = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=auto;orthogonalLoop=1;"
    if primary:
        style += "strokeWidth=2;strokeColor=#232F3E;"
    else:
        style += "dashed=1;strokeWidth=1;strokeColor=#6B7280;"
    if labelled:
        style += "labelBackgroundColor=#F5F5F5;fontSize=11;fontColor=#333333;"
    return style
