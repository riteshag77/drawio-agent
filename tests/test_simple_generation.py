"""The non-cloud path: ``SimpleDiagramIR`` validation and its two compilers.

Replaces the Mermaid fast-path tests. Those exercised a generator whose output
could never reach the canvas — it produced Mermaid source for a draw.io embed
``import`` action that does not exist, so the embed answered
``{error: 'unknownMessage'}`` and drew nothing. The assertions here are
therefore weighted toward the property that was missing before: what comes out
is draw.io XML that renders.
"""

from __future__ import annotations

import math
import re
import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler.chart_theme import PALETTE_SLOTS, SERIES
from app.adapters.outbound.diagram.compiler.simple import compile_simple_ir
from app.core.logging import StepLogger
from app.core.services.agent import _SIMPLE_SOFT_NODE_CAP, generate_simple_diagram_from_ir
from app.domain.ir.simple import MAX_PIE_SLICES, MAX_SERIES, SimpleDiagramIR


def _flow(**overrides) -> dict:
    payload = {
        "kind": "flow",
        "title": "Claim Flow",
        "nodes": [
            {"id": "start", "shape": "rounded_rectangle", "label": "Start"},
            {"id": "check", "shape": "diamond", "label": "Valid?"},
            {"id": "done", "shape": "rounded_rectangle", "label": "End"},
        ],
        "edges": [
            {"source": "start", "target": "check"},
            {"source": "check", "target": "done", "label": "Yes"},
        ],
    }
    payload.update(overrides)
    return payload


def _bar(**overrides) -> dict:
    payload = {
        "kind": "bar",
        "title": "Revenue",
        "categories": ["Q1", "Q2"],
        "series": [{"label": "2026", "values": [10.0, 20.0]}],
    }
    payload.update(overrides)
    return payload


def _cells(xml: str) -> list[ET.Element]:
    return list(ET.fromstring(xml).iter("mxCell"))


