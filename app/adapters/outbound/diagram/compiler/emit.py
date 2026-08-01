"""mxCell emission — the only place that writes draw.io XML syntax.

Built with ElementTree rather than string concatenation so escaping of labels
is handled by the serializer. A user-supplied service label containing ``&`` or
``<`` is a routine occurrence, and hand-built XML is exactly where that becomes
an unparseable document.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


class CellWriter:
    """Accumulates mxCells into a draw.io document."""

    def __init__(self, page_name: str = "Network") -> None:
        self.page_name = page_name
        self._root = ET.Element("root")
        ET.SubElement(self._root, "mxCell", {"id": "0"})
        ET.SubElement(self._root, "mxCell", {"id": "1", "parent": "0"})

    def vertex(
        self,
        *,
        id: str,
        label: str,
        style: str,
        x: float,
        y: float,
        w: float,
        h: float,
        parent: str,
        shape_type: str | None = None,
    ) -> None:
        attrs = {
            "id": id,
            "value": label,
            "style": style,
            "vertex": "1",
            "parent": parent,
        }
        # shape_type is the contract with the MCP icon resolver — it reads this
        # attribute to look up the official stencil and merge it into `style`.
        if shape_type:
            attrs["shape_type"] = shape_type
        cell = ET.SubElement(self._root, "mxCell", attrs)
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": _num(x),
                "y": _num(y),
                "width": _num(w),
                "height": _num(h),
                "as": "geometry",
            },
        )

    def mark_layout_authoritative(self) -> None:
        """Flag this page's geometry as already final (Security/Data-flow's
        layered corridor compiler path) — ``xml_diagnostics.repair_xml``
        checks this on the default-layer cell (``id="1"``) and skips its
        corridor/shelf/leaf re-layout pass for pages carrying it, since that
        pass exists to fix LLM-guessed geometry and this page has none."""
        for cell in self._root:
            if cell.get("id") == "1":
                cell.set("layout", "ir")
                return

    def edge(
        self,
        *,
        id: str,
        source: str,
        target: str,
        label: str,
        style: str,
        points: list[tuple[float, float]] | None = None,
    ) -> None:
        """Emit an edge. ``points`` (if given) are intermediate bend/elbow
        points only — never the start/end, which draw.io always derives from
        ``source``/``target`` — written as the standard manual-waypoints
        ``Array as="points"`` of ``mxPoint``, the same convention
        ``placement_fixers._set_edge_waypoints`` already uses for the
        direct-XML repair path's routed edges."""
        cell = ET.SubElement(
            self._root,
            "mxCell",
            {
                "id": id,
                "value": label,
                "style": style,
                "edge": "1",
                "parent": "1",
                "source": source,
                "target": target,
            },
        )
        geom = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        if points:
            arr = ET.SubElement(geom, "Array", {"as": "points"})
            for px, py in points:
                ET.SubElement(arr, "mxPoint", {"x": _num(px), "y": _num(py)})

    def to_xml(self, *, page_width: float, page_height: float) -> str:
        mxfile = ET.Element("mxfile", {"host": "app.diagrams.net"})
        diagram = ET.SubElement(mxfile, "diagram", {"name": self.page_name, "id": self.page_name.lower()})
        model = ET.SubElement(
            diagram,
            "mxGraphModel",
            {
                "dx": "1422",
                "dy": "798",
                "grid": "0",
                "gridSize": "10",
                "guides": "1",
                "tooltips": "1",
                "connect": "1",
                "arrows": "1",
                "fold": "1",
                "page": "1",
                "pageScale": "1",
                "pageWidth": _num(page_width),
                "pageHeight": _num(page_height),
                "math": "0",
                "shadow": "0",
            },
        )
        model.append(self._root)
        return ET.tostring(mxfile, encoding="unicode")


def _num(value: float) -> str:
    """Render a coordinate without a pointless trailing ``.0``."""
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return str(rounded)
