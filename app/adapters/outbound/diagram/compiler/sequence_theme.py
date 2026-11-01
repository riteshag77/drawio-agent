"""Style strings for the sequence-diagram compiler.

Fourth theme module, alongside ``theme.py`` (cloud), ``generic_theme.py``
(as-is image replication) and ``chart_theme.py`` (plotted charts). Same reason
they are all separate: the vocabularies don't overlap.

## What wears colour, and why

Colour is structural, never decorative, and it is spent in exactly three
places:

- **Participant headers**, by *role* — a person is one hue, a system another.
  Not one hue per column: ten columns in ten colours is a legend nobody asked
  for, and the reader would have to decode it before reading a single message.
  The two role hues are the same slots ``compiler/simple.py`` gives an ``actor``
  and a ``rectangle`` in a flowchart, so a person looks like a person across
  every view this app draws.
- **Phase bands**, cycling the palette, so consecutive stages of a long
  conversation are told apart at a glance.
- **Activation blocks**, in the system hue, marking who is busy.

Everything else — shafts, heads, message labels — is ink. A message's meaning
is *who* and *when*, and a coloured arrow would imply a distinction that isn't
there. The ink is a warm slate rather than black: at ``strokeWidth=2`` across a
tinted band, pure black reads as heavier than the participants it connects.

Replies take a lighter ink than calls. Both are already distinguishable by the
dash pattern, but on a 24-message diagram the call/return rhythm is the thing
the eye is trying to follow, and weight carries it better than dashes alone.

## Headers are cards, not boxes with a stick figure

Actors used to be drawn as draw.io's ``umlActor`` stick figure. Three things
were wrong with it, all visible in a render: the figure is scaled to its cell,
so the actor's column had to be a third the width of every other one, leaving a
gap in the header row where a person should be; the name is drawn *below* the
figure on the page background, so it overlapped the glyph and could not take
the header's own font colour; and it left no room for the emoji the prompt pack
asks the model to put at the front of every label — the icon that makes a
diagram scannable ended up as a 12px speck jammed against a stick figure's arm.

So every participant now gets the same card: a tinted fill with a full-strength
border of the same hue, the emoji lifted out of the label and set at
``SEQ_ICON_PT`` on its own line, and the name in bold beneath it. Role lives in
the hue. The UML stick figure is gone deliberately — this view is read by people
describing systems to each other, not by a UML tool.

## Every style here belongs to a vertex

A sequence diagram, like a chart, emits no edges: an arrow is a ``shape=line``
shaft plus a ``triangle`` head. Edges would be rewritten by
``fix_edge_anchors_from_geometry``, which recomputes endpoints from geometry and
would collapse every message onto its lifeline's face midpoint — on the one
view where a message's vertical position *is* its meaning.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import labels, palette

#: Ink. A warm slate, not black — see the module docstring.
_INK = "#3B4652"
#: Replies and other returning traffic, one step back from a call.
_INK_MUTED = "#78848F"
_TEXT = "#1F2933"

#: Role hues, taken as palette *slots* so they match what a flowchart gives the
#: same two things (``simple._SHAPE_SLOT``: actor -> 1, rectangle -> 0).
_ROLE_SLOT = {"actor": 1, "participant": 0}

#: How far an activation block's fill is mixed toward white. Lighter than a
#: header card's ``FILL_TINT`` would be: the block is a solid column the eye
#: tracks down the page, and at header weight a busy participant's bar competed
#: with the arrows landing on it.
_ACTIVATION_TINT = 0.72

#: The autonumber badge. Deliberately not a role hue: the badges are the most
#: repeated mark on the page (one per message), and in a participant's colour
#: two dozen of them would out-shout the headers they'd be echoing.
_NUMBER_FILL = "#5C6B7A"


def _role_color(kind: str) -> str:
    return palette.series_color(_ROLE_SLOT.get(kind, 0))


def header_label(label: str) -> str:
    """The participant header's label: icon over name, always bold.

    A header is a header on every column, so unlike a flow node this one takes
    bold and keeps its full-height box even when the model gave no emoji — a
    header row of uniform height is worth more than the ~20px those diagrams
    would save.
    """
    icon, name = labels.split_icon(label)
    if not icon:
        return f"<b>{labels.escape_html(name) or '&nbsp;'}</b>"
    return labels.icon_over_name(label, icon_pt=geo.SEQ_ICON_PT, bold=True)


def lifeline_style(kind: str, *, header_h: float) -> str:
    """A participant: header card plus the dashed stem below it, as ONE vertex.

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
    hue = _role_color(kind)
    fill, border = palette.fill_and_border(_ROLE_SLOT.get(kind, 0))
    return (
        f"shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;"
        "html=1;container=0;collapsible=0;recursiveResize=0;rounded=1;arcSize=8;"
        f"outlineConnect=0;size={round(header_h)};fillColor={fill};"
        f"strokeColor={border};fontColor={palette.shade(hue, 0.55)};fontSize=12;"
        "verticalAlign=middle;"
    )


def activation_style(kind: str = "participant") -> str:
    """The block on a lifeline showing it is busy handling a call.

    Takes its participant's role hue rather than one fixed colour, so a bar
    belongs visibly to the column it sits on — an actor's activation reading in
    the system blue made a person look like a service for the length of the bar.
    """
    hue = _role_color(kind)
    return (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor={palette.tint(hue, _ACTIVATION_TINT)};"
        f"strokeColor={hue};strokeWidth=1;"
    )