class KindDiscriminationTests(unittest.TestCase):
    """The schema is a discriminated union, so the cross-kind mistakes are the
    ones worth catching — a model that picks a chart and then fills in nodes
    would otherwise compile to a plausible-looking but wrong page."""

    def test_flow_is_valid(self) -> None:
        self.assertEqual(SimpleDiagramIR(**_flow()).semantic_errors(), [])

    def test_chart_with_flow_fields_is_rejected(self) -> None:
        ir = SimpleDiagramIR(**_bar(nodes=[{"id": "a", "shape": "rectangle", "label": "x"}]))
        self.assertTrue(any("does not use `nodes`" in e for e in ir.semantic_errors()))

    def test_flow_with_chart_fields_is_rejected(self) -> None:
        ir = SimpleDiagramIR(**_flow(categories=["Q1"]))
        self.assertTrue(any("categories" in e for e in ir.semantic_errors()))

    def test_kind_synonyms_are_normalized(self) -> None:
        """Models reach for the user's word ("flowchart", "piechart") over the
        schema's token often enough to absorb rather than round-trip."""
        self.assertEqual(SimpleDiagramIR(**_flow(kind="flowchart")).kind, "flow")
        self.assertEqual(SimpleDiagramIR(**_bar(kind="bar chart")).kind, "bar")
        self.assertEqual(SimpleDiagramIR(**_bar(kind="line graph")).kind, "line")

    def test_unknown_kind_still_fails(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            SimpleDiagramIR(**_flow(kind="sankey"))


def _sequence(**overrides) -> dict:
    payload = {
        "kind": "sequence",
        "title": "Login",
        "participants": [
            {"id": "user", "label": "Browser", "kind": "actor"},
            {"id": "api", "label": "API"},
            {"id": "db", "label": "Database"},
        ],
        "messages": [
            {"source": "user", "target": "api", "label": "POST /login", "kind": "sync"},
            {"source": "api", "target": "db", "label": "find user", "kind": "sync"},
            {"source": "db", "target": "api", "label": "user row", "kind": "reply"},
            {"source": "api", "target": "user", "label": "token", "kind": "reply"},
        ],
    }
    payload.update(overrides)
    return payload


class SequenceTests(unittest.TestCase):
    def test_valid_sequence(self) -> None:
        self.assertEqual(SimpleDiagramIR(**_sequence()).semantic_errors(), [])

    def test_message_endpoints_must_be_declared(self) -> None:
        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "user", "target": "ghost", "label": "x"},
        ]))
        self.assertTrue(any("not a declared participant" in e for e in ir.semantic_errors()))

    def test_other_kinds_reject_sequence_fields(self) -> None:
        ir = SimpleDiagramIR(**_bar(participants=[{"id": "a", "label": "A"}]))
        self.assertTrue(any("`participants`" in e for e in ir.semantic_errors()))

    def test_sequence_emits_no_edges(self) -> None:
        """Messages are a shape=line shaft plus a triangle head — both vertices.
        As edges they would be re-anchored by fix_edge_anchors_from_geometry to
        their lifeline's face midpoint, collapsing the conversation onto one
        line, on the one view where vertical position is the meaning."""
        xml = compile_simple_ir(SimpleDiagramIR(**_sequence()))
        self.assertEqual([c for c in _cells(xml) if c.get("edge") == "1"], [])

    def test_messages_descend_in_declared_order(self) -> None:
        ir = SimpleDiagramIR(**_sequence())
        cells = _cells(compile_simple_ir(ir))
        shafts = [c for c in cells if "_shaft" in (c.get("id") or "")]
        ys = [float(c.find("mxGeometry").get("y")) for c in shafts]
        self.assertEqual(ys, sorted(ys), "message order is the diagram's content")
        self.assertEqual(len(shafts), len(ir.messages))

    def test_activations_nest_from_sync_reply_pairing(self) -> None:
        """A->B then B->C, C-->B, B-->A must give C's bar inside B's, derived
        from the messages rather than declared."""
        from app.adapters.outbound.diagram.compiler.sequence import _activations, _message_slots

        ir = SimpleDiagramIR(**_sequence())
        offsets = _message_slots(ir.messages)
        spans = dict((pid, (s, e)) for pid, s, e in _activations(ir.messages, offsets, offsets[-1]))
        api_start, api_end = spans["api"]
        db_start, db_end = spans["db"]
        self.assertLess(api_start, db_start)
        self.assertGreater(api_end, db_end)

    def test_unreturned_call_still_draws_a_bar(self) -> None:
        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "user", "target": "api", "label": "fire", "kind": "sync"},
        ]))
        xml = compile_simple_ir(ir)
        self.assertTrue([c for c in _cells(xml) if "_act" in (c.get("id") or "")])

    def test_async_message_opens_no_activation(self) -> None:
        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "api", "target": "db", "label": "log", "kind": "async"},
        ]))
        xml = compile_simple_ir(ir)
        self.assertEqual([c for c in _cells(xml) if "_act" in (c.get("id") or "")], [])

    def test_self_message_draws_a_loop(self) -> None:
        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "api", "target": "api", "label": "sign token", "kind": "sync"},
        ]))
        cells = _cells(compile_simple_ir(ir))
        # Three legs: out, down, back.
        self.assertEqual(len([c for c in cells if "_shaft" in (c.get("id") or "")]), 3)

    def test_unreturned_call_ends_at_the_participants_last_message(self) -> None:
        """A bar with no reply closes where that participant stops appearing —
        not at the bottom of the page, which claimed it was still working
        through stages it takes no part in (and painted a full-height pillar)."""
        from app.adapters.outbound.diagram.compiler.sequence import _activations, _message_slots

        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "user", "target": "api", "label": "fire", "kind": "sync"},
            {"source": "user", "target": "db", "label": "later", "kind": "sync"},
            {"source": "db", "target": "user", "label": "done", "kind": "reply"},
        ]))
        offsets = _message_slots(ir.messages)
        spans = {pid: (s, e) for pid, s, e in _activations(ir.messages, offsets, offsets[-1])}
        # api is only involved in message 0, so its unclosed bar stops there.
        self.assertLess(spans["api"][1], offsets[-1])

    def test_participant_icon_becomes_the_header_icon(self) -> None:
        """The prompt pack asks for a leading emoji because a label renders
        as-is — so it is lifted onto its own line at icon size rather than
        being left inline at label size, where it read as punctuation."""
        ir = SimpleDiagramIR(**_sequence(
            participants=[{"id": "cl", "label": "👤 Claimant", "kind": "actor"},
                          {"id": "api", "label": "API"}],
            messages=[{"source": "cl", "target": "api", "label": "go"}],
        ))
        lifelines = [
            c for c in _cells(compile_simple_ir(ir)) if "_life" in (c.get("id") or "")
        ]
        labels = [c.get("value") for c in lifelines]
        self.assertTrue(any(f"font-size:{geo.SEQ_ICON_PT}px" in v and "👤" in v for v in labels))
        # The name survives the split, and no icon markup is invented for a
        # participant that never had an emoji.
        self.assertTrue(any("Claimant" in v for v in labels))
        self.assertTrue(any(v == "<b>API</b>" for v in labels))

    def test_actor_and_system_headers_share_one_row(self) -> None:
        """Actors used to be drawn as a narrow umlActor stick figure, leaving a
        gap in the header row and no room for the icon."""
        ir = SimpleDiagramIR(**_sequence())
        lifelines = [
            c for c in _cells(compile_simple_ir(ir)) if "_life" in (c.get("id") or "")
        ]
        widths = {float(c.find("mxGeometry").get("width")) for c in lifelines}
        self.assertEqual(widths, {geo.SEQ_PARTICIPANT_W})
        self.assertNotIn("umlActor", "".join(c.get("style") or "" for c in lifelines))

    def test_actor_and_system_take_different_role_hues(self) -> None:
        styles = {
            c.get("id"): c.get("style")
            for c in _cells(compile_simple_ir(SimpleDiagramIR(**_sequence())))
            if "_life" in (c.get("id") or "")
        }
        fills = {re.search(r"fillColor=(#\w+)", s).group(1) for s in styles.values()}
        self.assertEqual(len(fills), 2, "a person and a system must not look alike")

    def test_phase_band_carries_a_rail_and_a_title_chip(self) -> None:
        """The band's wash has to stay light enough to read arrows through, so
        its hue lives in a rail and a chip instead."""
        ir = SimpleDiagramIR(**_sequence(messages=[
            {"source": "user", "target": "api", "label": "go", "phase": "1 — Intake"},
            {"source": "api", "target": "user", "label": "ok", "kind": "reply",
             "phase": "1 — Intake"},
        ]))
        cells = _cells(compile_simple_ir(ir))
        rails = [c for c in cells if "_rail" in (c.get("id") or "")]
        chips = [c for c in cells if "_chip" in (c.get("id") or "")]
        self.assertEqual(len(rails), 1)
        self.assertEqual([c.get("value") for c in chips], ["1 — Intake"])
        # The chip straddles the band's top edge, clear of the first message's
        # autonumber badge, which shares that strip.
        band = next(c for c in cells if "_band" in (c.get("id") or ""))
        band_y = float(band.find("mxGeometry").get("y"))
        chip_y = float(chips[0].find("mxGeometry").get("y"))
        self.assertLess(chip_y, band_y)

    def test_lifeline_ids_are_minted_not_model_authored(self) -> None:
        """Cell z-order is keyed on id by validate_and_fix_xml, so a participant
        called "zebra" must not be able to sort its lifeline above the arrows."""
        ir = SimpleDiagramIR(**_sequence(
            participants=[{"id": "zebra", "label": "Z"}, {"id": "api", "label": "API"}],
            messages=[{"source": "zebra", "target": "api", "label": "go"}],
        ))
        ids = [c.get("id") for c in _cells(compile_simple_ir(ir))]
        self.assertNotIn("zebra", ids)
        self.assertTrue(any("_life" in (i or "") for i in ids))


