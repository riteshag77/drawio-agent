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

from app.domain.ir.base import ActorKind, DiagramIR, ExternalActor, Flow, Leaf, ViewKind
from app.domain.ir.corridor import AnchoredCorridor, Corridor, CorridorIR
from app.domain.ir.data_flow import DataFlowIR
from app.domain.ir.logical import LogicalIR
from app.domain.ir.network import (
    AvailabilityZone,
    CloudBoundary,
    CrossCuttingGroup,
    EdgeLayer,
    GroupKind,
    NetworkIR,
    Region,
    Subnet,
    SubnetTier,
    Vpc,
)
from app.domain.ir.security import SecurityIR

__all__ = [
    "ActorKind",
    "AnchoredCorridor",
    "AvailabilityZone",
    "CloudBoundary",
    "Corridor",
    "CorridorIR",
    "CrossCuttingGroup",
    "DataFlowIR",
    "DiagramIR",
    "EdgeLayer",
    "ExternalActor",
    "Flow",
    "GroupKind",
    "Leaf",
    "LogicalIR",
    "NetworkIR",
    "Region",
    "SecurityIR",
    "Subnet",
    "SubnetTier",
    "ViewKind",
    "Vpc",
]

#: Maps a primary-view key to its IR type — the single dispatch table every
#: generic (view-agnostic) call site should read from, instead of an if/elif
#: chain repeated at each call site.
IR_TYPES_BY_VIEW: dict[str, type[DiagramIR]] = {
    "network": NetworkIR,
    "logical": LogicalIR,
    "security": SecurityIR,
    "data_flow": DataFlowIR,
}
