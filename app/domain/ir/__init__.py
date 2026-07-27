"""Diagram intermediate representation (IR).

The IR is the contract between the LLM and the renderer. The model emits *this*
— a small, closed, self-validating structure — instead of raw draw.io XML, and a
deterministic compiler turns it into mxCells.

Why this layer exists: draw.io XML has a huge surface (ids, parents, absolute vs
relative geometry, style micro-syntax) and every one of those is a way for a
generated document to be subtly wrong. The IR removes all of them from the
model's job. Geometry, styles, parent wiring and spacing are the compiler's
responsibility; the model only decides *what* exists and *how it connects*.

Pure domain: no I/O, no draw.io vocabulary, no rendering concerns.
"""

from app.domain.ir.base import DiagramIR, Flow, Leaf, ViewKind
from app.domain.ir.network import (
    AvailabilityZone,
    CloudBoundary,
    CrossCuttingGroup,
    EdgeLayer,
    ExternalActor,
    GroupKind,
    NetworkIR,
    Region,
    Subnet,
    SubnetTier,
    Vpc,
)

__all__ = [
    "AvailabilityZone",
    "CloudBoundary",
    "CrossCuttingGroup",
    "DiagramIR",
    "EdgeLayer",
    "ExternalActor",
    "Flow",
    "GroupKind",
    "Leaf",
    "NetworkIR",
    "Region",
    "Subnet",
    "SubnetTier",
    "ViewKind",
    "Vpc",
]