class SeriesAlignmentTests(unittest.TestCase):
    def test_series_must_match_category_count(self) -> None:
        ir = SimpleDiagramIR(**_bar(series=[{"label": "2026", "values": [1.0]}]))
        self.assertTrue(any("one-to-one" in e for e in ir.semantic_errors()))

    def test_pie_takes_one_series(self) -> None:
        ir = SimpleDiagramIR(
            kind="pie", categories=["A", "B"],
            series=[{"values": [1.0, 2.0]}, {"values": [3.0, 4.0]}],
        )
        self.assertTrue(any("exactly one series" in e for e in ir.semantic_errors()))

    def test_pie_rejects_all_zero(self) -> None:
        ir = SimpleDiagramIR(kind="pie", categories=["A"], series=[{"values": [0.0]}])
        self.assertTrue(any("greater than 0" in e for e in ir.semantic_errors()))

    def test_pie_slices_capped_to_palette(self) -> None:
        """A 7th slice could only reuse a hue already on the chart, so it is
        rejected rather than drawn in a duplicate colour."""
        count = MAX_PIE_SLICES + 1
        ir = SimpleDiagramIR(
            kind="pie",
            categories=[f"C{i}" for i in range(count)],
            series=[{"values": [1.0] * count}],
        )
        self.assertTrue(any("Other" in e for e in ir.semantic_errors()))

    def test_palette_covers_every_allowed_series(self) -> None:
        self.assertGreaterEqual(PALETTE_SLOTS, MAX_SERIES)
        self.assertGreaterEqual(PALETTE_SLOTS, MAX_PIE_SLICES)
        self.assertEqual(len(set(SERIES)), len(SERIES), "palette hues must be distinct")


