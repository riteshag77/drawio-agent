"""Compile the chart kinds of ``SimpleDiagramIR`` (bar / pie / gantt) to draw.io XML.

A chart is *plotted*, not laid out: there is no topology to read, only numbers
to map onto a frame. So none of this app's layout machinery applies — no ELK,
no topological placement, no leaf grid, no ``LEAF_W``/``LEAF_H`` (the "leaf size
is always LEAF_W/H" rule in CLAUDE.md is about icons; a bar's height *is* its
value). Every dimension comes from the ``CHART_*`` block in ``geometry_spec``.

## Everything is a vertex

This compiler emits no edges at all — gridlines, axes and baselines are 1px
rectangles (``chart_theme.rule_style``). That is a deliberate defence, not a
shortcut: ``repair_xml`` runs ``remove_dangling_edges`` (which would delete
every gridline, since a gridline connects no cells) and
``fix_edge_anchors_from_geometry`` (which recomputes edge endpoints from
surrounding geometry) over every document. A chart made of rectangles is
invisible to both, so it survives the shared pipeline unmodified.

The same care applies to zero-valued bars: a bar of height 0 would be clamped
back up to a full 78x78 leaf box by ``ensure_valid_vertex_geometry``, which
rejects non-positive sizes. Zero-height marks are therefore emitted as 1px
hairlines sitting on the baseline — see ``_MIN_MARK``.

## Reading order

Cells are emitted back-to-front because draw.io z-orders by document order:
row bands, then gridlines, then data marks, then text. A label written before
its bar would be painted under it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import chart_theme as theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.domain.ir.simple import SimpleDiagramIR

#: Thinnest mark the compiler will emit. Anything smaller is either invisible
#: or (at zero) clamped to a full leaf box by the geometry repair pass.
_MIN_MARK = 1.0

#: A bar narrower than this cannot carry a centred value label without
#: colliding with its neighbour, so labels are dropped for that chart and the
#: value axis carries the reading instead.
_MIN_LABELLED_BAR_W = 34.0

#: Breathing room between stacked blocks (title -> plot -> captions).
_BLOCK_GAP = 12.0
#: Outer margin added to the right and bottom of the content box.
_PAGE_MARGIN = 40.0


@dataclass(frozen=True)
class _Frame:
    """The plot area a cartesian chart draws into, and the scale mapping onto it."""

    x: float
    y: float
    w: float
    h: float
    bottom: float
    #: Width of one category's slot along the x axis.
    slot_w: float
    #: Value -> absolute y. Closes over the axis range, so callers never see it.
    y_for: Callable[[float], float]


def compile_chart_ir(ir: SimpleDiagramIR, *, page_name: str = "Diagram") -> str:
    """Render a ``bar``/``line``/``pie``/``gantt`` IR to a complete draw.io document."""
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    if not ir.is_chart():
        raise CompilerError(f"compile_chart_ir got kind={ir.kind!r}, which is not a chart")

    compiler = _ChartCompiler(ir, page_name)
    if ir.kind == "bar":
        return compiler.run_bar()
    if ir.kind == "line":
        return compiler.run_line()
    if ir.kind == "pie":
        return compiler.run_pie()
    return compiler.run_gantt()


def _nice_step(rough: float) -> float:
    """Round a rough interval up to the nearest 1/2/2.5/5 x 10^n.

    The standard axis-scaling trick: humans read ticks at those intervals and
    nowhere else, so a computed range of 0-3170 becomes 0-3500 in steps of 500
    rather than 0-3170 in steps of 634.
    """
    if rough <= 0:
        return 1.0
    exponent = math.floor(math.log10(rough))
    magnitude = 10.0**exponent
    fraction = rough / magnitude
    for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
        if fraction <= candidate:
            return candidate * magnitude
    return 10.0 * magnitude


def _nice_scale(vmin: float, vmax: float, ticks: int) -> tuple[float, float, float]:
    """Return ``(lo, hi, step)`` — a rounded axis range covering ``vmin..vmax``.

    Always includes zero, because a bar's length is read from the baseline and
    a truncated baseline exaggerates differences (the classic misleading-chart
    failure). A flat series (every value identical) still gets a real range, so
    it renders as one uniform row of bars instead of dividing by zero.
    """
    lo = min(0.0, vmin)
    hi = max(0.0, vmax)
    if math.isclose(lo, hi):
        hi = lo + 1.0
    step = _nice_step((hi - lo) / max(ticks, 1))
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    if math.isclose(lo, hi):
        hi = lo + step
    return lo, hi, step


def _fmt(value: float) -> str:
    """Format an axis tick or data label compactly.

    Large magnitudes get a k/M/B suffix — a y-axis reading 1,500,000 crowds out
    the plot it labels, and the exact figure is not what an axis is for.
    """
    if value == 0:
        return "0"
    magnitude = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if magnitude >= limit:
            scaled = value / limit
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude < 1:
        # Two decimals turn every sub-0.01 value into "0" — a label stating a
        # number the bar isn't drawing (measured: 0.001 rendered as "0" beside
        # a 1.5B bar). Significant figures keep small magnitudes truthful
        # without padding ordinary values with noise.
        return f"{value:.3g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


class _ChartCompiler:
    def __init__(self, ir: SimpleDiagramIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)
        self._seq = 0

    def _id(self, prefix: str) -> str:
        """Mint a unique cell id whose *lexicographic* order is emission order.

        Charts carry no model-authored ids (``semantic_errors`` requires
        ``nodes``/``groups`` to be empty on every chart kind), so unlike the
        generic compiler there is nothing here to collide with — but the id
        still has to sort correctly, which is why it is zero-padded and
        sequence-first rather than the obvious ``f"{prefix}_{n}"``.

        ``xml_tools.validate_and_fix_xml`` re-sorts every cell by
        ``(z_rank, depth, id)`` so that parents always precede children, and
        within one rank that tiebreak is a plain string compare. With names
        like ``bar_13``/``rule_2`` it put every ``bar`` before every ``rule``,
        which inverted this module's whole back-to-front emission order:
        gridlines were re-stacked on top of the bars they sit behind (visible
        in the rendered chart as lines striping across each bar). Sorting on a
        padded sequence makes that pass a no-op for chart pages instead of a
        silent re-layer.
        """
        self._seq += 1
        return f"c{self._seq:04d}_{prefix}"

    # ── shared furniture ────────────────────────────────────────────────

    def _title(self, x: float, y: float, width: float) -> float:
        """Emit the chart title; return the y the next block starts at."""
        self.w.vertex(
            id=self._id("title"), label=self.ir.title or "Chart", style=theme.title_style(),
            x=x, y=y, w=max(width, 200.0), h=geo.CHART_TITLE_H, parent="1",
        )
        return y + geo.CHART_TITLE_H + _BLOCK_GAP

    def _rule(self, x: float, y: float, w: float, h: float, style: str) -> None:
        self.w.vertex(
            id=self._id("rule"), label="", style=style,
            x=x, y=y, w=max(w, _MIN_MARK), h=max(h, _MIN_MARK), parent="1",
        )

    def _text(self, label: str, style: str, x: float, y: float, w: float, h: float) -> None:
        self.w.vertex(id=self._id("txt"), label=label, style=style, x=x, y=y, w=w, h=h, parent="1")

    def _legend(self, entries: list[tuple[str, str]], x: float, y: float) -> tuple[float, float]:
        """Draw a legend column of ``(colour, text)`` rows at ``(x, y)``.

        Returns the block's ``(width, height)``. This is what discharges the
        palette's sub-3:1 contrast warning (see ``chart_theme``): identity is
        carried by text in ink, with the colour beside it as a cue rather than
        as the only channel.
        """
        row_h = geo.CHART_LEGEND_ROW_H
        swatch = geo.CHART_LEGEND_SWATCH
        for index, (color, text) in enumerate(entries):
            row_y = y + index * row_h
            self.w.vertex(
                id=self._id("swatch"), label="", style=theme.legend_swatch_style(color),
                x=x, y=row_y + (row_h - swatch) / 2.0, w=swatch, h=swatch, parent="1",
            )
            self._text(
                text, theme.legend_text_style(),
                x + swatch + geo.CHART_LEGEND_TEXT_GAP, row_y,
                geo.CHART_LEGEND_TEXT_W, row_h,
            )
        width = swatch + geo.CHART_LEGEND_TEXT_GAP + geo.CHART_LEGEND_TEXT_W
        return width, row_h * len(entries)

    def _finish(self, content_right: float, content_bottom: float) -> str:
        self.w.mark_layout_authoritative()
        return self.w.to_xml(
            page_width=max(content_right + _PAGE_MARGIN, 400.0),
            page_height=max(content_bottom + _PAGE_MARGIN, 300.0),
        )

    # ── shared cartesian frame (bar + line) ─────────────────────────────

    def _plot_frame(self) -> _Frame:
        """Emit title, gridlines, value ticks and the axis rule; return the
        frame the data marks are then plotted into.

        Bar and line differ only in what they draw *inside* this frame — the
        scale, gridlines and axes are identical — so it lives here rather than
        being written twice and drifting.
        """
        categories = self.ir.categories
        plot_w = max(geo.CHART_PLOT_W, len(categories) * geo.CHART_MIN_COLUMN_W)
        plot_h = geo.CHART_PLOT_H
        plot_x = geo.CHART_ORIGIN_X + geo.CHART_AXIS_GUTTER_X
        plot_y = self._title(geo.CHART_ORIGIN_X, geo.CHART_ORIGIN_Y, plot_w + geo.CHART_AXIS_GUTTER_X)
        plot_bottom = plot_y + plot_h

        flat = [v for s in self.ir.series for v in s.values]
        lo, hi, step = _nice_scale(min(flat), max(flat), geo.CHART_VALUE_TICKS)
        span = hi - lo

        def y_for(value: float) -> float:
            return plot_bottom - (value - lo) / span * plot_h

        # Gridlines + value ticks, back-to-front: furniture before data.
        tick = lo
        while tick <= hi + step * 0.5:
            ty = y_for(tick)
            is_zero = math.isclose(tick, 0.0, abs_tol=step * 1e-6)
            self._rule(
                plot_x, ty, plot_w, _MIN_MARK,
                theme.axis_line_style() if is_zero else theme.gridline_style(),
            )
            self._text(
                _fmt(tick), theme.tick_label_style(),
                geo.CHART_ORIGIN_X, ty - 9.0, geo.CHART_AXIS_GUTTER_X - 8.0, 18.0,
            )
            tick += step
        # Left axis rule, drawn full height so the plot frame reads as a frame.
        self._rule(plot_x, plot_y, _MIN_MARK, plot_h, theme.axis_line_style())

        return _Frame(
            x=plot_x, y=plot_y, w=plot_w, h=plot_h,
            bottom=plot_bottom, slot_w=plot_w / len(categories), y_for=y_for,
        )

    def _finish_cartesian(self, frame: _Frame) -> str:
        """Category labels, the value-axis caption and the legend — the parts
        below and beside the plot that bar and line share."""
        for cat_index, name in enumerate(self.ir.categories):
            self._text(
                name, theme.tick_label_style(align="center"),
                frame.x + cat_index * frame.slot_w, frame.bottom + 6.0,
                frame.slot_w, geo.CHART_AXIS_GUTTER_Y - 6.0,
            )

        content_right = frame.x + frame.w
        content_bottom = frame.bottom + geo.CHART_AXIS_GUTTER_Y

        if self.ir.value_label:
            self._text(
                self.ir.value_label, theme.axis_caption_style(vertical=True),
                geo.CHART_ORIGIN_X - 26.0, frame.y, 22.0, frame.h,
            )

        # A single series needs no legend — the title already names it.
        if len(self.ir.series) > 1:
            legend_w, legend_h = self._legend(
                [
                    (theme.series_color(i), s.label or f"Series {i + 1}")
                    for i, s in enumerate(self.ir.series)
                ],
                content_right + geo.CHART_LEGEND_TEXT_GAP * 3, frame.y,
            )
            content_right += geo.CHART_LEGEND_TEXT_GAP * 3 + legend_w
            content_bottom = max(content_bottom, frame.y + legend_h)

        return self._finish(content_right, content_bottom)

    # ── bar ─────────────────────────────────────────────────────────────

    def run_bar(self) -> str:
        categories = self.ir.categories
        series = self.ir.series
        frame = self._plot_frame()
        plot_x, y_for = frame.x, frame.y_for

        slot_w = frame.slot_w
        cluster_w = slot_w * geo.CHART_BAR_FILL
        gap = geo.CHART_BAR_SERIES_GAP if len(series) > 1 else 0.0
        bar_w = (cluster_w - gap * (len(series) - 1)) / len(series)
        bar_w = max(bar_w, _MIN_MARK)
        label_bars = bar_w >= _MIN_LABELLED_BAR_W

        zero_y = y_for(0.0)
        for cat_index in range(len(categories)):
            slot_x = plot_x + cat_index * slot_w + (slot_w - cluster_w) / 2.0
            for series_index, one_series in enumerate(series):
                value = one_series.values[cat_index]
                bar_x = slot_x + series_index * (bar_w + gap)
                value_y = y_for(value)
                top = min(value_y, zero_y)
                height = abs(value_y - zero_y)
                self.w.vertex(
                    id=self._id("bar"), label="",
                    style=theme.bar_style(theme.series_color(series_index)),
                    x=bar_x, y=top, w=bar_w, h=max(height, _MIN_MARK), parent="1",
                )
                if label_bars:
                    # Above a positive bar, below a negative one — never inside,
                    # where a short bar would clip the text.
                    label_y = top - 18.0 if value >= 0 else top + height
                    self._text(_fmt(value), theme.value_label_style(), bar_x, label_y, bar_w, 16.0)

        return self._finish_cartesian(frame)

    # ── line ────────────────────────────────────────────────────────────

    def run_line(self) -> str:
        """Plot each series as connected segments with a marker per point.

        Segments are **rotated rectangles**, not edges. A sloped line is the
        one mark here that isn't naturally axis-aligned, and reaching for an
        edge to draw it would put the chart straight back in the path of
        ``remove_dangling_edges`` (a segment connects no cells, so it would be
        deleted) and ``fix_edge_anchors_from_geometry``. draw.io's ``rotation=``
        turns a thin rectangle about its own centre, which draws the same line
        while staying a vertex — so the no-edges rule holds for every chart
        kind, with no special case downstream.
        """
        frame = self._plot_frame()
        # Points sit at the centre of their category slot, matching where a bar
        # chart puts its bars, so the two read the same way against the axis.
        def point_x(index: int) -> float:
            return frame.x + (index + 0.5) * frame.slot_w

        for series_index, one_series in enumerate(self.ir.series):
            color = theme.series_color(series_index)
            points = [
                (point_x(i), frame.y_for(value)) for i, value in enumerate(one_series.values)
            ]
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                self._segment(x1, y1, x2, y2, color)
            # Markers last so they sit above the segments meeting under them.
            for px, py in points:
                self.w.vertex(
                    id=self._id("dot"), label="", style=theme.marker_style(color),
                    x=px - geo.CHART_LINE_MARKER / 2.0, y=py - geo.CHART_LINE_MARKER / 2.0,
                    w=geo.CHART_LINE_MARKER, h=geo.CHART_LINE_MARKER, parent="1",
                )

        return self._finish_cartesian(frame)

    def _segment(self, x1: float, y1: float, x2: float, y2: float, color: str) -> None:
        """One line segment, drawn as a rectangle rotated about its midpoint."""
        length = math.hypot(x2 - x1, y2 - y1)
        if length < _MIN_MARK:
            return
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        mid_x, mid_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        self.w.vertex(
            id=self._id("seg"), label="",
            style=theme.segment_style(color, angle),
            x=mid_x - length / 2.0, y=mid_y - geo.CHART_LINE_W / 2.0,
            w=length, h=geo.CHART_LINE_W, parent="1",
        )

    # ── pie ─────────────────────────────────────────────────────────────

    def run_pie(self) -> str:
        labels = self.ir.categories
        values = self.ir.series[0].values
        total = sum(values)
        diameter = geo.CHART_PIE_D
        pie_x = geo.CHART_ORIGIN_X
        pie_y = self._title(geo.CHART_ORIGIN_X, geo.CHART_ORIGIN_Y, diameter)

        cursor = 0.0
        for index, value in enumerate(values):
            fraction = value / total
            if fraction <= 0:
                # A zero-value slice has no wedge to draw, but it still earns a
                # legend row below — dropping it silently would leave the reader
                # wondering which category went missing.
                continue
            end = cursor + fraction
            self.w.vertex(
                id=self._id("slice"), label="",
                # Last slice closes exactly on 1.0 rather than on accumulated
                # float error, so no hairline gap appears at the 12 o'clock seam.
                style=theme.pie_slice_style(
                    theme.series_color(index), cursor, 1.0 if index == len(values) - 1 else end,
                ),
                x=pie_x, y=pie_y, w=diameter, h=diameter, parent="1",
            )
            cursor = end

        legend_w, legend_h = self._legend(
            [
                (
                    theme.series_color(index),
                    f"{label} — {_fmt(values[index])} ({values[index] / total * 100:.0f}%)",
                )
                for index, label in enumerate(labels)
            ],
            pie_x + diameter + geo.CHART_PIE_LEGEND_GAP,
            # Vertically centred against the pie.
            pie_y + max((diameter - geo.CHART_LEGEND_ROW_H * len(labels)) / 2.0, 0.0),
        )
        return self._finish(
            pie_x + diameter + geo.CHART_PIE_LEGEND_GAP + legend_w,
            pie_y + max(diameter, legend_h),
        )

    # ── gantt ───────────────────────────────────────────────────────────

    def run_gantt(self) -> str:
        tasks = self.ir.tasks
        # Columns span the whole timeline: enough for the latest-finishing task,
        # and never fewer than the caller supplied headings for.
        span = max((t.start + t.duration for t in tasks), default=1.0)
        columns = max(int(math.ceil(span - 1e-9)), len(self.ir.categories), 1)
        column_w = max(geo.CHART_MIN_COLUMN_W, geo.CHART_PLOT_W / columns)
        plot_w = column_w * columns
        plot_x = geo.CHART_ORIGIN_X + geo.CHART_GANTT_LABEL_W
        header_y = self._title(
            geo.CHART_ORIGIN_X, geo.CHART_ORIGIN_Y, geo.CHART_GANTT_LABEL_W + plot_w,
        )
        rows_y = header_y + geo.CHART_GANTT_ROW_H
        rows_h = geo.CHART_GANTT_ROW_H * len(tasks)

        # Alternating row bands first — everything else is drawn on top.
        for index in range(len(tasks)):
            if index % 2:
                continue
            self._rule(
                geo.CHART_ORIGIN_X, rows_y + index * geo.CHART_GANTT_ROW_H,
                geo.CHART_GANTT_LABEL_W + plot_w, geo.CHART_GANTT_ROW_H,
                theme.rule_style("#FAFAFA"),
            )

        # Column headings and their separators.
        for column in range(columns + 1):
            self._rule(plot_x + column * column_w, header_y, _MIN_MARK, rows_h + geo.CHART_GANTT_ROW_H,
                       theme.gridline_style())
        for column in range(columns):
            heading = (
                self.ir.categories[column] if column < len(self.ir.categories) else str(column + 1)
            )
            self._text(
                heading, theme.tick_label_style(align="center"),
                plot_x + column * column_w, header_y, column_w, geo.CHART_GANTT_ROW_H,
            )
        self._rule(geo.CHART_ORIGIN_X, rows_y, geo.CHART_GANTT_LABEL_W + plot_w, _MIN_MARK,
                   theme.axis_line_style())

        for index, task in enumerate(tasks):
            row_y = rows_y + index * geo.CHART_GANTT_ROW_H
            self._text(
                task.label, theme.tick_label_style(),
                geo.CHART_ORIGIN_X, row_y, geo.CHART_GANTT_LABEL_W - 10.0, geo.CHART_GANTT_ROW_H,
            )
            self.w.vertex(
                id=self._id("task"), label="",
                # One hue for every bar: on a gantt the encoding is *time*, so
                # colouring bars by index would imply a categorical difference
                # between tasks that doesn't exist.
                style=theme.bar_style(theme.series_color(0)),
                x=plot_x + task.start * column_w,
                y=row_y + geo.CHART_GANTT_BAR_INSET,
                w=max(task.duration * column_w, _MIN_MARK),
                h=geo.CHART_GANTT_ROW_H - geo.CHART_GANTT_BAR_INSET * 2,
                parent="1",
            )

        return self._finish(plot_x + plot_w, rows_y + rows_h)
