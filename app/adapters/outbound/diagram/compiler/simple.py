"""Compile a ``SimpleDiagramIR`` — the single entry point for the non-cloud path.

Dispatches on ``kind``:

- ``"flow"`` reuses ``compile_generic_ir``. A flowchart, org chart, state
  machine, ER diagram and mindmap are all one shape underneath — labelled boxes
  joined by arrows — and that compiler already draws exactly that, including a
  layered layout read off the edge graph (``generic._place_topological``) for
  nodes that carry no coordinates. Nothing about it is specific to tracing an
  uploaded image once the position hints are absent, so a second near-identical
  compiler would be pure duplication.
- ``"bar"``/``"pie"``/``"gantt"`` go to ``compile_chart_ir``, which plots rather
  than lays out.

The flow conversion drops to ``GenericDiagramIR`` rather than the two IRs
sharing a base class. They describe different *acts* — one reports what was in
a picture, the other designs something new — so their field sets and prompts
diverge on purpose (see ``domain/ir/simple.py``'s docstring). Converting at the
compiler boundary keeps that separation while sharing the rendering.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.compiler.chart import compile_chart_ir
from app.adapters.outbound.diagram.compiler.generic import compile_generic_ir
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericGroup, GenericNode
from app.domain.ir.simple import SimpleDiagramIR


def _to_generic_ir(ir: SimpleDiagramIR) -> GenericDiagramIR:
    """Project a ``kind="flow"`` IR onto the generic compiler's schema.

    Every field the generic path uses to reproduce a source image —
    ``x``/``y``, ``size_hint``, ``fill_color``, ``stroke_color``, ``icon_hint``,
    edge ``color`` — is deliberately left unset. There is no source image here,
    so there is nothing to reproduce: leaving positions blank is what routes
    the nodes through ``_place_topological`` (the layered edge-graph layout)
    instead of the proportional image-tracing placement, and leaving colours
    blank keeps the neutral house palette.
    """
    return GenericDiagramIR(
        title=ir.title or "Diagram",
        groups=[
            GenericGroup(id=g.id, label=g.label, boundary=g.boundary) for g in ir.groups
        ],
        nodes=[
            GenericNode(id=n.id, shape=n.shape, label=n.label, group=n.group) for n in ir.nodes
        ],
        edges=[
            GenericEdge(
                source=e.source, target=e.target, label=e.label,
                line_style=e.line_style, dashed=e.dashed, arrow=e.arrow,
            )
            for e in ir.edges
        ],
    )


def compile_simple_ir(ir: SimpleDiagramIR, *, page_name: str = "Diagram") -> str:
    """Render ``ir`` to a complete draw.io document."""
    if ir.is_chart():
        return compile_chart_ir(ir, page_name=page_name)
    return compile_generic_ir(_to_generic_ir(ir), page_name=page_name)