class CompilerOutputTests(unittest.TestCase):
    def test_flow_compiles_to_loadable_xml(self) -> None:
        xml = compile_simple_ir(SimpleDiagramIR(**_flow()))
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "mxfile")
        labels = {c.get("value") for c in _cells(xml) if c.get("vertex") == "1"}
        self.assertIn("Start", labels)
        self.assertIn("Valid?", labels)
        edges = [c for c in _cells(xml) if c.get("edge") == "1"]
        self.assertEqual(len(edges), 2)

    def test_flow_nodes_get_a_landscape_box(self) -> None:
        """A flow node holds words, not an icon with a caption, so it does not
        take the square cloud leaf — at LEAF_W a two-word label wrapped."""
        cells = _cells(compile_simple_ir(SimpleDiagramIR(**_flow())))
        boxes = [c for c in cells if c.get("vertex") == "1"]
        widths = {float(c.find("mxGeometry").get("width")) for c in boxes}
        self.assertEqual(widths, {geo.FLOW_NODE_W})
        # Still inside the column pitch the placement code lays out on.
        self.assertLess(geo.FLOW_NODE_W, geo.DATA_CANONICAL_PITCH)

    def test_flow_icon_is_lifted_above_the_label(self) -> None:
        ir = SimpleDiagramIR(**_flow(nodes=[
            {"id": "a", "shape": "rectangle", "label": "📞 Report loss"},
            {"id": "b", "shape": "rectangle", "label": "Plain step"},
        ], edges=[{"source": "a", "target": "b"}]))
        values = [c.get("value") for c in _cells(compile_simple_ir(ir)) if c.get("vertex") == "1"]
        self.assertIn(f'<span style="font-size:{geo.FLOW_ICON_PT}px', "".join(values))
        # A label with no emoji is left exactly as the model wrote it — a
        # diagram that never used icons produces the markup it always did.
        self.assertIn("Plain step", values)

    def test_actor_is_drawn_as_a_box_with_a_person_icon(self) -> None:
        """draw.io's umlActor is scaled to its cell, so beside a row of boxes
        the person was the largest thing on the page, in hairlines, with its
        name on the background below it."""
        ir = SimpleDiagramIR(**_flow(nodes=[
            {"id": "a", "shape": "actor", "label": "Claimant"},
            {"id": "b", "shape": "rectangle", "label": "Intake"},
        ], edges=[{"source": "a", "target": "b"}]))
        cells = {c.get("id"): c for c in _cells(compile_simple_ir(ir))}
        actor = cells["a"]
        self.assertNotIn("umlActor", actor.get("style"))
        self.assertIn("👤", actor.get("value"))
        # It keeps the actor hue even though it is drawn as a rectangle.
        self.assertNotEqual(
            re.search(r"fillColor=(#\w+)", actor.get("style")).group(1),
            re.search(r"fillColor=(#\w+)", cells["b"].get("style")).group(1),
        )

    def test_traced_images_keep_the_stick_figure_and_the_square_leaf(self) -> None:
        """The icon/box/actor treatment above is the *fresh* path's design
        choice. On the image-tracing path a shape is a report of what was in
        the picture, so nothing there may be restyled."""
        from app.adapters.outbound.diagram.compiler.generic import compile_generic_ir
        from app.domain.ir.generic import GenericDiagramIR, GenericNode

        traced = GenericDiagramIR(
            title="Traced", nodes=[GenericNode(id="a", shape="actor", label="👤 Person")],
        )
        cell = next(c for c in _cells(compile_generic_ir(traced)) if c.get("id") == "a")
        self.assertIn("umlActor", cell.get("style"))
        self.assertEqual(cell.get("value"), "👤 Person")
        self.assertEqual(float(cell.find("mxGeometry").get("width")), geo.LEAF_W)

    def test_flow_layout_is_marked_authoritative(self) -> None:
        """Without this the repair pass would re-flow geometry the compiler
        already decided."""
        xml = compile_simple_ir(SimpleDiagramIR(**_flow()))
        root_cell = [c for c in _cells(xml) if c.get("id") == "1"][0]
        self.assertEqual(root_cell.get("layout"), "ir")

    def test_charts_contain_no_edges(self) -> None:
        """Gridlines, axes and line segments are all rectangles, never edges:
        ``repair_xml`` deletes edges with no resolvable endpoints and rewrites
        the anchors of the rest, either of which would wreck a plotted chart.
        A sloped line segment is the one mark that isn't naturally
        axis-aligned, and it stays a vertex via ``rotation=``.
        """
        for payload in (
            _bar(),
            _bar(kind="line"),
            {"kind": "pie", "categories": ["A", "B"], "series": [{"values": [3.0, 1.0]}]},
            {"kind": "gantt", "tasks": [{"label": "Build", "start": 0, "duration": 2}]},
        ):
            with self.subTest(kind=payload["kind"]):
                xml = compile_simple_ir(SimpleDiagramIR(**payload))
                self.assertEqual([c for c in _cells(xml) if c.get("edge") == "1"], [])

    def test_no_mark_is_degenerate(self) -> None:
        """A zero-height bar would be clamped back to a full 78x78 leaf box by
        ``ensure_valid_vertex_geometry``, which rejects non-positive sizes."""
        xml = compile_simple_ir(SimpleDiagramIR(**_bar(series=[{"values": [0.0, 5.0]}])))
        for cell in _cells(xml):
            geom = cell.find("mxGeometry")
            if geom is None or cell.get("vertex") != "1":
                continue
            self.assertGreater(float(geom.get("width")), 0)
            self.assertGreater(float(geom.get("height")), 0)

    def test_bar_series_get_distinct_colours(self) -> None:
        ir = SimpleDiagramIR(**_bar(series=[
            {"label": "2025", "values": [1.0, 2.0]},
            {"label": "2026", "values": [3.0, 4.0]},
        ]))
        xml = compile_simple_ir(ir)
        fills = {
            part.split("=", 1)[1]
            for cell in _cells(xml)
            for part in (cell.get("style") or "").split(";")
            if part.startswith("fillColor=") and part.split("=", 1)[1] in SERIES
        }
        self.assertEqual(fills, {SERIES[0], SERIES[1]})

    def test_small_values_are_not_labelled_zero(self) -> None:
        """A label must never state a number the mark isn't drawing. Found by
        rendering 0.001 next to 1.5e9: the small bar came out labelled "0"."""
        from app.adapters.outbound.diagram.compiler.chart import _fmt

        self.assertEqual(_fmt(0.001), "0.001")
        self.assertEqual(_fmt(0.25), "0.25")
        self.assertEqual(_fmt(0), "0")
        self.assertEqual(_fmt(12.5), "12.5")
        self.assertEqual(_fmt(1_500_000_000), "1.5B")
        self.assertNotEqual(_fmt(0.004), _fmt(0))

    def test_line_connects_every_point(self) -> None:
        """N points make N-1 segments and N markers — a missing segment reads
        as a break in the data that isn't there."""
        ir = SimpleDiagramIR(
            kind="line", title="Trend", categories=["a", "b", "c", "d"],
            series=[{"label": "one", "values": [1.0, 4.0, 2.0, 5.0]}],
        )
        cells = _cells(compile_simple_ir(ir))
        segments = [c for c in cells if "_seg" in (c.get("id") or "")]
        markers = [c for c in cells if "_dot" in (c.get("id") or "")]
        self.assertEqual(len(segments), 3)
        self.assertEqual(len(markers), 4)

    def test_line_segments_are_rotated_rectangles(self) -> None:
        """The geometry each segment is rotated about must be its own centre,
        so a segment's length is its point-to-point distance."""
        ir = SimpleDiagramIR(
            kind="line", categories=["a", "b"], series=[{"values": [0.0, 10.0]}],
        )
        cells = _cells(compile_simple_ir(ir))
        segment = next(c for c in cells if "_seg" in (c.get("id") or ""))
        self.assertIn("rotation=", segment.get("style"))
        self.assertEqual(segment.get("edge"), None)
        geom = segment.find("mxGeometry")
        markers = [c.find("mxGeometry") for c in cells if "_dot" in (c.get("id") or "")]
        centres = [(float(g.get("x")) + float(g.get("width")) / 2.0,
                    float(g.get("y")) + float(g.get("height")) / 2.0) for g in markers]
        expected = math.hypot(centres[1][0] - centres[0][0], centres[1][1] - centres[0][1])
        # delta, not places: the emitter rounds every coordinate to 2dp.
        self.assertAlmostEqual(float(geom.get("width")), expected, delta=0.02)

    def test_line_series_get_distinct_colours(self) -> None:
        ir = SimpleDiagramIR(**_bar(kind="line", series=[
            {"label": "API", "values": [1.0, 2.0]},
            {"label": "Worker", "values": [3.0, 4.0]},
        ]))
        xml = compile_simple_ir(ir)
        fills = {
            part.split("=", 1)[1]
            for cell in _cells(xml)
            for part in (cell.get("style") or "").split(";")
            if part.startswith("fillColor=") and part.split("=", 1)[1] in SERIES
        }
        self.assertEqual(fills, {SERIES[0], SERIES[1]})

    def test_pie_slices_close_on_a_full_turn(self) -> None:
        """Accumulated float error at the 12 o'clock seam shows as a hairline
        gap, so the last slice is pinned to exactly 1.0."""
        ir = SimpleDiagramIR(kind="pie", categories=["A", "B", "C"], series=[{"values": [1.0, 1.0, 1.0]}])
        xml = compile_simple_ir(ir)
        ends = [
            float(part.split("=", 1)[1])
            for cell in _cells(xml)
            for part in (cell.get("style") or "").split(";")
            if part.startswith("endAngle=")
        ]
        self.assertEqual(max(ends), 1.0)

    def test_paint_order_survives_the_cell_reorder_pass(self) -> None:
        """Chart cells are emitted back-to-front and must stay that way.

        ``validate_and_fix_xml`` re-sorts every cell by ``(z_rank, depth, id)``
        to guarantee parents precede children, and the id tiebreak is a string
        compare — which, with ids like ``bar_13``/``rule_2``, restacked the
        gridlines on top of the bars (visible as lines striping across each
        bar in the rendered chart). Padded sequence-first ids keep that pass a
        no-op here.
        """
        from app.adapters.outbound.diagram.xml_tools import (
            choose_fixed_xml,
            ensure_diagram_document,
            validate_and_fix_xml,
        )

        xml = ensure_diagram_document(compile_simple_ir(SimpleDiagramIR(**_bar())), "Diagram")
        before = [c.get("id") for c in _cells(xml)]
        after = [c.get("id") for c in _cells(choose_fixed_xml(xml, validate_and_fix_xml(xml)))]
        self.assertEqual(before, after)

    def test_gantt_bar_spans_its_duration(self) -> None:
        ir = SimpleDiagramIR(kind="gantt", categories=["Jan", "Feb", "Mar", "Apr"], tasks=[
            {"label": "Short", "start": 0, "duration": 1},
            {"label": "Long", "start": 1, "duration": 3},
        ])
        xml = compile_simple_ir(ir)
        widths = sorted(
            float(cell.find("mxGeometry").get("width"))
            for cell in _cells(xml)
            if "_task" in (cell.get("id") or "")
        )
        self.assertEqual(len(widths), 2)
        self.assertAlmostEqual(widths[1] / widths[0], 3.0, places=5)

    def test_invalid_ir_is_refused_rather_than_half_drawn(self) -> None:
        from app.adapters.outbound.diagram.compiler.shared import CompilerError

        ir = SimpleDiagramIR(**_bar(series=[{"values": [1.0]}]))
        with self.assertRaises(CompilerError):
            compile_simple_ir(ir)


