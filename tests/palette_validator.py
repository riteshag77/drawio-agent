"""Python twin of the data-viz skill's ``scripts/validate_palette.js``.

``compiler/palette.py`` documents the validator output it was checked against —
lightness band, chroma floor, CVD separation, normal-vision floor, contrast —
as a *comment*, produced by running the JS validator once by hand. A comment
cannot fail. Nothing stopped a seventh hue being appended that collides under
protanopia, or an existing slot being nudged out of the lightness band; the
palette is a plain tuple in a plain module and the only guard was that somebody
remembered the ritual.

This is that ritual as code. Ported rather than shelled out to on purpose: the
suite must not need Node (the app needs it for ELK, the tests do not), and a
check that silently skips when a runtime is missing is the same as no check.

Constants and formulas are kept in lockstep with the JS original — the
Machado-Oliveira-Fernandes matrices, the OKLab conversion, the thresholds. The
numbers this produces match the ones recorded in ``palette.py``'s docstring,
which is the evidence the port is faithful (see
``test_palette_validation.PortFidelityTests``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ── thresholds (identical to validate_palette.js) ────────────────────────────
BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}  # OKLCH L
CHROMA_FLOOR = 0.10
#: OKLab ΔE×100, min(protan, deutan) over the pair list.
CVD_TARGET = 8.0
CVD_FLOOR = 6.0
#: Worst unsimulated pair. A hard gate: secondary encoding does not excuse it.
NORMAL_FLOOR = 15.0
CONTRAST_MIN = 3.0

#: Machado, Oliveira & Fernandes (2009) at severity 1.0, in linear RGB. The
#: simulation model is part of the calibration, not an implementation detail —
#: swapping in Viénot-1999 moves borderline pairs and would mean recalibrating
#: CVD_TARGET/CVD_FLOOR.
MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def _srgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _linear(hex_color: str) -> tuple[float, float, float]:
    return tuple(_to_linear(c) for c in _srgb(hex_color))  # type: ignore[return-value]


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colors."""
    hi, lo = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _oklab_from_linear(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def oklch(hex_color: str) -> tuple[float, float]:
    """``(L, C)`` — the two coordinates the band and floor checks read."""
    lightness, a, b = _oklab_from_linear(_linear(hex_color))
    return lightness, math.hypot(a, b)


def _simulate(hex_color: str, kind: str) -> tuple[float, float, float]:
    r, g, b = _linear(hex_color)
    matrix = MACHADO[kind]
    return tuple(  # type: ignore[return-value]
        min(1.0, max(0.0, row[0] * r + row[1] * g + row[2] * b)) for row in matrix
    )


def delta_e(a: str, b: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab, ×100. ``kind=None`` is normal vision."""
    first = _oklab_from_linear(_simulate(a, kind) if kind else _linear(a))
    second = _oklab_from_linear(_simulate(b, kind) if kind else _linear(b))
    return 100 * math.dist(first, second)


@dataclass
class Check:
    name: str
    state: str  # "pass" | "fail" | "floor" | "relief"
    detail: str

    @property
    def hard_failed(self) -> bool:
        return self.state == "fail"


def validate(
    palette: list[str], *, mode: str = "light", surface: str = "#ffffff",
    pairs: str = "adjacent",
) -> list[Check]:
    """Run the measurable checks. Mirrors ``validate()`` in the JS original.

    ``pairs="adjacent"`` is right for bars, lines and stacks, where only
    neighbours are compared; ``"all"`` is the stricter list a scatter or a map
    needs, where any two marks can end up side by side.
    """
    low, high = BAND[mode]
    checks: list[Check] = []

    offband = [(c, round(oklch(c)[0], 3)) for c in palette if not low <= oklch(c)[0] <= high]
    checks.append(Check(
        "Lightness band", "fail" if offband else "pass",
        f"outside band: {offband}" if offband else f"all {len(palette)} inside L {low}-{high}",
    ))

    low_chroma = [(c, round(oklch(c)[1], 3)) for c in palette if oklch(c)[1] < CHROMA_FLOOR]
    checks.append(Check(
        "Chroma floor", "fail" if low_chroma else "pass",
        f"below floor (reads gray): {low_chroma}" if low_chroma
        else f"all {len(palette)} >= {CHROMA_FLOOR}",
    ))

    n = len(palette)
    if pairs == "all":
        pairlist = [(i, j) for i in range(n) for j in range(i + 1, n)]
    else:
        pairlist = [(i, i + 1) for i in range(n - 1)]
    label = "all-pairs" if pairs == "all" else "adjacent"

    worst = None
    for kind in ("protan", "deutan"):
        for i, j in pairlist:
            distance = delta_e(palette[i], palette[j], kind)
            if worst is None or distance < worst[0]:
                worst = (distance, kind, palette[i], palette[j])
    tritan = min((delta_e(palette[i], palette[j], "tritan") for i, j in pairlist), default=99.0)
    cvd = worst[0] if worst else 99.0
    checks.append(Check(
        "CVD separation",
        "pass" if cvd >= CVD_TARGET else "floor" if cvd >= CVD_FLOOR else "fail",
        f"worst {label} {worst[3]}<->{worst[2]} dE {cvd:.1f} ({worst[1]}) · tritan {tritan:.1f}"
        if worst else "n/a",
    ))

    normal_worst = None
    for i, j in pairlist:
        distance = delta_e(palette[i], palette[j])
        if normal_worst is None or distance < normal_worst[0]:
            normal_worst = (distance, palette[i], palette[j])
    normal = normal_worst[0] if normal_worst else 99.0
    checks.append(Check(
        "Normal-vision floor", "pass" if normal >= NORMAL_FLOOR else "fail",
        f"worst {label} {normal_worst[2]}<->{normal_worst[1]} dE {normal:.1f} (normal)"
        if normal_worst else "n/a",
    ))

    dim = [(c, round(contrast(c, surface), 2)) for c in palette if contrast(c, surface) < CONTRAST_MIN]
    checks.append(Check(
        "Contrast vs surface", "relief" if dim else "pass",
        f"below {CONTRAST_MIN}:1 — relief required (visible labels or a table view): {dim}"
        if dim else f"all {len(palette)} >= {CONTRAST_MIN}:1",
    ))

    return checks