def shaft_style(*, dashed: bool) -> str:
    """A message's line. ``shape=line`` draws a rule through the middle of its
    box, and unlike a plain rectangle it has a stroke — which is what makes a
    dashed reply arrow possible without faking dashes out of many small cells."""
    if dashed:
        return f"shape=line;strokeWidth=2;html=1;strokeColor={_INK_MUTED};dashed=1;"
    return f"shape=line;strokeWidth=2;html=1;strokeColor={_INK};"


def arrowhead_style(*, pointing_right: bool, open_head: bool, muted: bool = False) -> str:
    """The head of a message arrow.

    ``open_head`` (an unfilled outline) marks an asynchronous message — the
    UML convention for "nothing waits for this", and the one distinction worth
    carrying since it changes how the diagram is read. ``muted`` keeps a reply's
    head the same weight as its own shaft; a full-ink head on a muted shaft
    reads as a different arrow at each end.
    """
    direction = "east" if pointing_right else "west"
    ink = _INK_MUTED if muted else _INK
    if open_head:
        return f"triangle;direction={direction};html=1;fillColor=none;strokeColor={ink};"
    return f"triangle;direction={direction};html=1;fillColor={ink};strokeColor=none;"


def message_label_style(*, align: str = "center", muted: bool = False) -> str:
    """Message text, sitting just above its own arrow.

    Centred over the shaft for a normal message. A self-message's label sits
    beside its loop instead, where centring inside the label's own box would
    push the text away from the arrow it belongs to.

    The white label background is what keeps a label legible where it crosses a
    lifeline stem or another column's activation block.
    """
    color = _INK_MUTED if muted else _TEXT
    return (
        f"text;html=1;align={align};verticalAlign=bottom;whiteSpace=nowrap;"
        f"fontSize=11;fontColor={color};labelBackgroundColor=#FFFFFF;"
    )


def title_style() -> str:
    return (
        "text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;"
        f"fontSize=16;fontStyle=1;fontColor={_TEXT};"
    )


def band_style(index: int) -> str:
    """The wash behind one phase of the conversation.

    Emitted before every arrow in its range so it stays behind them, and
    borderless: the rail and chip (below) carry the band's identity, and an
    outline around a full-width rectangle only adds a second edge for the eye to
    trip over between stages.
    """
    fill, _ = palette.fill_and_border(index % palette.PALETTE_SLOTS, band=True)
    return f"rounded=0;html=1;fillColor={fill};strokeColor=none;"


def band_rail_style(index: int) -> str:
    """The full-strength colour rail down a band's left edge."""
    hue = palette.series_color(index % palette.PALETTE_SLOTS)
    return f"rounded=0;html=1;fillColor={hue};strokeColor=none;"


def band_chip_style(index: int) -> str:
    """The band's title, set solid in its own hue rather than as loose text.

    A chip because the title sits *inside* the band it names, above the first
    message: as plain tinted text it competed with the message labels below it
    at the same size and weight, and the eye had no anchor for where a stage
    began.
    """
    hue = palette.series_color(index % palette.PALETTE_SLOTS)
    return (
        f"rounded=1;arcSize=40;whiteSpace=nowrap;html=1;fillColor={hue};"
        "strokeColor=none;fontColor=#FFFFFF;fontSize=11;fontStyle=1;"
        "align=center;verticalAlign=middle;"
    )


def fragment_frame_style() -> str:
    """The box around a conditional/repeated block.

    Unfilled, deliberately: a fragment sits *inside* a phase band and around
    live messages, so any fill would either hide the band it is in or wash out
    the arrows it contains. It is a border and nothing else.
    """
    return (
        f"rounded=0;html=1;fillColor=none;strokeColor={_INK_MUTED};strokeWidth=1;"
        "verticalAlign=top;align=left;"
    )


def fragment_tab_style() -> str:
    """The operator label ("alt", "opt", "loop", "par") in the frame's corner.

    A filled tab rather than bare text: it sits on the frame's own border line,
    and text alone there reads as a message label that has drifted.
    """
    return (
        f"rounded=0;whiteSpace=nowrap;html=1;fillColor=#FFFFFF;strokeColor={_INK_MUTED};"
        f"strokeWidth=1;fontColor={_TEXT};fontSize=10;fontStyle=1;"
        "align=center;verticalAlign=middle;"
    )


def fragment_guard_style() -> str:
    """A branch condition, written in brackets under its divider — the UML
    convention, and what tells one arm of an ``alt`` from the next."""
    return (
        f"text;html=1;align=left;verticalAlign=middle;whiteSpace=nowrap;"
        f"fontSize=10;fontStyle=2;fontColor={_TEXT};labelBackgroundColor=#FFFFFF;"
    )


def fragment_divider_style() -> str:
    """The dashed rule between two arms of an ``alt``."""
    return f"shape=line;strokeWidth=1;html=1;strokeColor={_INK_MUTED};dashed=1;"


def sequence_number_style() -> str:
    """The autonumber badge on a message — a small circle at the arrow's tail,
    so a long conversation can be talked about by step number."""
    return (
        f"ellipse;html=1;fillColor={_NUMBER_FILL};strokeColor=none;"
        "fontColor=#FFFFFF;fontSize=9;fontStyle=1;whiteSpace=wrap;"
    )
