"""Generic (non-cloud) diagram IR — reproduce an uploaded image's shapes,
lines and layout as-is, instead of forcing it through the cloud IR schema.

Standalone sibling to ``DiagramIR``/``Leaf``/``Flow``, not a subclass: those
carry cloud-only concerns (``Leaf.shape_type``'s closed cloud-service
vocabulary, ``Flow.classification``/``protection``, ``DiagramIR.view``/
``platform``) that mean nothing for a flowchart, org chart, or any other
non-cloud drawing. Reuses only the two primitives worth sharing — ``IRModel``
(``extra="forbid"``) and ``Identified`` (the id contract) — from ``base.py``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.domain.ir.base import Identified, IRModel

GenericNodeShape = Literal[
    "rectangle", "rounded_rectangle", "ellipse", "diamond",
    "parallelogram", "hexagon", "cylinder", "cloud", "actor", "text",
]

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _validate_hex_color(value: str | None) -> str | None:
    if value is None:
        return None
    if not _HEX_COLOR_RE.match(value):
        raise ValueError(f"color {value!r} must be a 6-digit hex string, e.g. '#3B82F6'")
    return value.upper()


class GenericGroup(Identified):
    """A visibly-drawn boundary/swimlane/cluster box — never invented."""

    label: str = ""
    boundary: Literal["none", "dashed", "solid"] = "dashed"
    x: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description=(
            "Coarse left-to-right position hint for this boundary box itself, as a "
            "fraction of the source image's width (0.0 = leftmost, 1.0 = rightmost). "
            "Leave blank when unsure; boxes without a hint are laid out left to right "
            "in declaration order."
        ),
    )
    y: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description=(
            "Coarse top-to-bottom position hint for this boundary box itself, as a "
            "fraction of the source image's height (0.0 = topmost, 1.0 = bottommost). "
            "Leave blank when unsure."
        ),
    )


class GenericNode(Identified):
    """A single shape traced from the source image."""

    shape: GenericNodeShape
    label: str = ""
    group: str = Field(
        default="",
        description="Optional GenericGroup.id this node belongs to; '' means ungrouped.",
    )
    x: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description=(
            "Coarse left-to-right position hint as a fraction of the source image's "
            "width (0.0 = leftmost, 1.0 = rightmost) — not a pixel coordinate. Leave "
            "blank when unsure rather than guessing."
        ),
    )
    y: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description=(
            "Coarse top-to-bottom position hint as a fraction of the source image's "
            "height (0.0 = topmost, 1.0 = bottommost) — not a pixel coordinate. Leave "
            "blank when unsure rather than guessing."
        ),
    )
    size_hint: Literal["small", "normal", "large"] = Field(
        default="normal",
        description=(
            "The shape's size relative to the other shapes in the image — a "
            "comparative judgement, not a measurement. 'normal' unless a shape is "
            "visibly and clearly smaller or larger than its peers."
        ),
    )
    icon_hint: str | None = Field(
        default=None,
        description=(
            "A short free-text description of a recognizable brand/product/cloud-"
            "service icon actually drawn on this shape (e.g. 'aws s3 bucket', "
            "'kubernetes', 'slack'), used to search an icon library. Omit entirely "
            "when the shape has no distinctive icon — never invent a brand that "
            "isn't visibly there."
        ),
    )
    fill_color: str | None = Field(
        default=None,
        description=(
            "The shape's actual fill color from the source image, as a 6-digit hex string "
            "(e.g. '#3B82F6'). Omit if the shape is white/transparent/uncolored in the image — "
            "never invent a color that isn't there."
        ),
    )
    stroke_color: str | None = Field(
        default=None,
        description=(
            "The shape's actual border/outline color from the source image, as a 6-digit hex "
            "string. Omit if not visually distinct from a plain grey/black outline."
        ),
    )

    @field_validator("fill_color", "stroke_color")
    @classmethod
    def _valid_hex(cls, value: str | None) -> str | None:
        return _validate_hex_color(value)


class GenericEdge(IRModel):
    """A directed connector between two node/group ids."""

    source: str
    target: str
    label: str = ""
    line_style: Literal["straight", "orthogonal", "curved"] = "orthogonal"
    dashed: bool = False
    arrow: Literal["none", "forward", "both"] = "forward"
    color: str | None = Field(
        default=None,
        description=(
            "The connector's actual line color from the source image, as a 6-digit hex string. "
            "Omit if it's a plain grey/black line."
        ),
    )

    @field_validator("color")
    @classmethod
    def _valid_hex(cls, value: str | None) -> str | None:
        return _validate_hex_color(value)


class GenericDiagramIR(IRModel):
    """Root document for the as-is image-replication path.

    Duck-types the method surface ``DiagramIR`` exposes (``leaf_ids``/
    ``structural_ids``/``all_ids``/``semantic_errors``/``semantic_warnings``/
    ``is_valid``) without subclassing it — the fields underneath (``nodes``/
    ``edges`` vs. cloud's ``flows``/``external_actors``) differ enough that
    inheriting would only invite confusion about which fields actually apply.
    """

    title: str = "Diagram"
    groups: list[GenericGroup] = Field(default_factory=list)
    nodes: list[GenericNode] = Field(default_factory=list)
    edges: list[GenericEdge] = Field(default_factory=list)

    def leaf_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def structural_ids(self) -> set[str]:
        return {g.id for g in self.groups}

    def all_ids(self) -> set[str]:
        return self.leaf_ids() | self.structural_ids()

    def semantic_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.nodes:
            errors.append("nodes is empty — a diagram needs at least one shape")
        group_ids = self.structural_ids()
        seen: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                errors.append(f"duplicate cell id {node.id!r}")
            seen.add(node.id)
            if node.group and node.group not in group_ids:
                errors.append(f"node {node.id!r} references unknown group {node.group!r}")
        for group in self.groups:
            if group.id in seen:
                errors.append(f"duplicate cell id {group.id!r} reused across nodes/groups")
            seen.add(group.id)
        known = self.all_ids()
        for edge in self.edges:
            if edge.source not in known:
                errors.append(f"edge source {edge.source!r} is not a known cell id")
            if edge.target not in known:
                errors.append(f"edge target {edge.target!r} is not a known cell id")
            # Deliberately NO self-loop rule here, unlike every other view.
            # DiagramIR._base_flow_errors rejects source == target because on
            # a cloud diagram a service calling itself conveys nothing. On an
            # as-is replication of a flowchart or state machine a self-
            # transition ("retry", "repeat until valid") is ordinary content
            # that is literally drawn in the source image. It used to be a
            # hard error the prompt never mentioned, which then got silently
            # deleted by agent._drop_invalid_generic_edges — the diagram came
            # back missing an arrow the user could see in their own picture.
        return errors

    def semantic_warnings(self) -> list[str]:
        return []

    def is_valid(self) -> bool:
        return not self.semantic_errors()
