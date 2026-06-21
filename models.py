from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class Node(BaseModel):
    id: str = Field(description="A unique alphanumeric ID (e.g., 'vpc1', 'ec2_a')")
    label: str = Field(description="The human-readable name of the component")
    shape_type: str = Field(description="Standardized shape key from the shape library (e.g., 'aws_vpc', 'aws_ec2', 'aws_lambda')")
    is_container: bool = Field(default=False, description="True if this shape is a boundary (like a VPC, Subnet, AZ, or logical group) that holds other resources.")
    parent_id: Optional[str] = Field(default=None, description="The ID of the container this shape sits inside. Null if it sits on the main canvas.")
    step_number: Optional[int] = Field(default=None, description="The numbered step in the traffic flow sequence (1, 2, 3, ...). Only set for nodes that represent a numbered step in the architecture flow.")
    width: Optional[int] = Field(default=None, description="Optional width hint in pixels. Leaf nodes default to 48, containers auto-size.")
    height: Optional[int] = Field(default=None, description="Optional height hint in pixels. Leaf nodes default to 48, containers auto-size.")
    x: Optional[int] = Field(default=None, description="Optional horizontal position hint. The layout engine uses this as a preferred X coordinate.")
    y: Optional[int] = Field(default=None, description="Optional vertical position hint. The layout engine uses this as a preferred Y coordinate.")
    description: Optional[str] = Field(default=None, description="Optional tooltip or annotation text displayed on hover.")


class Edge(BaseModel):
    source_id: str = Field(description="The ID of the origin node")
    target_id: str = Field(description="The ID of the destination node")
    label: Optional[str] = Field(default="", description="Optional text on the connecting line")
    style: Optional[str] = Field(default="solid", description="Edge line style: 'solid', 'dashed', or 'bold'")
    color: Optional[str] = Field(default="#ED7100", description="Edge color as hex string, default orange for AWS")


class ArchitectureDiagram(BaseModel):
    nodes: List[Node] = Field(description="List of all infrastructure components and containers")
    edges: List[Edge] = Field(description="List of all connections between components")
    suggestions: List[str] = Field(description="Missing best-practice components added by architect")


class DiagramOperation(BaseModel):
    operation: Literal["update", "add", "delete"] = Field(
        description="Operation to perform on a diagram cell"
    )
    cell_id: str = Field(description="The id of the mxCell (must match new_xml id for add/update)")
    new_xml: Optional[str] = Field(
        default=None,
        description="Complete mxCell XML element including mxGeometry (required for add/update)",
    )


class EditResponse(BaseModel):
    mode: Literal["edit", "replace"] = Field(
        default="edit",
        description="edit = incremental operations; replace = full mxCell fragment replacement",
    )
    operations: List[DiagramOperation] = Field(
        default_factory=list,
        description="ID-based diagram operations (when mode=edit)",
    )
    xml: Optional[str] = Field(
        default=None,
        description="Bare mxCell XML fragment for full replace (when mode=replace)",
    )
    assistant_message: str = Field(
        default="Diagram updated.",
        description="Brief summary for the chat UI",
    )
    suggestions: List[str] = Field(default_factory=list, description="Optional improvement suggestions")
