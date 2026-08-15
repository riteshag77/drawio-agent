"""Style strings for the sequence-diagram compiler.

Fourth theme module, alongside ``theme.py`` (cloud), ``generic_theme.py``
(as-is image replication) and ``chart_theme.py`` (plotted charts). Same reason
they are all separate: the vocabularies don't overlap. A sequence diagram has
no platform accent, no traced source colour and no series identity — colour
here carries nothing at all, which is exactly the point. Every mark is neutral
ink, because the information is in the *ordering*, and a coloured arrow would
imply a distinction that doesn't exist.

Palette values match ``generic_theme``'s neutral house colours (rather than
importing them) so the two can diverge if the sequence view ever needs to,
without a cross-theme dependency: ``#F5F5F5`` fill, ``#B3B3B3`` stroke,
``#333333`` ink.

Every style here belongs to a *vertex*. A sequence diagram, like a chart,
emits no edges: an arrow is a ``shape=line`` shaft plus a ``triangle`` head,
not an mxCell edge. Edges would be rewritten by
``fix_edge_anchors_from_geometry``, which recomputes endpoints from geometry
and would collapse every message onto its lifeline's face midpoint — on the
one view where a message's vertical position *is* its meaning.
"""

from __future__ import annotations

_FILL = "#F5F5F5"
_STROKE = "#B3B3B3"
_INK = "#333333"
#: Activation blocks sit on top of the dashed lifeline stem and must hide it,
#: so they are opaque white rather than "no fill".
_ACTIVATION_FILL = "#FFFFFF"


def lifeline_style(kind: str, *, header_h: float) -> str:
    """A participant: header box plus the dashed stem below it, as ONE vertex.

    draw.io's ``umlLifeline`` draws both, with ``size=`` marking where the
    header ends — so the stem needs no cell of its own and can never drift out
    of alignment with its header. ``header_h`` must be passed explicitly:
    omitting it leaves draw.io's own default of 40, which silently disagrees
    with the compiler's ``SEQ_HEADER_H`` and offsets every message by the
    difference. ``container=0`` because this compiler places activations in page
    coordinates rather than as children; ``recursiveResize`` and ``collapsible``
    are off for the same reason every other compiled cell turns them off — the
    geometry is final.
    """
    # An actor is drawn as a stick figure above the stem instead of a box, the
    # standard UML distinction between a person and a system. It must be set as
    # `participant=umlActor` — a *modifier* telling umlLifeline which head to
    # draw. Writing `shape=umlActor` instead replaces the lifeline shape
    # outright, which renders a stick figure scaled to the whole 130x340 cell
    # and no stem at all.
    head = "participant=umlActor;" if kind == "actor" else ""
    return (
        f"shape=umlLifeline;perimeter=lifelinePerimeter;{head}"
        "whiteSpace=wrap;html=1;container=0;collapsible=0;recursiveResize=0;"
        f"outlineConnect=0;size={round(header_h)};fillColor={_FILL};"
        f"strokeColor={_STROKE};fontColor={_INK};fontSize=12;"
    )


def activation_style() -> str:
    """The block on a lifeline showing it is busy handling a call."""
    return (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor={_ACTIVATION_FILL};"
        f"strokeColor={_STROKE};strokeWidth=1;"
    )


def shaft_style(*, dashed: bool) -> str:
    """A message's line. ``shape=line`` draws a rule through the middle of its
    box, and unlike a plain rectangle it has a stroke — which is what makes a
    dashed reply arrow possible without faking dashes out of many small cells."""
    dash = "dashed=1;" if dashed else ""
    return f"shape=line;strokeWidth=2;html=1;strokeColor={_INK};{dash}"


def arrowhead_style(*, pointing_right: bool, open_head: bool) -> str:
    """The head of a message arrow.

    ``open_head`` (an unfilled outline) marks an asynchronous message — the
    UML convention for "nothing waits for this", and the one distinction worth
    carrying since it changes how the diagram is read.
    """
    direction = "east" if pointing_right else "west"
    if open_head:
        return f"triangle;direction={direction};html=1;fillColor=none;strokeColor={_INK};"
    return f"triangle;direction={direction};html=1;fillColor={_INK};strokeColor=none;"


def message_label_style(*, align: str = "center") -> str:
    """Message text, sitting just above its own arrow.

    Centred over the shaft for a normal message. A self-message's label sits
    beside its loop instead, where centring inside the label's own box would
    push the text away from the arrow it belongs to.
    """
    return (
        f"text;html=1;align={align};verticalAlign=bottom;whiteSpace=nowrap;"
        f"fontSize=11;fontColor={_INK};labelBackgroundColor=#FFFFFF;"
    )


def title_style() -> str:
    return (
        "text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;"
        "fontSize=16;fontStyle=1;fontColor=#0B0B0B;"
    )
