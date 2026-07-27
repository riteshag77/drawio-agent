"""IR -> draw.io XML compilation.

An outbound adapter: it is the only place that knows draw.io's mxCell/mxGeometry
vocabulary on the *write* side. The IR it consumes is pure domain.
"""

from app.adapters.outbound.diagram.compiler.network import compile_network_ir

__all__ = ["compile_network_ir"]
