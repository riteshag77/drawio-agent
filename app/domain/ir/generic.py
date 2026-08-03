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

from typing import Literal

from pydantic import Field

from app.domain.ir.base import Identified, IRModel

GenericNodeShape = Literal[
    "rectangle", "rounded_rectangle", "ellipse", "diamond",
    "parallelogram", "hexagon", "cylinder", "cloud", "actor", "text",
]


class GenericGroup(Identified):
    """A visibly-drawn boundary/swimlane/cluster box — never invented."""

    label: str = ""
    boundary: Literal["none", "dashed", "solid"] = "dashed"


class GenericNode(Identified):
    """A single shape traced from the source image."""

    shape: GenericNodeShape
    label: str = ""
    group: str = Field(
        default="",
        description="Optional GenericGroup.id this node belongs to; '' means ungrouped.",
    )
    row: int | None = Field(
        default=None, ge=0,
        description="Coarse top-to-bottom reading-order hint (grid ordinal, not a pixel coordinate).",
    )
    col: int | None = Field(
        default=None, ge=0,
        description="Coarse left-to-right reading-order hint (grid ordinal, not a pixel coordinate).",
    )


class GenericEdge(IRModel):
    """A directed connector between two node/group ids."""

    source: str
    target: str
    label: str = ""
    line_style: Literal["straight", "orthogonal", "curved"] = "orthogonal"
    dashed: bool = False
    arrow: Literal["none", "forward", "both"] = "forward"


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
            if edge.source == edge.target:
                errors.append(f"edge {edge.source!r} -> itself is not meaningful")
        return errors

    def semantic_warnings(self) -> list[str]:
        return []

    def is_valid(self) -> bool:
        return not self.semantic_errors()
