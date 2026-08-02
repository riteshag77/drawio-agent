"""Canonical colour table (``diagram/palette.py``) and its enforcement.

The load-bearing property: two containers that mean *different things* must
never render identically. That is exactly what had regressed — ``zone_public``
was byte-identical to ``zone_dmz`` (the internet-facing surface and the
perimeter looked like one zone), ``zone_detect`` shared a fill with
``zone_private``, and both Data-flow SaaS shelves fell through to a single
``phase_saas`` entry. A colour collision passes every other test in the suite
and is glaring in the rendered diagram, so it is pinned here.

Peers of the *same* category legitimately share a hue — that shared hue is what
makes the category readable. Those pairs are declared in
``palette.INTENTIONAL_COLOR_TWINS`` and excluded below, so the exclusion list is
itself reviewable rather than the test silently tolerating any collision.
"""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import palette
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.placement_fixers import normalize_corridor_colors
from app.domain.ir import AnchoredCorridor, Corridor, DataFlowIR, Flow, Leaf, SecurityIR


def _leaf(i: str) -> Leaf:
    return Leaf(id=i, shape_type="aws_ecs", label=i.title())


def _style_of(xml: str, cell_id: str) -> str:
    cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
    return cells[cell_id].get("style") or ""


def _kv(style: str, key: str) -> str | None:
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]*)", style)
    return match.group(1) if match else None


def _relative_luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class ColourDistinctnessTests(unittest.TestCase):
    def _assert_distinct(self, prefix: str) -> None:
        seen: dict[tuple[str, str], str] = {}
        for cid, pair in palette.CORRIDOR_COLORS.items():
            if not cid.startswith(prefix):
                continue
            twin = seen.get(pair)
            if twin is not None and frozenset({cid, twin}) not in palette.INTENTIONAL_COLOR_TWINS:
                self.fail(
                    f"{cid!r} and {twin!r} both render {pair} but are not declared in "
                    "palette.INTENTIONAL_COLOR_TWINS — either give one a distinct colour "
                    "or declare the sharing deliberate"
                )
            seen.setdefault(pair, cid)

    def test_logical_domains(self) -> None:
        self._assert_distinct("domain_")

    def test_security_zones(self) -> None:
        self._assert_distinct("zone_")

    def test_data_flow_phases(self) -> None:
        self._assert_distinct("phase_")

    def test_public_and_dmz_no_longer_collide(self) -> None:
        """Named regression: the internet-facing surface and the perimeter were
        byte-identical, so a reader could not see where the boundary was."""
        self.assertNotEqual(
            palette.CORRIDOR_COLORS["zone_public"],
            palette.CORRIDOR_COLORS["zone_dmz"],
        )

    def test_detect_and_private_no_longer_collide(self) -> None:
        self.assertNotEqual(
            palette.CORRIDOR_COLORS["zone_detect"],
            palette.CORRIDOR_COLORS["zone_private"],
        )

    def test_both_data_flow_saas_shelves_are_distinct(self) -> None:
        """Producer and consumer are opposite ends of an exchange; before, both
        prefix-matched ``phase_saas`` and rendered identically."""
        self.assertNotEqual(
            palette.CORRIDOR_COLORS["phase_saas_producer"],
            palette.CORRIDOR_COLORS["phase_saas_consumer"],
        )

    def test_every_declared_twin_actually_shares_a_colour(self) -> None:
        """Keeps the exclusion list honest: a stale entry would silently widen
        the collision check's blind spot."""
        for pair in palette.INTENTIONAL_COLOR_TWINS:
            a, b = sorted(pair)
            self.assertEqual(
                palette.CORRIDOR_COLORS[a], palette.CORRIDOR_COLORS[b],
                f"{a}/{b} are declared colour twins but no longer share a colour — "
                "drop the INTENTIONAL_COLOR_TWINS entry",
            )


