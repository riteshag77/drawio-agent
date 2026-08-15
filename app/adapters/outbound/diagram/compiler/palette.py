"""The one colour palette every non-cloud diagram draws from.

Extracted from ``chart_theme`` once flow and sequence diagrams grew colour of
their own: three modules picking their own hues is how a product ends up with
a chart, a flowchart and a sequence diagram that look like they came from
three different tools. Everything here derives from a single validated
categorical order, so adding a view means choosing *slots*, never new hex.

## Why these hues

``SERIES`` is the data-viz reference categorical palette, assigned in fixed
slot order and never cycled. Validated against this app's actual chart surface
— pure white, since ``static/js/diagram.js`` pins the embed canvas to
``#ffffff`` regardless of editor theme:

    node scripts/validate_palette.js \\
      "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300" --mode light --surface "#ffffff"

    [PASS] Lightness band      all 6 inside L 0.43-0.77
    [PASS] Chroma floor        all 6 >= 0.1
    [PASS] CVD separation      worst adjacent aqua<->yellow dE 9.1 (protan)
    [PASS] Normal-vision floor worst adjacent magenta<->yellow dE 19.6
    [WARN] Contrast vs surface aqua/yellow/magenta below 3:1 - relief required

The WARN is discharged wherever these are used as *marks* (charts ship a legend
row and a direct value label per bar/slice). Used as a **fill behind text** —
flow nodes, sequence phase bands — the hue is tinted toward white first, which
raises contrast rather than lowering it, and the label sits in near-black ink.

Only a light palette exists, deliberately: the canvas is forced white, so there
is no dark surface for a second set of steps to target.
"""

from __future__ import annotations

#: Categorical hues in fixed assignment order. Slot N is always the Nth thing
#: declared — never re-assigned when something is added or removed, so a
#: diagram regenerated with one fewer series keeps every survivor's colour.
SERIES: tuple[str, ...] = (
    "#2A78D6",  # blue
    "#EB6834",  # orange
    "#1BAF7A",  # aqua
    "#EDA100",  # yellow
    "#E87BA4",  # magenta
    "#008300",  # green
)

#: Must stay >= ``domain.ir.simple.MAX_SERIES`` and ``MAX_PIE_SLICES``: those
#: caps exist precisely so every series/slice gets its own slot without the
#: palette ever wrapping. Shrinking this tuple without shrinking them would
#: silently hand two series the same colour (``series_color`` clamps).
PALETTE_SLOTS = len(SERIES)

#: How far a fill is mixed toward white before text is placed on it. High
#: enough that near-black ink clears comfortably on every slot — the label, not
#: the box, should be the highest-contrast thing in a node.
FILL_TINT = 0.86
#: A lighter mix again for phase bands, which sit behind *entire* messages
#: rather than behind one short label.
BAND_TINT = 0.90


def series_color(index: int) -> str:
    """Colour for slot ``index``.

    Clamps rather than wrapping. Wrapping would hand two different series the
    same hue, which is the one failure a categorical palette must never
    produce; the IR's own caps mean a clamp is unreachable in practice, and a
    duplicated last colour is at least visibly wrong rather than subtly so.
    """
    if index < 0:
        index = 0
    return SERIES[min(index, len(SERIES) - 1)]


def tint(hex_color: str, amount: float) -> str:
    """Mix ``hex_color`` toward white by ``amount`` (0 = unchanged, 1 = white).

    Used so a fill and its border are provably the same hue: the border is the
    palette slot itself and the fill is that slot lightened, rather than two
    colours someone picked to look similar.
    """
    amount = min(max(amount, 0.0), 1.0)
    channels = (
        int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16),
    )
    mixed = tuple(round(c + (255 - c) * amount) for c in channels)
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def shade(hex_color: str, amount: float) -> str:
    """Mix ``hex_color`` toward black by ``amount`` — the mirror of ``tint``.

    Used where a saturated slot is itself the fill (a sequence participant
    header) and the border has to be a darker step of the same hue rather than
    a second colour chosen by eye.
    """
    amount = min(max(amount, 0.0), 1.0)
    channels = (
        int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16),
    )
    mixed = tuple(round(c * (1.0 - amount)) for c in channels)
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def fill_and_border(index: int, *, band: bool = False) -> tuple[str, str]:
    """``(fill, border)`` for slot ``index`` — a tint of the hue, and the hue."""
    base = series_color(index)
    return tint(base, BAND_TINT if band else FILL_TINT), base
