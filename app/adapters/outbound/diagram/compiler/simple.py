"""Compile a ``SimpleDiagramIR`` — the single entry point for the non-cloud path.

Dispatches on ``kind``:

- ``"flow"`` reuses ``compile_generic_ir``. A flowchart, org chart, state
  machine, ER diagram and mindmap are all one shape underneath — labelled boxes
  joined by arrows — and that compiler already draws exactly that, including a
  layered layout read off the edge graph (``generic._place_topological``) for
  nodes that carry no coordinates. Nothing about it is specific to tracing an
  uploaded image once the position hints are absent, so a second near-identical
  compiler would be pure duplication.
- ``"sequence"`` goes to ``compile_sequence_ir``. It is neither a node graph
  nor a plot: its vertical axis is time, so message order is meaning and no
  layout engine may reorder it.
- ``"bar"``/``"line"``/``"pie"``/``"gantt"`` go to ``compile_chart_ir``, which
  plots rather than lays out.

The flow conversion drops to ``GenericDiagramIR`` rather than the two IRs
sharing a base class. They describe different *acts* — one reports what was in
a picture, the other designs something new — so their field sets and prompts
diverge on purpose (see ``domain/ir/simple.py``'s docstring). Converting at the
compiler boundary keeps that separation while sharing the rendering.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.compiler.chart import compile_chart_ir
from app.adapters.outbound.diagram.compiler import palette
from app.adapters.outbound.diagram.compiler.generic import compile_generic_ir
from app.adapters.outbound.diagram.compiler.sequence import compile_sequence_ir
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericGroup, GenericNode
from app.domain.ir.simple import SimpleDiagramIR


#: Which palette slot each shape role takes. Roles, not identities: two
#: "steps" always look alike and a diamond always reads as a choice, so colour
#: never has to be decoded. Slots come from the shared ``palette`` so a
#: flowchart, a chart and a sequence diagram are visibly one product.
#:
#: The colours are supplied *through the IR* rather than added to
#: ``generic_theme``, which is shared with the image-replication path — its
#: neutral grey is deliberate there, because a colour on that path must mean
#: "this is the colour the user drew". ``generic_theme.node_style`` already
#: honours ``fill_color``/``stroke_color`` when present and picks a contrasting
#: font, so the simple path gets a palette by *filling those fields in* and the
#: traced path is untouched.
_SHAPE_SLOT: dict[str, int] = {
    "rectangle": 0,            # a step — blue, the most common shape
    "rounded_rectangle": 5,    # start/end terminal — green
    "diamond": 3,              # decision — yellow
    "cylinder": 4,             # datastore — magenta
    "parallelogram": 2,        # input/output — aqua
    "hexagon": 4,              # preparation — magenta
    "actor": 1,                # a person — orange
    "cloud": 2,                # external system — aqua
    "ellipse": 1,              # connector/alternate — orange
    # `text` is deliberately absent: a borderless caption has no box to fill,
    # and generic_theme would turn the fill into the font colour instead.
}


def _shape_colors(shape: str) -> dict[str, str]:
    slot = _SHAPE_SLOT.get(shape)
    if slot is None:
        return {}
    fill, border = palette.fill_and_border(slot)
    return {"fill_color": fill, "stroke_color": border}


def _to_generic_ir(ir: SimpleDiagramIR) -> GenericDiagramIR:
    """Project a ``kind="flow"`` IR onto the generic compiler's schema.

    Positions (``x``/``y``), ``size_hint`` and ``icon_hint`` are deliberately
    left unset: there is no source image here, and leaving positions blank is
    what routes the nodes through ``_place_topological`` (the layered
    edge-graph layout) instead of the proportional image-tracing placement.

    Colours are the exception, and they are set rather than left blank. On the
    image path a blank colour means "the shape was uncoloured in the picture",
    so the neutral grey is correct there. Here nothing was traced, and an
    all-grey flowchart is just drab — so the palette is applied by role (see
    ``_SHAPE_PALETTE``), reusing the same fields the traced path fills in from
    what it saw.
    """
    return GenericDiagramIR(
        title=ir.title or "Diagram",
        groups=[
            GenericGroup(id=g.id, label=g.label, boundary=g.boundary) for g in ir.groups
        ],
        nodes=[
            GenericNode(
                id=n.id, shape=n.shape, label=n.label, group=n.group,
                **_shape_colors(n.shape),
            )
            for n in ir.nodes
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
    if ir.is_sequence():
        return compile_sequence_ir(ir, page_name=page_name)
    return compile_generic_ir(_to_generic_ir(ir), page_name=page_name)
