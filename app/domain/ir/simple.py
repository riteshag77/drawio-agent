"""Simple (non-cloud) diagram IR — the fresh-generation counterpart to ``generic.py``.

Two families live behind one schema, discriminated by ``kind``:

- ``"flow"`` — anything that is nodes joined by arrows: flowcharts, org charts,
  state machines, ER diagrams, mindmaps, swimlaned processes. These compile
  through the existing generic compiler, which already lays out an unpositioned
  node graph (``compiler/generic._place_topological``).
- ``"sequence"`` — who calls whom, in what order. Not a node graph: the vertical
  axis is time, so message *order* carries meaning that a flow layout cannot
  express. Own compiler (``compiler/sequence.py``).
- ``"bar"`` / ``"line"`` / ``"pie"`` / ``"gantt"`` — charts plotted from *numbers* rather than
  drawn from a topology. These need axes, scales and proportional geometry, so
  they get their own compiler (``compiler/chart.py``).

Sibling to ``GenericDiagramIR``, not a reuse of it, because the two answer
different questions. ``GenericDiagramIR`` describes *an image the user
uploaded*: every field is an observation (``x``/``y`` as a fraction of the
source picture, ``fill_color`` "omit if the shape is white in the image",
``icon_hint`` "never invent a brand that isn't visibly there"). Those field
descriptions are the prompt the model actually reads, and there is no source
image on this path — asking a model to report the colour it saw when nothing
was shown to it invites invention. Here the model is *designing* a diagram, so
it declares structure only and the compiler owns every pixel, exactly like the
cloud views.

One schema rather than four is deliberate: the ``"simple"`` intent is a single
LLM call, and a discriminated union lets the model pick the representation
that fits the request ("show me the login steps" -> flow, "sales by month" ->
bar) without a separate classification round-trip. ``semantic_errors`` enforces
that only the chosen kind's fields are populated, so a model that fills in both
gets one corrective pass rather than a half-drawn page.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from app.domain.ir.base import Identified, IRModel
from app.domain.ir.generic import GenericNodeShape

SimpleDiagramKind = Literal["flow", "sequence", "bar", "line", "pie", "gantt"]

#: Density figures above which a diagram is *probably* doing too much. These
#: two are advisory only — they surface through ``semantic_warnings`` and are
#: logged, never enforced. They were hard errors, which meant a 30-category
#: chart failed the whole request over a readability opinion, even though the
#: compiler grows the plot frame to fit any number of them
#: (``CHART_MIN_COLUMN_W``). Rejecting a diagram nobody can prove is wrong is
#: the same mistake as the shelf-anchor dead end: fail the user for a
#: preference they never agreed to.
MAX_CATEGORIES = 24
MAX_TASKS = 30

#: This one *is* enforced, and is not a matter of taste. A categorical palette
#: is assigned in fixed slot order and never cycled, so with only
#: ``palette.PALETTE_SLOTS`` hues a 7th series could only reuse a colour already
#: on the chart — two different things drawn identically. Raising it means
#: adding validated hues to ``compiler/palette.py`` first.
MAX_SERIES = 6
#: Pie is capped harder than the other charts because each slice needs its own
#: categorical colour, and a categorical palette is assigned in fixed slot
#: order and never cycled — a 7th slice could only reuse a hue already in use.
#: Must stay <= ``compiler/chart_theme.PALETTE_SLOTS``.
MAX_PIE_SLICES = 6


class SimpleGroup(Identified):
    """A labelled boundary box grouping nodes — a swimlane, phase, or cluster."""

    label: str = ""
    boundary: Literal["none", "dashed", "solid"] = Field(
        default="dashed",
        description="Border of the grouping box: 'dashed' (default), 'solid', or 'none' for an invisible group.",
    )


class SimpleNode(Identified):
    """One shape in a node-and-arrow diagram."""

    shape: GenericNodeShape = Field(
        description=(
            "One of: rectangle (a step/entity — the default), rounded_rectangle "
            "(start/end terminal), diamond (a decision), ellipse, parallelogram "
            "(input/output), hexagon (preparation), cylinder (a datastore), cloud, "
            "actor (a person), text (a bare caption with no box)."
        )
    )
    label: str = Field(default="", description="Short text shown on the shape — 1-4 words.")
    group: str = Field(
        default="",
        description="Optional SimpleGroup.id this node sits inside; '' means ungrouped.",
    )


class SimpleEdge(IRModel):
    """A connector between two node (or group) ids."""

    source: str = Field(description="id of the cell the arrow leaves")
    target: str = Field(description="id of the cell the arrow enters")
    label: str = Field(
        default="",
        description="Optional text on the connector, e.g. a decision branch ('Yes', 'No'). 1-3 words.",
    )
    line_style: Literal["straight", "orthogonal", "curved"] = "orthogonal"
    dashed: bool = Field(
        default=False, description="True for an optional/conditional/asynchronous path.",
    )
    arrow: Literal["none", "forward", "both"] = "forward"


class ChartSeries(IRModel):
    """One named run of values, aligned positionally to ``categories``."""

    label: str = Field(
        default="",
        description=(
            "Name of this series, shown in the legend (e.g. '2025', 'Revenue'). "
            "May be empty when the chart has only one series."
        ),
    )
    values: list[float] = Field(
        description=(
            "One number per category, in the same order as `categories` — same length "
            "as `categories`, no gaps. Use 0 for a genuinely zero value, never null."
        )
    )


class SequenceParticipant(Identified):
    """One column of a sequence diagram — a lifeline."""

    label: str = Field(description="Name shown in the participant's header box, 1-3 words.")
    kind: Literal["participant", "actor"] = Field(
        default="participant",
        description="'actor' for a human/external role, 'participant' for a system or service.",
    )


class SequenceMessage(IRModel):
    """One arrow between lifelines, at a point in time.

    Order in the list *is* the order in time — there is no timestamp field,
    because a sequence diagram's whole content is the ordering.
    """

    source: str = Field(description="id of the participant the arrow leaves")
    target: str = Field(description="id of the participant it arrives at")
    label: str = Field(
        default="",
        description="What is being sent — a call, event or return value. 1-5 words.",
    )
    kind: Literal["sync", "async", "reply"] = Field(
        default="sync",
        description=(
            "'sync' for a call that waits for a response (solid arrow, and it starts "
            "an activation bar on the target). 'reply' for the return of an earlier "
            "call (dashed arrow, ends that activation). 'async' for fire-and-forget "
            "— an event or a queued message that nothing waits on."
        ),
    )
    phase: str = Field(
        default="",
        description=(
            "Optional name of the stage this message belongs to, e.g. 'First Notice of "
            "Loss' or 'Settlement'. Consecutive messages sharing a phase are drawn "
            "inside one shaded, labelled band, which is what turns a long conversation "
            "into a readable story. Repeat the exact same string on every message in "
            "the stage; leave blank when a diagram is short enough not to need stages."
        ),
    )
    fragment: str = Field(
        default="",
        description=(
            "Optional name of the conditional/repeated block this message is inside, "
            "e.g. 'fraud outcome' or 'retry'. Consecutive messages sharing it are "
            "framed together and labelled with `fragment_kind`. Same repeat-the-string "
            "convention as `phase`. Leave blank for messages that always happen."
        ),
    )
    fragment_kind: Literal["alt", "opt", "loop", "par"] = Field(
        default="alt",
        description=(
            "What kind of block `fragment` is. 'alt' = mutually exclusive branches "
            "(use `branch` to name each one). 'opt' = happens or doesn't. 'loop' = "
            "repeats. 'par' = happens concurrently. Ignored when `fragment` is blank."
        ),
    )
    branch: str = Field(
        default="",
        description=(
            "Which branch of an 'alt' this message belongs to — the condition, e.g. "
            "'High fraud score' or 'Below threshold'. Consecutive messages sharing the "
            "same branch form one arm; changing it starts the next arm, drawn below a "
            "dashed divider. For 'opt'/'loop'/'par' this is the guard or repeat "
            "condition and is written once."
        ),
    )


class GanttTask(IRModel):
    """One bar on a timeline, positioned in abstract column units."""

    label: str = Field(description="Task name, shown to the left of its bar.")
    start: float = Field(
        ge=0.0,
        description=(
            "Which time column this task begins in, counting from 0. With `categories` "
            "of ['Jan','Feb','Mar'], a task starting in February has start=1."
        ),
    )
    duration: float = Field(
        gt=0.0,
        description="How many time columns the task spans. Must be greater than 0.",
    )


class SimpleDiagramIR(IRModel):
    """Root document for the non-cloud generation path.

    Field order matters and is not alphabetical: Pydantic emits JSON-schema
    properties in declaration order, and both constrained decoding and
    function-calling models write fields in that order (see ``DiagramIR``'s
    docstring for the measured failure this avoids). ``kind`` therefore comes
    first — every later field's relevance depends on it — and within the flow
    family the structural fields precede ``edges`` so the model decides its
    nodes before wiring them together.
    """

    kind: SimpleDiagramKind = Field(
        description=(
            "Which representation this request needs. 'flow' for anything made of "
            "shapes joined by arrows (flowchart, org chart, state machine, ER diagram, "
            "mindmap, process/swimlane). 'sequence' when the point is who calls whom "
            "in what order over time (API calls, message passing, a protocol "
            "handshake). 'bar' for comparing quantities across "
            "categories, 'line' for a trend across an ordered sequence (months, "
            "versions, steps), 'pie' for parts of a whole, 'gantt' for tasks over "
            "time. Populate only the fields belonging to the kind you choose."
        )
    )
    title: str = Field(default="Diagram", description="Short title for the diagram.")

    # --- kind="flow" ---------------------------------------------------------
    groups: list[SimpleGroup] = Field(
        default_factory=list,
        description="flow only. Boundary boxes (swimlanes/phases). Omit unless grouping helps.",
    )
    nodes: list[SimpleNode] = Field(
        default_factory=list, description="flow only. The shapes in the diagram.",
    )
    edges: list[SimpleEdge] = Field(
        default_factory=list, description="flow only. The arrows between shapes.",
    )

    # --- kind="bar" | "line" | "pie" | "gantt" -------------------------------
    categories: list[str] = Field(
        default_factory=list,
        description=(
            "bar/line/pie: the label of each column, point or slice. gantt: the "
            "time-axis column headings (e.g. ['Q1','Q2','Q3']) — optional there, "
            "and numbered if omitted."
        ),
    )
    series: list[ChartSeries] = Field(
        default_factory=list,
        description=(
            "bar/line/pie only. One entry per data series; 'pie' takes exactly one "
            "(a pie has a single set of slices)."
        ),
    )
    tasks: list[GanttTask] = Field(
        default_factory=list, description="gantt only. The timeline bars, top to bottom.",
    )

    # --- kind="sequence" -----------------------------------------------------
    participants: list[SequenceParticipant] = Field(
        default_factory=list,
        description="sequence only. The lifelines, left to right in the order they appear.",
    )
    messages: list[SequenceMessage] = Field(
        default_factory=list,
        description=(
            "sequence only. The arrows, in the order they happen — first in the list "
            "is first in time, drawn at the top."
        ),
    )
    value_label: str = Field(
        default="",
        description=(
            "bar/line only. Caption for the value axis, including its unit — e.g. "
            "'Revenue ($M)', 'Users'. Leave blank if the numbers are self-explanatory."
        ),
    )

    # Declared last on purpose. This is the one prose field on the schema, and a
    # model that writes its summary first tends to describe a diagram it has not
    # designed yet, then follow its own description rather than the request.
    assistant_message: str = Field(
        default="",
        description=(
            "One short sentence for the chat reply, written after the diagram above. "
            "Say what you drew. If the request asked for something this schema cannot "
            "express (a line chart, a scatter plot, a sequence diagram with lifelines), "
            "say plainly which of the four kinds you used instead and why."
        ),
    )

    @field_validator("kind", mode="before")
    @classmethod
    def _normalize_kind(cls, value: object) -> object:
        """Accept the obvious synonyms for the four kinds.

        Models reach for the word in the user's request ('flowchart', 'piechart',
        'barchart') rather than the schema's own token often enough to be worth
        absorbing here — the alternative is a corrective round-trip that changes
        one string. Anything not listed still fails validation loudly.
        """
        if not isinstance(value, str):
            return value
        key = value.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        aliases = {
            "flow": "flow", "flowchart": "flow", "graph": "flow", "diagram": "flow",
            "bar": "bar", "barchart": "bar", "column": "bar", "columnchart": "bar",
            "line": "line", "linechart": "line", "linegraph": "line", "area": "line",
            "trend": "line", "series": "line",
            "pie": "pie", "piechart": "pie", "donut": "pie",
            "gantt": "gantt", "ganttchart": "gantt", "timeline": "gantt",
        }
        return aliases.get(key, value)

    def is_chart(self) -> bool:
        return self.kind in ("bar", "line", "pie", "gantt")

    def is_sequence(self) -> bool:
        return self.kind == "sequence"

    def leaf_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def structural_ids(self) -> set[str]:
        return {g.id for g in self.groups}

    def all_ids(self) -> set[str]:
        return self.leaf_ids() | self.structural_ids()

    def _flow_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.nodes:
            errors.append("kind='flow' needs at least one node in `nodes`")
        seen: set[str] = set()
        group_ids = self.structural_ids()
        for node in self.nodes:
            if node.id in seen:
                errors.append(f"duplicate cell id {node.id!r}")
            seen.add(node.id)
            if node.group and node.group not in group_ids:
                errors.append(f"node {node.id!r} references unknown group {node.group!r}")
        for group in self.groups:
            if group.id in seen:
                errors.append(f"duplicate cell id {group.id!r} reused across nodes/groups")
            seen.add(group.id)
        known = self.all_ids()
        for edge in self.edges:
            if edge.source not in known:
                errors.append(f"edge source {edge.source!r} is not a known cell id")
            if edge.target not in known:
                errors.append(f"edge target {edge.target!r} is not a known cell id")
        # Self-loops are deliberately allowed, matching GenericDiagramIR: a
        # "retry"/"repeat until valid" transition is ordinary flowchart and
        # state-machine content, not a mistake.
        return errors

    def _series_errors(self) -> list[str]:
        """Shared bar/pie checks — a series is only meaningful when its values
        line up one-to-one with the categories they are plotted against."""
        errors: list[str] = []
        if not self.categories:
            errors.append(f"kind={self.kind!r} needs at least one entry in `categories`")
        if not self.series:
            errors.append(f"kind={self.kind!r} needs at least one entry in `series`")
        if len(self.series) > MAX_SERIES:
            errors.append(
                f"`series` has {len(self.series)} entries but the palette has {MAX_SERIES} "
                "distinct colours — two series would be drawn in the same colour"
            )
        for index, series in enumerate(self.series):
            if len(series.values) != len(self.categories):
                errors.append(
                    f"series[{index}] has {len(series.values)} values but there are "
                    f"{len(self.categories)} categories — they must match one-to-one"
                )
        return errors

    def _sequence_errors(self) -> list[str]:
        """A sequence diagram is participants plus an ordered message list; the
        endpoints must resolve or an arrow has nowhere to land."""
        errors: list[str] = []
        if not self.participants:
            errors.append("kind='sequence' needs at least one entry in `participants`")
        if not self.messages:
            errors.append("kind='sequence' needs at least one entry in `messages`")
        seen: set[str] = set()
        for participant in self.participants:
            if participant.id in seen:
                errors.append(f"duplicate participant id {participant.id!r}")
            seen.add(participant.id)
        for index, message in enumerate(self.messages):
            if message.source not in seen:
                errors.append(
                    f"messages[{index}] source {message.source!r} is not a declared participant"
                )
            if message.target not in seen:
                errors.append(
                    f"messages[{index}] target {message.target!r} is not a declared participant"
                )
        # A self-message (source == target) is deliberately allowed — an object
        # calling its own method is ordinary sequence-diagram content, and the
        # compiler draws it as a loop back into the same lifeline.
        return errors

    def semantic_errors(self) -> list[str]:
        errors: list[str] = []

        if self.kind == "flow":
            errors.extend(self._flow_errors())
        else:
            # Populating flow fields on another kind is the one cross-kind
            # mistake that silently produces a wrong *and* plausible page, so it
            # is an error rather than something the compiler quietly ignores.
            if self.nodes or self.edges or self.groups:
                errors.append(
                    f"kind={self.kind!r} does not use `nodes`, `edges` or `groups` — leave them empty"
                )

        if self.kind == "sequence":
            errors.extend(self._sequence_errors())
        elif self.participants or self.messages:
            errors.append(
                f"kind={self.kind!r} does not use `participants` or `messages` — leave them empty"
            )

        if self.kind in ("bar", "line", "pie"):
            errors.extend(self._series_errors())
        if self.kind == "pie":
            if len(self.series) > 1:
                errors.append("kind='pie' takes exactly one series — a pie has a single set of slices")
            if len(self.categories) > MAX_PIE_SLICES:
                errors.append(
                    f"kind='pie' has {len(self.categories)} slices — keep it to {MAX_PIE_SLICES} "
                    "or fewer, grouping the smallest values into an 'Other' slice"
                )
            for series in self.series[:1]:
                if any(v < 0 for v in series.values):
                    errors.append("kind='pie' cannot plot negative values")
                elif sum(series.values) <= 0:
                    errors.append("kind='pie' needs at least one value greater than 0")

        if self.kind == "gantt":
            if not self.tasks:
                errors.append("kind='gantt' needs at least one entry in `tasks`")
        elif self.tasks:
            errors.append(f"kind={self.kind!r} does not use `tasks` — leave it empty")

        if self.kind in ("flow", "sequence", "gantt") and self.series:
            errors.append(f"kind={self.kind!r} does not use `series` — leave it empty")
        if self.kind in ("flow", "sequence") and self.categories:
            errors.append(f"kind={self.kind!r} does not use `categories` — leave it empty")

        return errors

    def semantic_warnings(self) -> list[str]:
        """Density advice. Never blocks: these are judgements about whether a
        diagram is doing too much, and the user asking for a 40-row gantt is
        entitled to one."""
        warnings: list[str] = []
        if len(self.categories) > MAX_CATEGORIES:
            warnings.append(
                f"{len(self.categories)} categories is dense for one chart "
                f"(over {MAX_CATEGORIES}) — it will render, but consider grouping"
            )
        if len(self.tasks) > MAX_TASKS:
            warnings.append(
                f"{len(self.tasks)} gantt rows is dense for one chart (over {MAX_TASKS})"
            )
        return warnings

    def is_valid(self) -> bool:
        return not self.semantic_errors()
