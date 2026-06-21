"""Draw.io XML helpers inspired by next-ai-draw-io (wrap, validate, auto-fix)."""

from __future__ import annotations

import html
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


MAX_XML_SIZE = 1_000_000


@dataclass
class XmlValidationResult:
    valid: bool
    error: str | None = None
    fixed: str | None = None
    fixes: list[str] = field(default_factory=list)

    @property
    def output_xml(self) -> str:
        """Best XML to load — fixed version when available."""
        return self.fixed if self.fixed is not None else ""


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
            '<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="1169" pageHeight="827" math="0" shadow="0">'
            f'<root><mxCell id="0" /><mxCell id="1" parent="0" />{inner}</root></mxGraphModel>'
        )

    page_id = uuid.uuid4().hex[:12]
    return (
        f'<mxfile host="app.diagrams.net" agent="drawio-agent" version="24.7.7">\n'
        f'  <diagram id="{page_id}" name="{html.escape(page_name, quote=True)}">\n'
        f"    {inner}\n"
        f"  </diagram>\n"
        f"</mxfile>"
    )


def _strip_llm_wrapper_tags(xml: str) -> tuple[str, bool]:
    """Remove common LLM wrapper tags that break draw.io parsing."""
    fixed = xml
    changed = False
    for tag in ("mxfile", "diagram", "mxGraphModel", "root"):
        pattern = re.compile(rf"</?{tag}[^>]*>", re.IGNORECASE)
        new = pattern.sub("", fixed)
        if new != fixed:
            changed = True
            fixed = new
    return fixed.strip(), changed


def validate_mxcell_structure(graph_xml: str) -> str | None:
    """Return an error message if the mxGraphModel structure is invalid."""
    if not graph_xml or not graph_xml.strip():
        return "Empty diagram XML."

    if len(graph_xml) > MAX_XML_SIZE:
        return f"XML exceeds maximum size ({MAX_XML_SIZE} bytes)."

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

    stripped, changed = _strip_llm_wrapper_tags(fixed)
    if changed:
        fixes.append("stripped LLM wrapper tags")
        fixed = stripped

    if "&" in fixed and "&amp;" not in fixed and "&lt;" not in fixed:
        fixed = re.sub(
            r"&(?!amp;|lt;|gt;|quot;|apos;)",
            "&amp;",
            fixed,
        )
        fixes.append("escaped bare ampersands")

    no_comments = re.sub(r"<!--.*?-->", "", fixed, flags=re.DOTALL)
    if no_comments != fixed:
        fixes.append("removed XML comments")
        fixed = no_comments

    # Remove duplicate structural attributes on mxCell tags (common LLM mistake)
    def _dedupe_attrs(match: re.Match[str]) -> str:
        tag = match.group(0)
        for attr in ("edge", "parent", "source", "target", "vertex", "connectable"):
            parts = re.findall(rf'\s{attr}="[^"]*"', tag)
            if len(parts) > 1:
                tag = re.sub(rf'\s{attr}="[^"]*"', "", tag, count=len(parts) - 1)
        return tag

    deduped = re.sub(r"<mxCell[^>]*/?>", _dedupe_attrs, fixed)
    if deduped != fixed:
        fixes.append("removed duplicate structural attributes")
        fixed = deduped

    return fixed, fixes


def validate_and_fix_xml(graph_xml: str) -> XmlValidationResult:
    """
    Validate diagram XML and attempt auto-fix — mirrors next-ai-draw-io validateAndFixXml().
    """
    error = validate_mxcell_structure(graph_xml)
    if not error:
        return XmlValidationResult(valid=True, fixed=None, fixes=[])

    fixed, fixes = auto_fix_xml(graph_xml)
    retry_error = validate_mxcell_structure(fixed)

    if not retry_error:
        return XmlValidationResult(valid=True, error=None, fixed=fixed, fixes=fixes)

    return XmlValidationResult(
        valid=False,
        error=retry_error,
        fixed=fixed if fixes else None,
        fixes=fixes,
    )


def validate_and_fix_xml_legacy(graph_xml: str) -> tuple[str, list[str]]:
    """Backward-compatible helper returning (xml, fixes) for draftsman."""
    result = validate_and_fix_xml(graph_xml)
    output = result.fixed if result.fixed is not None else graph_xml
    if result.valid and not result.fixed:
        output = graph_xml
    return output, result.fixes
