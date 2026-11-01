"""Compile a ``kind="sequence"`` ``SimpleDiagramIR`` into draw.io XML.

A sequence diagram is the one view in this app whose **vertical axis is time**.
Participants are columns; each message sits one pitch below the last; and the
order of ``ir.messages`` is the entire content. That makes it structurally
unlike everything else here: the flow compiler's layered layout would happily
reorder these, and any pass that "tidies" geometry destroys the meaning rather
than the appearance.

## No edges, again

Like ``chart.py``, this compiler emits only vertices. An arrow is a
``shape=line`` shaft plus a ``triangle`` head — two vertices — rather than an
mxCell edge. That is not stylistic: ``repair_xml`` runs
``fix_edge_anchors_from_geometry`` over every document, which recomputes each
edge's endpoints from surrounding geometry and would re-anchor every message to
its lifeline's face midpoint, collapsing a whole conversation onto one line.
Keeping messages as vertices means this view needs no exemption from the shared
pipeline at all.

## What is derived rather than declared

Activation bars (the blocks showing a participant is busy) are *not* in the IR.
They follow from the messages: a ``sync`` call activates its target, and the
matching ``reply`` from that participant deactivates it. Asking a model to
place them by hand would be asking it to keep two representations of the same
fact consistent — see ``_activations``.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import sequence_theme as theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.domain.ir.simple import SequenceMessage, SimpleDiagramIR

#: Thinnest mark emitted, for the same reason charts have one: a zero-sized
#: vertex is clamped back up to a full leaf box by ``ensure_valid_vertex_geometry``.
_MIN_MARK = 1.0
_PAGE_MARGIN = 40.0
_TITLE_H = 34.0
_BLOCK_GAP = 12.0


def compile_sequence_ir(ir: SimpleDiagramIR, *, page_name: str = "Diagram") -> str:
    """Render a sequence IR to a complete draw.io document."""
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    if not ir.is_sequence():
        raise CompilerError(f"compile_sequence_ir got kind={ir.kind!r}")
    return _SequenceCompiler(ir, page_name).run()


def _message_slots(messages: list[SequenceMessage]) -> list[float]:
    """Vertical offset of each message from the first message's baseline.

    Not a flat multiple of the pitch, for two reasons. A self-message loops out
    and back down its own lifeline, so it occupies more vertical space than a
    straight arrow. And entering a new phase reserves a title strip for the
    band's label, while leaving one opens a gap so consecutive bands read as
    separate stages rather than one striped block. Returning offsets (rather
    than letting the caller multiply) keeps all of that in one place.
    """
    offsets: list[float] = []
    cursor = 0.0
    previous_phase = ""
    previous_fragment = ""
    previous_branch = ""
    for message in messages:
        phase = message.phase.strip()
        if phase != previous_phase:
            if previous_phase:
                cursor += geo.SEQ_BAND_GAP_Y
            if phase:
                cursor += geo.SEQ_BAND_TITLE_H
        # A fragment reserves a strip for its operator tab and first guard on
        # entry, and each further branch needs room for its dashed divider and
        # its own guard — without which the divider would be drawn through the
        # message above it.
        fragment = message.fragment.strip()
        branch = message.branch.strip()
        if fragment != previous_fragment:
            if previous_fragment:
                cursor += geo.SEQ_FRAGMENT_TAIL_Y
            if fragment:
                cursor += geo.SEQ_FRAGMENT_HEAD_H
        elif fragment and branch != previous_branch:
            cursor += geo.SEQ_FRAGMENT_BRANCH_H
        offsets.append(cursor)
        cursor += geo.SEQ_MESSAGE_PITCH_Y
        if message.source == message.target:
            cursor += geo.SEQ_SELF_DROP
        previous_phase = phase
        previous_fragment = fragment
        previous_branch = branch
    return offsets


def _fragments(
    messages: list[SequenceMessage], offsets: list[float],
) -> list[tuple[SequenceMessage, float, float, list[tuple[str, float]]]]:
    """Group consecutive messages sharing a ``fragment`` into one framed block.

    Returns ``(first_message, top, bottom, branches)`` where ``branches`` is
    ``(guard_label, y)`` per arm — the first at the fragment's own top, each
    later one at the offset where its dashed divider goes.

    Keyed on the repeated fragment *name*, exactly like ``_bands``, and for the
    same reason: index ranges ("messages 3 to 7") ask a model to keep
    arithmetic correct while it edits the list around them, and one inserted
    message silently shifts every block.

    Without this the compiler drew a conditional as consecutive messages, so
    "if the fraud score is high" and "if it is low" read as though both
    happen — the one case where this view stated something false rather than
    merely plain.
    """
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, message in enumerate(messages):
        name = message.fragment.strip()
        previous = messages[index - 1].fragment.strip() if index else ""
        if name != previous and start is not None:
            groups.append((start, index - 1))
            start = None
        if name and start is None:
            start = index
    if start is not None:
        groups.append((start, len(messages) - 1))

    out: list[tuple[SequenceMessage, float, float, list[tuple[str, float]]]] = []
    for first, last in groups:
        branches: list[tuple[str, float]] = []
        seen_branch: str | None = None
        for index in range(first, last + 1):
            branch = messages[index].branch.strip()
            if branch != seen_branch:
                y = offsets[index] - (
                    geo.SEQ_FRAGMENT_HEAD_H if index == first else geo.SEQ_FRAGMENT_BRANCH_H
                )
                branches.append((branch, y))
                seen_branch = branch
        tail = geo.SEQ_SELF_DROP if messages[last].source == messages[last].target else 0.0
        out.append((
            messages[first],
            offsets[first] - geo.SEQ_FRAGMENT_HEAD_H,
            offsets[last] + tail + geo.SEQ_FRAGMENT_TAIL_Y,
            branches,
        ))
    return out


def _bands(
    messages: list[SequenceMessage], offsets: list[float],
) -> list[tuple[str, float, float]]:
    """Group consecutive messages sharing a phase into ``(label, top, bottom)``.

    Keyed on the phase *name* repeated across messages rather than on index
    ranges. Index ranges would be the obvious schema ("messages 3 to 7"), but
    they ask a model to do arithmetic that stays correct while it edits the
    list around them — and a revision that inserts one message silently shifts
    every band. A repeated string cannot drift out of alignment with the
    messages it labels.
    """
    bands: list[tuple[str, float, float]] = []
    start_index: int | None = None
    for index, message in enumerate(messages):
        phase = message.phase.strip()
        previous = messages[index - 1].phase.strip() if index else ""
        if phase != previous and start_index is not None:
            bands.append((previous, start_index, index - 1))
            start_index = None
        if phase and start_index is None:
            start_index = index
    if start_index is not None:
        bands.append((messages[start_index].phase.strip(), start_index, len(messages) - 1))

    spans: list[tuple[str, float, float]] = []
    for label, first, last in bands:
        tail = geo.SEQ_SELF_DROP if messages[last].source == messages[last].target else 0.0
        spans.append((
            label,
            offsets[first] - geo.SEQ_BAND_TITLE_H,
            offsets[last] + tail + geo.SEQ_BAND_TAIL_Y,
        ))
    return spans


def _activations(
    messages: list[SequenceMessage], offsets: list[float], last_offset: float,
) -> list[tuple[str, float, float]]:
    """Derive ``(participant_id, start_offset, end_offset)`` activation blocks.

    A ``sync`` message activates its *target* — the callee is now doing work.
    A ``reply`` from a participant closes the most recent activation opened on
    it, which is what makes nested calls (A->B, B->C, C-->B, B-->A) come out as
    nested bars. ``async`` messages open nothing: nobody is waiting.

    An activation with no matching reply is still drawn rather than dropped —
    an unreturned call is a perfectly ordinary thing to draw, and silently
    omitting the bar would hide it. It closes at the last message that
    *involves* that participant, though, not at the bottom of the page. Running
    it to the page bottom claims the participant is still working long after it
    last appears in the conversation, which is a fact the diagram doesn't have;
    on a 24-message claims flow it also painted two full-height pillars down
    columns that were only busy near the top.
    """
    open_stacks: dict[str, list[float]] = {}
    spans: list[tuple[str, float, float]] = []
    last_seen: dict[str, float] = {}
    for message, offset in zip(messages, offsets):
        last_seen[message.source] = offset
        last_seen[message.target] = offset
        if message.source == message.target:
            continue
        if message.kind == "sync":
            open_stacks.setdefault(message.target, []).append(offset)
        elif message.kind == "reply":
            stack = open_stacks.get(message.source)
            if stack:
                spans.append((message.source, stack.pop(), offset))
    for participant_id, stack in open_stacks.items():
        end = min(
            last_seen.get(participant_id, last_offset) + geo.SEQ_MESSAGE_PITCH_Y / 2.0,
            last_offset + geo.SEQ_MESSAGE_PITCH_Y / 2.0,
        )
        for start in stack:
            spans.append((participant_id, start, end))
    return spans


class _SequenceCompiler:
    def __init__(self, ir: SimpleDiagramIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)
        self._seq = 0

    def _id(self, prefix: str) -> str:
        """Sequence-first, zero-padded — same contract as ``chart._id``.

        ``xml_tools.validate_and_fix_xml`` re-sorts cells by ``(z_rank, depth,
        id)`` with a plain string tiebreak, so ids whose alphabetical order
        differs from their emission order silently re-layer the page. Here that
        would put arrowheads behind activation blocks.
        """
        self._seq += 1
        return f"s{self._seq:04d}_{prefix}"

    def _text(self, label: str, style: str, x: float, y: float, w: float, h: float) -> None:
        self.w.vertex(id=self._id("txt"), label=label, style=style, x=x, y=y, w=w, h=h, parent="1")

    def run(self) -> str:
        participants = self.ir.participants
        messages = self.ir.messages
        offsets = _message_slots(messages)

        title_y = geo.SEQ_ORIGIN_Y
        head_y = title_y + _TITLE_H + _BLOCK_GAP
        first_message_y = head_y + geo.SEQ_HEADER_H + geo.SEQ_FIRST_MESSAGE_GAP
        last_offset = offsets[-1] if offsets else 0.0
        lifeline_h = (
            geo.SEQ_HEADER_H + geo.SEQ_FIRST_MESSAGE_GAP + last_offset + geo.SEQ_TAIL_GAP
        )
        pitch = geo.SEQ_PARTICIPANT_W + geo.SEQ_PARTICIPANT_GAP_X

        column_x: dict[str, float] = {}
        for index, participant in enumerate(participants):
            left = geo.SEQ_ORIGIN_X + index * pitch
            column_x[participant.id] = left + geo.SEQ_PARTICIPANT_W / 2.0

        self._text(
            self.ir.title or "Sequence", theme.title_style(),
            geo.SEQ_ORIGIN_X, title_y,
            max(len(participants) * pitch - geo.SEQ_PARTICIPANT_GAP_X, 200.0), _TITLE_H,
        )

        # Phase bands go down before the lifelines so the dashed stems, the
        # activation blocks and every arrow all render *over* their band —
        # a band is a backdrop for a stage, not a box drawn on top of it.
        band_left = geo.SEQ_ORIGIN_X - geo.SEQ_BAND_PAD_X
        band_right = (
            geo.SEQ_ORIGIN_X + (len(participants) - 1) * pitch
            + geo.SEQ_PARTICIPANT_W + geo.SEQ_BAND_PAD_X
        )
        for band_index, (label, top, bottom) in enumerate(_bands(messages, offsets)):
            band_y = first_message_y + top
            band_h = max(bottom - top, geo.SEQ_MESSAGE_PITCH_Y)
            self.w.vertex(
                id=self._id("band"), label="", style=theme.band_style(band_index),
                x=band_left, y=band_y,
                w=max(band_right - band_left, _MIN_MARK), h=band_h, parent="1",
            )
            self.w.vertex(
                id=self._id("rail"), label="", style=theme.band_rail_style(band_index),
                x=band_left, y=band_y, w=geo.SEQ_BAND_RAIL_W, h=band_h, parent="1",
            )
            # The title rides in a chip straddling the band's top edge — a tab,
            # not a caption inside the band. Sitting wholly inside it, the chip
            # shared the strip with the first message's autonumber badge and the
            # two overlapped whenever that message left the leftmost column.
            chip_w = len(label) * geo.SEQ_BAND_CHIP_CHAR_W + geo.SEQ_BAND_CHIP_PAD_X
            self.w.vertex(
                id=self._id("chip"), label=label, style=theme.band_chip_style(band_index),
                x=band_left + geo.SEQ_BAND_RAIL_W + 8.0,
                y=band_y - geo.SEQ_BAND_CHIP_H / 2.0,
                w=max(chip_w, 60.0), h=geo.SEQ_BAND_CHIP_H, parent="1",
            )

        # Fragments sit above the phase wash and below the lifelines: the frame
        # groups messages, so it must not be painted over the arrows it groups.
        for first, top, bottom, branches in _fragments(messages, offsets):
            frag_left = band_left + geo.SEQ_FRAGMENT_INSET_X
            frag_right = band_right - geo.SEQ_FRAGMENT_INSET_X
            frag_y = first_message_y + top
            frag_h = max(bottom - top, geo.SEQ_MESSAGE_PITCH_Y)
            self.w.vertex(
                id=self._id("frag"), label="", style=theme.fragment_frame_style(),
                x=frag_left, y=frag_y,
                w=max(frag_right - frag_left, _MIN_MARK), h=frag_h, parent="1",
            )
            self.w.vertex(
                id=self._id("fragtab"), label=first.fragment_kind,
                style=theme.fragment_tab_style(),
                x=frag_left, y=frag_y,
                w=geo.SEQ_FRAGMENT_TAB_W, h=geo.SEQ_FRAGMENT_TAB_H, parent="1",
            )
            for branch_index, (guard, guard_y) in enumerate(branches):
                # Every arm but the first is separated by a dashed rule; the
                # first needs none because the frame's own top edge is it.
                if branch_index:
                    self.w.vertex(
                        id=self._id("fragdiv"), label="",
                        style=theme.fragment_divider_style(),
                        x=frag_left, y=first_message_y + guard_y,
                        w=max(frag_right - frag_left, _MIN_MARK),
                        h=geo.SEQ_SHAFT_H, parent="1",
                    )
                if not guard:
                    continue
                self.w.vertex(
                    id=self._id("fragguard"), label=f"[{guard}]",
                    style=theme.fragment_guard_style(),
                    x=frag_left + geo.SEQ_FRAGMENT_TAB_W + 8.0,
                    y=first_message_y + guard_y,
                    w=max(frag_right - frag_left - geo.SEQ_FRAGMENT_TAB_W - 16.0, _MIN_MARK),
                    h=geo.SEQ_FRAGMENT_TAB_H, parent="1",
                )

        # Lifelines next: everything else is drawn over them, and draw.io
        # z-orders by document order.
        for participant in participants:
            # Minted, not `participant.id`. Nothing in the document refers to a
            # lifeline (there are no edges), and a model-authored id breaks the
            # z-order guarantee the padded sequence exists for: "zebra" sorts
            # after every "s0001_"-style id, so validate_and_fix_xml's id-keyed
            # cell sort would lift that lifeline above the arrows crossing it.
            self.w.vertex(
                id=self._id("life"), label=theme.header_label(participant.label),
                style=theme.lifeline_style(participant.kind, header_h=geo.SEQ_HEADER_H),
                x=column_x[participant.id] - geo.SEQ_PARTICIPANT_W / 2.0, y=head_y,
                w=geo.SEQ_PARTICIPANT_W, h=lifeline_h, parent="1",
            )

        # Activation blocks sit over the stem but under the arrows, so a head
        # landing on a busy lifeline is drawn on top of its block.
        kinds = {p.id: p.kind for p in participants}
        for participant_id, start, end in _activations(messages, offsets, last_offset):
            centre = column_x.get(participant_id)
            if centre is None:
                continue
            top = first_message_y + start
            height = max(end - start, geo.SEQ_MESSAGE_PITCH_Y / 2.0)
            self.w.vertex(
                id=self._id("act"), label="",
                style=theme.activation_style(kinds.get(participant_id, "participant")),
                x=centre - geo.SEQ_ACTIVATION_W / 2.0, y=top,
                w=geo.SEQ_ACTIVATION_W, h=height, parent="1",
            )

        for number, (message, offset) in enumerate(zip(messages, offsets), start=1):
            y = first_message_y + offset
            if message.source == message.target:
                self._self_message(message, column_x[message.source], y)
            else:
                self._message(message, column_x[message.source], column_x[message.target], y)
            # Autonumber badge at the tail, so a long conversation can be talked
            # about by step number the way the reference diagrams do.
            tail_x = column_x[message.source]
            forward = column_x[message.target] >= tail_x
            self.w.vertex(
                id=self._id("num"), label=str(number), style=theme.sequence_number_style(),
                x=tail_x + (2.0 if forward else -geo.SEQ_NUMBER_D - 2.0),
                y=y - geo.SEQ_SHAFT_H / 2.0 - geo.SEQ_NUMBER_D - 1.0,
                w=geo.SEQ_NUMBER_D, h=geo.SEQ_NUMBER_D, parent="1",
            )

        self.w.mark_layout_authoritative()
        width = geo.SEQ_ORIGIN_X + len(participants) * pitch
        return self.w.to_xml(
            page_width=max(width + _PAGE_MARGIN, 400.0),
            page_height=max(head_y + lifeline_h + _PAGE_MARGIN, 300.0),
        )

    def _shaft(self, x: float, y: float, w: float, h: float, *, dashed: bool) -> None:
        self.w.vertex(
            id=self._id("shaft"), label="", style=theme.shaft_style(dashed=dashed),
            x=x, y=y, w=max(w, _MIN_MARK), h=max(h, _MIN_MARK), parent="1",
        )

    def _head(
        self, x: float, y: float, *, pointing_right: bool, open_head: bool, muted: bool,
    ) -> None:
        self.w.vertex(
            id=self._id("head"), label="",
            style=theme.arrowhead_style(
                pointing_right=pointing_right, open_head=open_head, muted=muted,
            ),
            x=x, y=y - geo.SEQ_ARROW_H / 2.0,
            w=geo.SEQ_ARROW_W, h=geo.SEQ_ARROW_H, parent="1",
        )

    def _message(self, message: SequenceMessage, from_x: float, to_x: float, y: float) -> None:
        pointing_right = to_x > from_x
        dashed = message.kind == "reply"
        open_head = message.kind == "async"
        # Stop the shaft short of the arrowhead so the two don't overlap into a
        # blunt tip, and clear the activation block on the receiving lifeline.
        inset = geo.SEQ_ACTIVATION_W / 2.0
        if pointing_right:
            start = from_x + inset
            end = to_x - inset - geo.SEQ_ARROW_W
            head_x = to_x - inset - geo.SEQ_ARROW_W
        else:
            start = to_x + inset + geo.SEQ_ARROW_W
            end = from_x - inset
            head_x = to_x + inset
        self._shaft(start, y - geo.SEQ_SHAFT_H / 2.0, end - start, geo.SEQ_SHAFT_H, dashed=dashed)
        self._head(head_x, y, pointing_right=pointing_right, open_head=open_head, muted=dashed)
        if message.label:
            self._text(
                message.label, theme.message_label_style(muted=dashed),
                min(start, end), y - geo.SEQ_SHAFT_H / 2.0 - 18.0,
                max(abs(end - start), 40.0), 18.0,
            )

    def _self_message(self, message: SequenceMessage, centre: float, y: float) -> None:
        """A participant calling itself — out to the right, down, and back.

        Three shafts rather than one, because the loop has three straight legs;
        drawing it as a single rotated mark would misrepresent the shape.
        """
        dashed = message.kind == "reply"
        open_head = message.kind == "async"
        left = centre + geo.SEQ_ACTIVATION_W / 2.0
        right = left + geo.SEQ_SELF_WIDTH
        bottom = y + geo.SEQ_SELF_DROP
        self._shaft(left, y - geo.SEQ_SHAFT_H / 2.0, right - left, geo.SEQ_SHAFT_H, dashed=dashed)
        # The vertical leg: shape=line draws along the box's long axis, so a tall
        # narrow box needs `direction=north` to draw down rather than across.
        self.w.vertex(
            id=self._id("shaft"), label="",
            style=theme.shaft_style(dashed=dashed) + "direction=north;",
            x=right - geo.SEQ_SHAFT_H / 2.0, y=y, w=geo.SEQ_SHAFT_H, h=bottom - y, parent="1",
        )
        self._shaft(
            left + geo.SEQ_ARROW_W, bottom - geo.SEQ_SHAFT_H / 2.0,
            right - left - geo.SEQ_ARROW_W, geo.SEQ_SHAFT_H, dashed=dashed,
        )
        self._head(left, bottom, pointing_right=False, open_head=open_head, muted=dashed)
        if message.label:
            self._text(
                message.label, theme.message_label_style(align="left", muted=dashed),
                right + 8.0, y + geo.SEQ_SELF_DROP / 2.0 - 9.0, 160.0, 18.0,
            )