class TrustAxisTests(unittest.TestCase):
    def test_axis_fills_are_mutually_distinct(self) -> None:
        fills = [palette.CORRIDOR_COLORS[z][0] for z in palette.SECURITY_TRUST_ORDER]
        self.assertEqual(len(fills), len(set(fills)), f"trust axis has a repeated fill: {fills}")

    def test_restricted_is_the_visually_strongest(self) -> None:
        """Whatever the hue journey, the regulated zone must be the one that
        pulls the eye — it is the zone a reviewer most needs to notice."""
        lums = {z: _relative_luminance(palette.CORRIDOR_COLORS[z][0])
                for z in palette.SECURITY_TRUST_ORDER}
        self.assertEqual(min(lums, key=lums.get), "zone_restricted")

    def test_off_axis_zones_are_excluded(self) -> None:
        """identity/partner/detect are categorical, not ranks — putting them on
        the axis would imply an ordering they do not have."""
        for zid in ("zone_identity", "zone_partner", "zone_detect"):
            self.assertNotIn(zid, palette.SECURITY_TRUST_ORDER)


class PrefixResolutionTests(unittest.TestCase):
    def test_deduplicated_id_resolves_to_its_base(self) -> None:
        self.assertEqual(
            palette.corridor_colors("domain_core_2"),
            palette.CORRIDOR_COLORS["domain_core"],
        )

    def test_longest_prefix_wins(self) -> None:
        """``phase_saas_producer`` must not be shadowed by ``phase_saas``."""
        self.assertEqual(
            palette.corridor_colors("phase_saas_producer"),
            palette.CORRIDOR_COLORS["phase_saas_producer"],
        )

    def test_unknown_id_resolves_to_nothing(self) -> None:
        self.assertIsNone(palette.corridor_colors("totally_unknown"))

    def test_shelf_detection_handles_deduplicated_ids(self) -> None:
        self.assertTrue(palette.is_shelf("zone_external_saas"))
        self.assertTrue(palette.is_shelf("phase_saas_producer_2"))
        self.assertFalse(palette.is_shelf("zone_public"))


class ShelfBorderSurvivesNormalizationTests(unittest.TestCase):
    """``normalize_corridor_colors`` is authoritative for corridor appearance.
    It used to rewrite only colour, letting a shelf keep whatever border weight
    an upstream style carried — erasing the row-vs-shelf distinction, which
    matters most exactly where colour is shared on purpose (a SaaS shelf and
    the DMZ are both yellow; only the border says one is out-of-band)."""

    def _security_xml(self) -> str:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="External SaaS",
                anchor_id="zone_restricted", leaves=[_leaf("workday")],
            )],
            flows=[Flow(source="dns", target="rds"), Flow(source="rds", target="workday")],
        )
        return normalize_corridor_colors(compile_corridor_ir(ir, page_name="Security"))

    def test_shelf_keeps_dashed_heavier_border(self) -> None:
        style = _style_of(self._security_xml(), "zone_external_saas")
        self.assertEqual(_kv(style, "dashed"), "1")
        self.assertEqual(_kv(style, "strokeWidth"), "2")

    def test_main_row_corridor_is_solid_and_light(self) -> None:
        style = _style_of(self._security_xml(), "zone_public")
        self.assertEqual(_kv(style, "dashed"), "0")
        self.assertEqual(_kv(style, "strokeWidth"), "1")

    def test_normalization_applies_canonical_colour(self) -> None:
        style = _style_of(self._security_xml(), "zone_public")
        fill, stroke = palette.CORRIDOR_COLORS["zone_public"]
        self.assertEqual(_kv(style, "fillColor"), fill)
        self.assertEqual(_kv(style, "strokeColor"), stroke)

    def test_data_flow_saas_shelves_render_differently(self) -> None:
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("db")])],
            anchored=[
                AnchoredCorridor(id="phase_saas_producer", label="Producers",
                                 anchor_id="phase_store", leaves=[_leaf("segment")]),
                AnchoredCorridor(id="phase_saas_consumer", label="Consumers",
                                 anchor_id="phase_store", leaves=[_leaf("looker")]),
            ],
            flows=[Flow(source="segment", target="db"), Flow(source="db", target="looker")],
        )
        xml = normalize_corridor_colors(compile_corridor_ir(ir, page_name="Data flow"))
        self.assertNotEqual(
            _kv(_style_of(xml, "phase_saas_producer"), "fillColor"),
            _kv(_style_of(xml, "phase_saas_consumer"), "fillColor"),
        )


if __name__ == "__main__":
    unittest.main()
