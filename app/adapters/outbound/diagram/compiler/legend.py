"""Deterministic legend for the corridor views (Logical / Security / Data-flow).

Only describes conventions the specific page actually uses — never a generic
list of everything the visual system *can* express — so the legend can never
claim a convention that isn't in the diagram. Emitted with a placeholder
geometry; ``xml_diagnostics.ensure_legend_position`` (already runs
unconditionally in ``repair_xml``) pins it below the overview panel once the
panel's own final position is known, and ``move_overview_and_legend_to_end``
z-orders it — this module owns content only, not placement.
"""

from __future__ import annotations

from app.domain.ir.corridor import CorridorIR

LEGEND_ID = "legend_box"

LEGEND_STYLE = (
    "rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
    "spacingLeft=16;spacingTop=12;fillColor=#F5F5F5;strokeColor=#B3B3B3;"
    "strokeWidth=1;fontSize=13;"
)
#: Placeholder — ensure_legend_position overwrites x/y, fit_containers-style
#: sizing in repair overwrites height to the real text content.
LEGEND_PLACEHOLDER_W = 340.0
LEGEND_PLACEHOLDER_H = 90.0

#: Below this many rows, a legend is explaining less than it costs in space —
#: a single-convention page doesn't need a key, the label already says it.
_MIN_ROWS = 2


def _rows_for(ir: CorridorIR) -> list[str]:
    rows: list[str] = []

    primary_present = any(f.primary for f in ir.flows)
    secondary_present = any(not f.primary for f in ir.flows)
    if primary_present and secondary_present:
        rows.append("Solid line = primary path &#8226; Dashed line = secondary/async path")

    if ir.anchored:
        kind = "shelf" if len(ir.anchored) == 1 else "shelves"
        rows.append(f"Dashed container border = anchored {kind} (outside the main journey)")

    if ir.view == "security":
        rows.append(
            "Zone colour = trust level: exposed/perimeter (yellow) &#8594; "
            "internal (blue) &#8594; restricted (red)"
        )

    return rows


def build_legend(ir: CorridorIR) -> str | None:
    """HTML body for the legend cell, or ``None`` if there is nothing worth
    explaining (fewer than ``_MIN_ROWS`` rows) — an empty or single-row legend
    is clutter, not information, and this app's whole visual-quality goal is
    a clean canvas."""
    rows = _rows_for(ir)
    if len(rows) < _MIN_ROWS:
        return None
    return "".join(f'<div style="margin-bottom:4px;">{row}</div>' for row in rows)
