"""IR -> draw.io XML compilation.

An outbound adapter: it is the only place that knows draw.io's mxCell/mxGeometry
vocabulary on the *write* side. The IR it consumes is pure domain.
"""

from app.adapters.outbound.diagram.compiler.archimate import compile_archimate_ir
from app.adapters.outbound.diagram.compiler.corridor import compile_corridor_ir
from app.adapters.outbound.diagram.compiler.generic import compile_generic_ir
from app.adapters.outbound.diagram.compiler.network import compile_network_ir
from app.adapters.outbound.diagram.compiler.simple import compile_simple_ir

__all__ = [
    "compile_archimate_ir",
    "compile_corridor_ir",
    "compile_generic_ir",
    "compile_network_ir",
    "compile_simple_ir",
]
