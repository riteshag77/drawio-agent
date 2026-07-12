"""Architecture domain models: nodes, edges, flow steps, and the diagram graph."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Node(BaseModel):
    id: str = Field(description="A unique alphanumeric ID (e.g., 'vpc1', 'ec2_a')")
    label: str = Field(description="The human-readable name of the component")
    shape_type: str = Field(description="Standardized shape key from the shape library (e.g., 'aws_vpc', 'aws_ec2', 'aws_lambda')")
    is_container: bool = Field(default=False, description="True if this shape is a boundary (like a VPC, Subnet, AZ, or logical group) that holds other resources.")
    parent_id: Optional[str] = Field(default=None, description="The ID of the container this shape sits inside. Null if it sits on the main canvas.")
    step_number: Optional[int] = Field(default=None, description="The numbered step in the traffic flow sequence (1, 2, 3, ...). Only set for nodes that represent a numbered step in the architecture flow.")
    width: Optional[int] = Field(default=None, description="Explicit width in px. If provided by LLM, overrides auto-layout sizing.")
    height: Optional[int] = Field(default=None, description="Explicit height in px. If provided by LLM, overrides auto-layout sizing.")
    x: Optional[int] = Field(default=None, description="Explicit x-position in px. If provided by LLM, overrides auto-layout placement.")
    y: Optional[int] = Field(default=None, description="Explicit y-position in px. If provided by LLM, overrides auto-layout placement.")
    layer: Optional[str] = Field(default="default", description="Z-order layer: 'background' (rendered first, sits behind), 'default', or 'foreground' (rendered last, sits on top).")
    description: Optional[str] = Field(default=None, description="Optional tooltip or annotation text displayed on hover.")


class Edge(BaseModel):
    source_id: str = Field(description="The ID of the origin node")
    target_id: str = Field(description="The ID of the destination node")
    label: Optional[str] = Field(default="", description="Optional text on the connecting line")
    style: Optional[str] = Field(default="solid", description="Edge line style: 'solid', 'dashed', or 'bold'")
    color: Optional[str] = Field(default="#ED7100", description="Edge color as hex string, default orange for AWS")


class FlowStep(BaseModel):
    number: int = Field(description="The sequence number of this step, matching a node's step_number (1, 2, 3, ...)")
    title: str = Field(description="Short title for this step (e.g. 'Edge security', '2-4 words')")
    summary: str = Field(description="One concise sentence explaining what happens at this step in the flow")


class ArchitectureDiagram(BaseModel):
    nodes: List[Node] = Field(description="List of all infrastructure components and containers")
    edges: List[Edge] = Field(description="List of all connections between components")
    suggestions: List[str] = Field(description="Missing best-practice components added by architect")
    flow_steps: List[FlowStep] = Field(
        default_factory=list,
        description=(
            "Ordered, point-wise explanation of the numbered traffic flow. One entry per "
            "step_number used in the nodes, in ascending order. Rendered as a Notes section."
        ),
    )
    pattern_id: Optional[str] = Field(
        default=None,
        description="Identifier of the reference pattern this diagram is based on, if any.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Short human-readable title for the architecture (used as the diagram heading).",
    )
