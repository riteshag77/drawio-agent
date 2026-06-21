"""Draw.io XML helpers inspired by next-ai-draw-io (wrap, validate, auto-fix)."""

from __future__ import annotations

import html
import re
import uuid
import xml.etree.ElementTree as ET


def wrap_with_mxfile(graph_xml: str, page_name: str = "Architecture") -> str:
    """Wrap bare mxGraphModel / mxCell fragments in a full mxfile document."""
    trimmed = (graph_xml or "").strip()
    if not trimmed:
        return (
            '<mxfile host="app.diagrams.net" agent="drawio-agent" version="24.7.7">'
            f'  <diagram id="{uuid.uuid4().hex[:12]}" name="{html.escape(page_name, quote=True)}">'
            '    <mxGraphModel><root><mxCell id="0" /><mxCell id="1" parent="0" /></root></mxGraphModel>'
            "  </diagram>"
            "</mxfile>"
        )

    if "<mxfile" in trimmed:
        return trimmed

    if "<mxGraphModel" in trimmed:
        inner = trimmed
    else:
        inner = trimmed
        if "<root>" in inner:
            inner = re.sub(r"</?root>", "", inner).strip()
        inner = (
            "<mxGraphModel dx=\"1422\" dy=\"794\" grid=\"1\" gridSize=\"10\" guides=\"1\" "
            "tooltips=\"1\" connect=\"1\" arrows=\"1\" fold=\"1\" page=\"1\" pageScale=\"1\" "
            "pageWidth=\"1169\" pageHeight=\"827\" math=\"0\" shadow=\"0\">"
            f"<root><mxCell id=\"0\" /><mxCell id=\"1\" parent=\"0\" />{inner}</root></mxGraphModel>"
        )

    page_id = uuid.uuid4().hex[:12]
    return (
        f'<mxfile host="app.diagrams.net" agent="drawio-agent" version="24.7.7">\n'
        f'  <diagram id="{page_id}" name="{html.escape(page_name, quote=True)}">\n'
        f"    {inner}\n"
        f"  </diagram>\n"
        f"</mxfile>"
    )


def validate_mxcell_structure(graph_xml: str) -> str | None:
    """Return an error message if the mxGraphModel structure is invalid."""
    if not graph_xml or not graph_xml.strip():
        return "Empty diagram XML."

    try:
        root = ET.fromstring(f"<wrapper>{graph_xml}</wrapper>")
    except ET.ParseError as error:
        return f"XML parse error: {error}"

    cells = root.iter("mxCell")
    seen_ids: set[str] = set()
    for cell in cells:
        cell_id = cell.attrib.get("id", "")
        if not cell_id:
            return "mxCell missing id attribute."
        if cell_id in seen_ids:
            return f"Duplicate mxCell id: {cell_id}"
        seen_ids.add(cell_id)

        for child in list(cell):
            if child.tag == "mxCell":
                return f"Nested mxCell detected (id={cell_id}). All mxCells must be siblings."

    return None


def auto_fix_xml(graph_xml: str) -> tuple[str, list[str]]:
    """Apply safe structural fixes before loading into draw.io."""
    fixes: list[str] = []
    fixed = graph_xml

    if "&" in fixed and "&amp;" not in fixed and "&lt;" not in fixed:
        fixed = re.sub(
            r"&(?!amp;|lt;|gt;|quot;|apos;)",
            "&amp;",
            fixed,
        )
        fixes.append("escaped bare ampersands")

    # Remove XML comments (draw.io strips them and they break diffs)
    no_comments = re.sub(r"<!--.*?-->", "", fixed, flags=re.DOTALL)
    if no_comments != fixed:
        fixes.append("removed XML comments")
        fixed = no_comments

    return fixed, fixes


def validate_and_fix_xml(graph_xml: str) -> tuple[str, list[str]]:
    """Validate diagram XML and return a load-safe version."""
    error = validate_mxcell_structure(graph_xml)
    fixed, fixes = auto_fix_xml(graph_xml)
    if error:
        retry_error = validate_mxcell_structure(fixed)
        if retry_error:
            return graph_xml, fixes
        fixes.append(f"recovered from: {error}")
    return fixed, fixes
