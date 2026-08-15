"""Style strings for the sequence-diagram compiler.

Fourth theme module, alongside ``theme.py`` (cloud), ``generic_theme.py``
(as-is image replication) and ``chart_theme.py`` (plotted charts). Same reason
they are all separate: the vocabularies don't overlap.

Colour is used sparingly and structurally, not decoratively. Arrows and their
labels stay ink, because the information in a message is its *position* and a
coloured arrow would imply a distinction that doesn't exist. What does earn
colour is the frame: participant headers (so the columns read as headers at a
glance), activation blocks, and the phase bands that group a long conversation
into stages.

The tints are the Material 50/100 family with 700-weight borders, matching the
reference sequence diagram this view was measured against — the same
``#E3F2FD`` / ``#FFF3E0`` / ``#E8F5E9`` / ``#F3E5F5`` / ``#FFEBEE`` progression
its phase ``rect`` blocks use.

Every style here belongs to a *vertex*. A sequence diagram, like a chart,
emits no edges: an arrow is a ``shape=line`` shaft plus a ``triangle`` head,
not an mxCell edge. Edges would be rewritten by
``fix_edge_anchors_from_geometry``, which recomputes endpoints from geometry
and would collapse every message onto its lifeline's face midpoint — on the
one view where a message's vertical position *is* its meaning.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.compiler import palette

_FILL = "#F5F5F5"
_STROKE = "#B3B3B3"
_INK = "#333333"

#: Participant header: saturated fill with white text, so the columns read as
#: headers rather than as another box in the drawing.
_HEADER_FILL = palette.series_color(0)
_HEADER_STROKE = palette.shade(palette.series_color(0), 0.3)
_HEADER_TEXT = "#FFFFFF"

#: Phase bands cycle the shared palette, tinted well toward white: a band
#: sits *behind* every arrow and label in its stage, so anything stronger
#: would fight the content it exists to group.

#: Activation blocks sit on top of the dashed lifeline stem and must hide it,
#: so they are opaque rather than "no fill" — and tinted, so a busy participant
#: is visible at a glance against the band behind it.
_ACTIVATION_FILL = palette.tint(palette.series_color(0), 0.72)
_ACTIVATION_STROKE = palette.series_color(0)


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
    is_actor = kind == "actor"
    head = "participant=umlActor;" if is_actor else ""
    # An actor's name is drawn *below* the stick figure, on the page background
    # rather than inside a filled header box — so the white header text that
    # makes a boxed participant readable renders white-on-white and the label
    # vanishes entirely. Actors take ink instead.
    font = _INK if is_actor else _HEADER_TEXT
    # An actor's box is only SEQ_ACTOR_W wide (the stick figure is scaled to it),
    # so a wrapped label breaks "👤 Claimant" across lines and loses the emoji
    # off the top. It overflows the box either way; nowrap keeps it one line.
    # nowrap keeps "👤 Customer" on one line — a wrapped label breaks across
    # lines in the narrow actor box and loses the emoji off the top. Tried
    # verticalLabelPosition=bottom to also clear the stick figure: it puts the
    # label below the *whole lifeline* (400px down, off the diagram) rather
    # than below the glyph, so the slight overlap is the better of the two.
    wrap = "whiteSpace=nowrap;" if is_actor else "whiteSpace=wrap;"
    return (
        f"shape=umlLifeline;perimeter=lifelinePerimeter;{head}{wrap}"
        "html=1;container=0;collapsible=0;recursiveResize=0;"
        f"outlineConnect=0;size={round(header_h)};fillColor={_HEADER_FILL};"
        f"strokeColor={_HEADER_STROKE};fontColor={font};fontSize=12;"
        "fontStyle=1;"
    )


def activation_style() -> str:
    """The block on a lifeline showing it is busy handling a call."""
    return (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor={_ACTIVATION_FILL};"
        f"strokeColor={_ACTIVATION_STROKE};strokeWidth=1;"
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


def band_style(index: int) -> str:
    """The shaded box behind one phase of the conversation.

    Emitted before every arrow in its range so it stays behind them, and given
    a title in the top-left rather than centred — a centred label would sit in
    the middle of the messages it groups.
    """
    fill, stroke = palette.fill_and_border(index % palette.PALETTE_SLOTS, band=True)
    return (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
        "strokeWidth=1;dashed=1;verticalAlign=top;align=left;spacingLeft=10;"
        f"spacingTop=4;fontSize=11;fontStyle=1;fontColor={stroke};"
    )


def sequence_number_style() -> str:
    """The autonumber badge on a message — a small circle at the arrow's tail,
    matching the reference diagram's ``autonumber``."""
    return (
        f"ellipse;html=1;fillColor={_HEADER_FILL};strokeColor=none;"
        "fontColor=#FFFFFF;fontSize=9;fontStyle=1;whiteSpace=wrap;"
    )
