"""mxCell emission — the only place that writes draw.io XML syntax.

Built with ElementTree rather than string concatenation so escaping of labels
is handled by the serializer. A user-supplied service label containing ``&`` or
``<`` is a routine occurrence, and hand-built XML is exactly where that becomes
an unparseable document.
"""

from __future__ import annotations

import json
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
        criticality: str | None = None,
        tier: str | None = None,
        actor_kind: str | None = None,
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
        # tier ("public"/"app"/"data") on a subnet container is what lets a
        # reader of the finished XML tell a data subnet from a public one.
        # Everything else that distinguishes them — stroke colour, the "Data
        # Subnet" label — is presentation, and reading topology back out of
        # presentation is how an assessment breaks the first time a palette or
        # a label changes. Analysis (well_architected.topology) reads this.
        if tier:
            attrs["tier"] = tier
        # actor_kind marks what sits *outside* the architecture's boundary.
        # Analysis needs to tell "a user on the internet" from "a service in a
        # subnet" to say anything about ingress at all, and the only other
        # signals are the actor's stencil and the fact that it has no container
        # — both accidents of rendering rather than statements of role.
        if actor_kind:
            attrs["actor_kind"] = actor_kind
        # Stamped only when non-default: "standard" is the overwhelming common
        # case, and an attribute that's always present either way is noise a
        # reader of the raw XML has to filter past to find the leaves that
        # actually matter.
        if criticality and criticality != "standard":
            attrs["criticality"] = criticality
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
        """Flag this page's geometry as compiler-final (``layout="ir"``).

        ``xml_tools.fit_containers_to_children`` skips reflow when set.
        ``repair_xml`` still applies structural hygiene and edge ports; it no
        longer re-lays out corridors. Surgical edits clear this flag.
        """
        for cell in self._root:
            if cell.get("id") == "1":
                cell.set("layout", "ir")
                return

    def set_business_notes(self, notes: list[str]) -> None:
        """Stamp the IR's LLM-authored business bullets on the page root (id="1"),
        JSON-encoded, same "custom attribute rides through the pipeline
        untouched" convention as ``shape_type``/``criticality`` above.

        Read back by ``architecture_docs._read_business_notes`` once the page
        is compiled — ``synthesize_ir_overview_body`` never sees the live IR
        object, only compiled XML, so this is the only way the LLM's own
        wording reaches the overview panel instead of a heuristic guess at it.
        No-ops on an empty list so pages that never populate ``business_notes``
        (Security/Data-flow today) don't carry a pointless empty attribute.
        """
        if not notes:
            return
        for cell in self._root:
            if cell.get("id") == "1":
                cell.set("business_notes", json.dumps(notes))
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
        classification: str | None = None,
        protection: str | None = None,
    ) -> None:
        """Emit an edge. ``points`` (if given) are intermediate bend/elbow
        points only — never the start/end, which draw.io always derives from
        ``source``/``target`` — written as the standard manual-waypoints
        ``Array as="points"`` of ``mxPoint``, the same convention
        ``edge_routing._set_edge_waypoints`` already uses for the
        direct-XML repair path's routed edges.

        ``classification``/``protection`` are stamped as plain attributes
        (same "custom attribute" convention as ``shape_type``) only when not
        the default ("none") — a reader of the raw XML only sees them on the
        flows that actually carry a real classification or protection."""
        attrs = {
            "id": id,
            "value": label,
            "style": style,
            "edge": "1",
            "parent": "1",
            "source": source,
            "target": target,
        }
        if classification and classification != "none":
            attrs["classification"] = classification
        if protection and protection != "none":
            attrs["protection"] = protection
        cell = ET.SubElement(self._root, "mxCell", attrs)
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
