"""The palette's colour-science checks, as a gate rather than a comment.

``compiler/palette.py`` records the validator output it was checked against in
its docstring. That was a one-time ritual somebody had to remember: nothing
stopped a seventh hue being appended that collides under protanopia, or a slot
being nudged out of the lightness band, and the failure would have been
invisible until a colour-blind user could not read a chart.

See ``palette_validator.py`` for the port and why it is a port rather than a
shell-out to the JS original.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.compiler.palette import PALETTE_SLOTS, SERIES
from app.domain.ir.simple import MAX_PIE_SLICES, MAX_SERIES
from tests.palette_validator import CVD_TARGET, NORMAL_FLOOR, delta_e, oklch, validate

#: What the canvas actually is. `static/js/diagram.js` pins the embed to white
#: regardless of editor theme, so validating against a softer "paper" surface
#: would be checking a page this app never renders.
SURFACE = "#ffffff"


def _checks(palette: list[str] | None = None) -> dict[str, object]:
    return {c.name: c for c in validate(palette or list(SERIES), surface=SURFACE)}


class ShippedPaletteTests(unittest.TestCase):
    def test_no_check_hard_fails(self) -> None:
        failures = [
            f"{c.name}: {c.detail}" for c in validate(list(SERIES), surface=SURFACE)
            if c.hard_failed
        ]
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_hue_is_inside_the_lightness_band(self) -> None:
        band = _checks()["Lightness band"]
        self.assertEqual(band.state, "pass", band.detail)

    def test_every_hue_clears_the_chroma_floor(self) -> None:
        """Below the floor a hue reads as gray, and a categorical palette with a
        gray slot has one fewer usable colour than it thinks."""
        chroma = _checks()["Chroma floor"]
        self.assertEqual(chroma.state, "pass", chroma.detail)

    def test_adjacent_hues_separate_under_simulated_colour_blindness(self) -> None:
        cvd = _checks()["CVD separation"]
        self.assertEqual(cvd.state, "pass", cvd.detail)

    def test_adjacent_hues_separate_for_full_colour_vision_too(self) -> None:
        """The CVD gate protects dichromat readers; this one protects everyone
        else. It is hard — secondary encoding does not excuse it."""
        normal = _checks()["Normal-vision floor"]
        self.assertEqual(normal.state, "pass", normal.detail)

    def test_contrast_relief_is_exactly_the_three_documented_hues(self) -> None:
        """Sub-3:1 is a *documented conditional relax*, discharged because every
        surface that uses these as fills either labels the mark directly (charts
        ship a value per bar/slice) or tints the hue toward white first and puts
        near-black ink on top (flow nodes, sequence headers). Pinned by hue so
        that a *new* slot falling below 3:1 fails here and has to argue its own
        relief rather than inheriting one."""
        below = [
            hue for hue in SERIES
            if hue in _checks()["Contrast vs surface"].detail
        ]
        self.assertEqual(sorted(below), sorted(["#1BAF7A", "#EDA100", "#E87BA4"]))


class PaletteCapacityTests(unittest.TestCase):
    """The IR's caps exist so the palette never wraps — they have to agree."""

    def test_slots_match_the_series_length(self) -> None:
        self.assertEqual(PALETTE_SLOTS, len(SERIES))

    def test_slots_cover_every_series_and_slice_the_ir_allows(self) -> None:
        self.assertGreaterEqual(PALETTE_SLOTS, MAX_SERIES)
        self.assertGreaterEqual(PALETTE_SLOTS, MAX_PIE_SLICES)


class PortFidelityTests(unittest.TestCase):
    """Evidence the Python port agrees with the JS validator it came from.

    These are the exact figures ``palette.py``'s docstring records from the
    original run. If the colour maths drifts, these move before any pass/fail
    state does — which is the point: a port that silently disagreed would turn
    every check above into a rubber stamp.
    """

    def test_worst_protan_pair_matches_the_recorded_run(self) -> None:
        self.assertAlmostEqual(delta_e("#1BAF7A", "#EDA100", "protan"), 9.1, places=1)

    def test_worst_normal_vision_pair_matches_the_recorded_run(self) -> None:
        self.assertAlmostEqual(delta_e("#EDA100", "#E87BA4"), 19.6, places=1)

    def test_a_known_hue_converts_to_the_expected_oklch(self) -> None:
        lightness, chroma = oklch("#2A78D6")
        self.assertAlmostEqual(lightness, 0.573, places=2)
        self.assertAlmostEqual(chroma, 0.163, places=2)


class ValidatorCatchesRealProblemsTests(unittest.TestCase):
    """The checks above only mean something if they can fail. Each case here is
    a palette a person might plausibly write."""

    def test_a_near_duplicate_hue_fails_the_normal_vision_floor(self) -> None:
        """The most likely way a seventh slot gets added: reach for a colour
        that "looks different" and land next to one already in use.

        Inserted *beside* the blue rather than appended, because the default
        pair list is adjacent-only — a near-duplicate parked at the far end of
        the order is never compared with its twin, which is exactly why slot
        order is part of a categorical palette's correctness and not a
        presentation detail.
        """
        near_duplicate = [SERIES[0], "#2F7FDA", *SERIES[1:]]
        checks = _checks(near_duplicate)
        self.assertEqual(checks["Normal-vision floor"].state, "fail")

    def test_the_same_duplicate_hides_at_the_end_of_the_order(self) -> None:
        """The companion to the above, kept as documentation of the limit:
        adjacent-pair checking cannot see a collision between distant slots.
        A palette used for scatter or map marks needs ``pairs="all"``."""
        appended = [*SERIES, "#2F7FDA"]
        by_name = {c.name: c for c in validate(appended, surface=SURFACE)}
        self.assertEqual(by_name["Normal-vision floor"].state, "pass")
        all_pairs = {c.name: c for c in validate(appended, surface=SURFACE, pairs="all")}
        self.assertEqual(all_pairs["Normal-vision floor"].state, "fail")

    def test_a_desaturated_slot_fails_the_chroma_floor(self) -> None:
        checks = _checks([*SERIES, "#8C8C8C"])
        self.assertEqual(checks["Chroma floor"].state, "fail")

    def test_a_too_dark_slot_fails_the_lightness_band(self) -> None:
        checks = _checks([*SERIES, "#101820"])
        self.assertEqual(checks["Lightness band"].state, "fail")

    def test_thresholds_are_the_documented_ones(self) -> None:
        self.assertEqual(CVD_TARGET, 8.0)
        self.assertEqual(NORMAL_FLOOR, 15.0)


if __name__ == "__main__":
    unittest.main()