class GeneratorTests(unittest.TestCase):
    def _llm(self, payload: dict):
        def fake(system, human, provider, schema, logger=None, attachments=None):
            self.seen_system = system
            self.seen_human = human
            return SimpleDiagramIR(**payload)
        return fake

    def test_generates_xml_not_mermaid(self) -> None:
        result = generate_simple_diagram_from_ir("draw a claim flow", llm=self._llm(_flow()))
        self.assertIn('vertex="1"', result.xml)
        self.assertFalse(hasattr(result, "mermaid"))

    def test_model_authored_message_wins(self) -> None:
        payload = _bar(assistant_message="Shown as a bar chart — line charts aren't supported here.")
        result = generate_simple_diagram_from_ir("line chart of signups", llm=self._llm(payload))
        self.assertIn("bar chart", result.assistant_message)

    def test_revision_feeds_the_current_diagram_back_in(self) -> None:
        """A revision regenerates the whole IR, so it needs to see what it is
        revising — the conversation alone is lossy once a diagram has been
        built over several turns."""
        existing = compile_simple_ir(SimpleDiagramIR(**_bar(
            categories=["Q1", "Q2"], series=[{"label": "2026", "values": [120.0, 90.0]}],
        )))
        generate_simple_diagram_from_ir(
            "change Q2 to 200", llm=self._llm(_bar()), current_xml=existing,
        )
        self.assertIn("revising a diagram that already exists", self.seen_human)
        # The plotted numbers ride along in the value labels the compiler draws.
        self.assertIn("120", self.seen_human)
        self.assertIn("90", self.seen_human)

    def test_sequence_revision_carries_its_messages_back(self) -> None:
        """A sequence diagram's content is its *ordered* messages, and the
        revision block reaches the model as a flat label inventory. Verified by
        hand that a mid-order insert works ("MFA challenge" landed between
        "Authenticate" and "Redirect with Code" with nothing dropped) — what
        this pins is the mechanism that made it possible: the message labels
        must actually be in the inventory. They are only there because each one
        is its own text vertex.
        """
        existing = compile_simple_ir(SimpleDiagramIR(**_sequence()))
        generate_simple_diagram_from_ir(
            "add an MFA step", llm=self._llm(_sequence()), current_xml=existing,
        )
        for expected in ("Browser", "API", "POST /login", "find user", "user row"):
            self.assertIn(expected, self.seen_human)

    def test_fresh_generation_carries_no_revision_block(self) -> None:
        generate_simple_diagram_from_ir("draw a claim flow", llm=self._llm(_flow()))
        self.assertNotIn("revising a diagram", self.seen_human)

    def test_dense_flow_is_drawn_in_full_and_only_noted(self) -> None:
        """Nothing caps a flow's size — the pack's depth figures are floors.
        A large diagram is logged so the density is visible, and every node the
        model asked for still reaches the page. Sized off the constant so
        raising it can't quietly turn this into a test that asserts nothing."""
        count = _SIMPLE_SOFT_NODE_CAP + 4
        big = _flow(
            nodes=[{"id": f"n{i}", "shape": "rectangle", "label": f"Step {i}"} for i in range(count)],
            edges=[{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(count - 1)],
        )
        logger = StepLogger()
        result = generate_simple_diagram_from_ir("big flow", llm=self._llm(big), logger=logger)
        self.assertIn('vertex="1"', result.xml)
        # Every node survives: the density note must never become a truncation.
        self.assertEqual(result.xml.count('vertex="1"'), count)
        warnings = [e for e in logger.to_list() if e.get("level") == "warning"]
        self.assertTrue(
            any((e.get("details") or {}).get("node_count") == count for e in warnings)
        )


if __name__ == "__main__":
    unittest.main()
