"""Draw.io XML helpers \u2014 wrap, validate, auto-fix, choose."""

from __future__ import annotations

import html
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_XML_SIZE = 1_000_000
STRUCTURAL_ATTRS = ("edge", "parent", "source", "target", "vertex", "connectable")


@dataclass
class XmlValidationResult:
    valid: bool
    error: str | None = None
    fixed: str | None = None
    fixes: list[str] = field(default_factory=list)

    @property
    def output_xml(self) -> str:
        return self.fixed if self.fixed is not None else ""


def wrap_with_mxfile(graph_xml: str, page_name: str = "Architecture") -> str:
    """Wrap bare mxCell / mxGraphModel fragments in a full mxfile document."""
    trimmed = (graph_xml or "").strip()
    root_cells = '<mxCell id="0"/><mxCell id="1" parent="0"/>'

    if not trimmed:
        return (
            f'<mxfile host="embed.diagrams.net"><diagram name="Page-1" id="page-1">'
            f"<mxGraphModel><root>{root_cells}</root></mxGraphModel></diagram></mxfile>"
        )

    if "<mxfile" in trimmed:
        # Return as-is only when the document parses cleanly.
        try:
            ET.fromstring(trimmed)
            return trimmed
        except ET.ParseError:
            # Malformed mxfile — try to extract inner content and re-wrap.
            diag = re.search(r'<diagram[^>]*>(.*?)</diagram>', trimmed, re.DOTALL)
            if diag:
                return wrap_with_mxfile(diag.group(1).strip(), page_name)
            model = re.search(r'<mxGraphModel[^>]*>(.*?)</mxGraphModel>', trimmed, re.DOTALL)
            if model:
                return wrap_with_mxfile(model.group(1).strip(), page_name)
            # Strip all container tags and re-wrap the remaining content.
            stripped = re.sub(
                r'</?(?:mxfile|diagram|mxGraphModel|root)[^>]*>', '', trimmed
            )
            return wrap_with_mxfile(stripped.strip(), page_name)

    if "<mxGraphModel" in trimmed:
        inner = trimmed
    else:
        content = trimmed
        if "<root>" in content:
            content = re.sub(r"</?root>", "", content).strip()
        content = re.sub(
            r'<mxCell[^>]*\bid=["\']0["\'][^>]*(?:/>|></mxCell>)',
            "",
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(
            r'<mxCell[^>]*\bid=["\']1["\'][^>]*(?:/>|></mxCell>)',
            "",
            content,
            flags=re.IGNORECASE,
        ).strip()
        inner = (
            '<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="1169" pageHeight="827" math="0" shadow="0">'
            f"<root>{root_cells}{content}</root></mxGraphModel>"
        )

    page_id = uuid.uuid4().hex[:12]
    safe_name = html.escape(page_name, quote=True)
    return (
        f'<mxfile host="embed.diagrams.net">\n'
        f'  <diagram name="{safe_name}" id="{page_id}">\n'
        f"    {inner}\n"
        f"  </diagram>\n"
        f"</mxfile>"
    )


def _find_diagram_root(tree: ET.Element) -> ET.Element | None:
    for root in tree.iter("root"):
        return root
    return None


def _strip_llm_wrapper_tags(xml: str) -> tuple[str, bool]:
    """Remove spurious LLM wrapper tags only when the document does not already parse.

    Never strip a well-formed ``mxfile`` / ``mxGraphModel`` — that leaves bare
    ``mxCell`` siblings, which draw.io rejects as "Extra content at the end of
    the document" (line 2, column 5).
    """
    trimmed = (xml or "").strip()
    if not trimmed:
        return trimmed, False

    # Prefer an embedded well-formed diagram if LLM prefixed junk
    for tag, end in (("<mxfile", "</mxfile>"), ("<mxGraphModel", "</mxGraphModel>")):
        start = trimmed.find(tag)
        if start < 0:
            continue
        stop = trimmed.find(end, start)
        if stop < 0:
            continue
        candidate = trimmed[start:stop + len(end)].strip()
        try:
            ET.fromstring(candidate)
            return candidate, candidate != trimmed
        except ET.ParseError:
            continue

    try:
        if "<mxfile" in trimmed:
            ET.fromstring(trimmed)
            return trimmed, False
        if re.match(r"\s*<mxGraphModel[\s>]", trimmed, flags=re.IGNORECASE):
            ET.fromstring(trimmed)
            return trimmed, False
    except ET.ParseError:
        pass

    fixed = trimmed
    changed = False
    for tag in ("mxfile", "diagram", "mxGraphModel", "root"):
        pattern = re.compile(rf"</?{tag}[^>]*>", re.IGNORECASE)
        new = pattern.sub("", fixed)
        if new != fixed:
            changed = True
            fixed = new
    return fixed.strip(), changed


def _is_diagram_document(xml: str) -> bool:
    text = (xml or "").strip()
    return bool(text) and ("<mxfile" in text or "<mxGraphModel" in text)


def ensure_diagram_document(xml: str, page_name: str = "Architecture") -> str:
    """Guarantee a single-root mxfile/mxGraphModel document for draw.io load."""
    trimmed = (xml or "").strip()
    if not trimmed:
        return wrap_with_mxfile("", page_name)
    if "<mxfile" in trimmed:
        try:
            ET.fromstring(trimmed)
            return trimmed
        except ET.ParseError:
            # Malformed mxfile — let wrap_with_mxfile handle extraction & re-wrap
            return wrap_with_mxfile(trimmed, page_name)
    if re.match(r"\s*<mxGraphModel[\s>]", trimmed, flags=re.IGNORECASE):
        try:
            ET.fromstring(trimmed)
            return wrap_with_mxfile(trimmed, page_name)
        except ET.ParseError:
            pass
    return wrap_with_mxfile(trimmed, page_name)


def validate_mxcell_structure(graph_xml: str) -> str | None:
    if not graph_xml or not graph_xml.strip():
        return "Empty diagram XML."
    if len(graph_xml) > MAX_XML_SIZE:
        return f"XML exceeds maximum size ({MAX_XML_SIZE} bytes)."

    parse_target = graph_xml
    if "<mxfile" in graph_xml:
        try:
            tree = ET.fromstring(graph_xml)
            root = _find_diagram_root(tree)
            if root is not None:
                parse_target = "".join(ET.tostring(c, encoding="unicode") for c in root.findall("mxCell"))
        except ET.ParseError as exc:
            return f"XML parse error: {exc}"

    try:
        wrapper = ET.fromstring(f"<wrapper>{parse_target}</wrapper>")
    except ET.ParseError as error:
        return f"XML parse error: {error}"

    seen_ids: set[str] = set()
    for cell in wrapper.iter("mxCell"):
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


def _cell_depth(cell_id: str, parent_map: dict[str, str | None]) -> int:
    depth = 0
    seen: set[str] = set()
    current = parent_map.get(cell_id)
    while current and current in parent_map:
        if current in seen:
            break
        seen.add(current)
        depth += 1
        current = parent_map.get(current)
    return depth


def _fix_xml_with_elementtree(graph_xml: str) -> tuple[str, list[str]]:
    """Fix missing geometry, orphan parents, and parent-before-child ordering."""
    fixes: list[str] = []
    trimmed = (graph_xml or "").strip()
    if not trimmed or "<mxCell" not in trimmed:
        return graph_xml, fixes

    is_mxfile = "<mxfile" in trimmed
    parse_src = trimmed
    if not is_mxfile and "<root>" not in trimmed:
        parse_src = f"<root>{trimmed}</root>"

    try:
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return graph_xml, fixes

    root = _find_diagram_root(tree) if is_mxfile else tree
    if root is None:
        root = tree.find("root")
    if root is None:
        return graph_xml, fixes

    cells = root.findall("mxCell")
    if not cells:
        return graph_xml, fixes

    cell_map: dict[str, ET.Element] = {}
    for cell in cells:
        cid = cell.get("id")
        if cid:
            cell_map[cid] = cell

    for cell in cells:
        cid = cell.get("id") or ""
        parent = cell.get("parent")
        if parent and parent not in cell_map and parent not in ("0", "1"):
            cell.set("parent", "1")
            fixes.append(f"Re-parented orphan cell '{cid}' to canvas")

        is_vertex = cell.get("vertex") == "1"
        is_edge = cell.get("edge") == "1"
        if is_vertex and not is_edge:
            geom = cell.find("mxGeometry")
            if geom is None:
                geom = ET.SubElement(cell, "mxGeometry")
                geom.set("x", "0")
                geom.set("y", "0")
                geom.set("width", "60")
                geom.set("height", "60")
                geom.set("as", "geometry")
                fixes.append(f"Added missing mxGeometry for vertex '{cid}'")
            elif geom.get("as") != "geometry":
                geom.set("as", "geometry")
                fixes.append(f"Set as=geometry on cell '{cid}'")

    parent_map = {cid: cell.get("parent") for cid, cell in cell_map.items()}
    ordered = sorted(
        cells,
        key=lambda c: (
            1 if c.get("edge") == "1" else 0,
            _cell_depth(c.get("id") or "", parent_map),
            c.get("id") or "",
        ),
    )
    if [c.get("id") for c in cells] != [c.get("id") for c in ordered]:
        for cell in cells:
            root.remove(cell)
        for cell in ordered:
            root.append(cell)
        fixes.append("Reordered cells so parents appear before children")

    if is_mxfile:
        output = ET.tostring(tree, encoding="unicode")
    elif "<root>" in trimmed:
        output = ET.tostring(root, encoding="unicode")
    else:
        output = "".join(ET.tostring(c, encoding="unicode") for c in ordered)

    return output, fixes


def auto_fix_xml(graph_xml: str) -> tuple[str, list[str]]:
    """Apply safe structural fixes \u2014 expanded port of autoFixXml."""
    fixes: list[str] = []
    fixed = graph_xml

    if re.search(r'=\\"', fixed):
        fixed = fixed.replace('\\"', '"').replace("\\n", "\n")
        fixes.append("Fixed JSON-escaped XML")

    if re.match(r"^\s*<!\[CDATA\[", fixed):
        fixed = re.sub(r"^\s*<!\[CDATA\[", "", fixed)
        fixed = re.sub(r"\]\]>\s*$", "", fixed)
        fixes.append("Removed CDATA wrapper")

    last_self = fixed.rfind("/>")
    last_close = fixed.rfind("</mxCell>")
    last_valid = max(last_self, last_close)
    if last_valid != -1:
        end_offset = 9 if last_close > last_self else 2
        suffix = fixed[last_valid + end_offset :]
        if re.match(r"^(\s*</[^>]+>)+\s*$", suffix):
            fixed = fixed[: last_valid + end_offset]
            fixes.append("Stripped trailing LLM wrapper tags")

    stripped, changed = _strip_llm_wrapper_tags(fixed)
    if changed:
        fixes.append("stripped LLM wrapper tags")
        fixed = stripped

    fixed = re.sub(
        r"&(?!lt;|gt;|amp;|quot;|apos;|#[0-9]+;|#x[0-9a-fA-F]+;)",
        "&amp;",
        fixed,
    )
    if "&amp;" in fixed and "escaped bare ampersands" not in fixes:
        fixes.append("Escaped unescaped & characters")

    # Fix unquoted attribute values: shape_type=aws_lambda -> shape_type="aws_lambda"
    _unquoted = re.compile(
        r'(?<=[\s>])([a-zA-Z_][\w.-]*)=([^\s"\'<>`]+?)(?=\s|/?>|>)',
    )
    new_fixed = _unquoted.sub(r'\1="\2"', fixed)
    if new_fixed != fixed:
        fixes.append("Quoted bare attribute values")
        fixed = new_fixed

    for wrong, right in (
        (r"<Cell(\s)", r"<mxCell\1"),
        (r"<Cell>", "<mxCell>"),
        (r"</Cell>", "</mxCell>"),
        (r"</mxcell>", "</mxCell>"),
        (r"</mxgeometry>", "</mxGeometry>"),
        (r"</mxpoint>", "</mxPoint>"),
    ):
        new = re.sub(wrong, right, fixed, flags=re.IGNORECASE)
        if new != fixed:
            fixes.append(f"Fixed tag typo {wrong}")
            fixed = new

    def _dedupe_attrs(match: re.Match[str]) -> str:
        tag = match.group(0)
        for attr in STRUCTURAL_ATTRS:
            parts = re.findall(rf'\s{attr}="[^"]*"', tag)
            if len(parts) > 1:
                tag = re.sub(rf'\s{attr}="[^"]*"', "", tag, count=len(parts) - 1)
        return tag

    deduped = re.sub(r"<mxCell[^>]*/?>", _dedupe_attrs, fixed)
    if deduped != fixed:
        fixes.append("Removed duplicate structural attributes")
        fixed = deduped

    fixed = re.sub(r'("[^"]*")([a-zA-Z][a-zA-Z0-9_:-]*=)', r"\1 \2", fixed)
    if "Added missing space between attributes" not in fixes:
        if re.search(r'("[^"]*")([a-zA-Z][a-zA-Z0-9_:-]*=)', graph_xml):
            fixes.append("Added missing space between attributes")

    seen: dict[str, int] = {}
    has_dup = False

    def _count_dup_id(match: re.Match[str]) -> str:
        nonlocal has_dup
        cid = match.group(1)
        count = seen.get(cid, 0)
        seen[cid] = count + 1
        if count > 0:
            has_dup = True
        return match.group(0)

    re.sub(r'\bid="([^"]+)"', _count_dup_id, fixed)
    if has_dup:
        # Reject duplicate IDs rather than renaming without rewriting parent/source/target refs
        return ensure_diagram_document(fixed), fixes + [
            "Duplicate mxCell id(s) detected — reject rather than silent rename"
        ]

    struct_fixed, struct_fixes = _fix_xml_with_elementtree(fixed)
    if struct_fixes:
        fixes.extend(struct_fixes)
        fixed = struct_fixed

    return ensure_diagram_document(fixed), fixes


def validate_and_fix_xml(graph_xml: str) -> XmlValidationResult:
    struct_fixed, struct_fixes = _fix_xml_with_elementtree(graph_xml)
    if struct_fixed and struct_fixes:
        struct_fixed = ensure_diagram_document(struct_fixed)
    struct_error = validate_mxcell_structure(struct_fixed)
    if not struct_error:
        return XmlValidationResult(
            valid=True,
            error=None,
            fixed=struct_fixed if struct_fixes else None,
            fixes=struct_fixes,
        )

    fixed, fixes = auto_fix_xml(graph_xml)
    fixed = ensure_diagram_document(fixed)
    all_fixes = struct_fixes + [f for f in fixes if f not in struct_fixes]
    if fixed != (graph_xml or "").strip() and "Re-wrapped as mxfile document" not in all_fixes:
        if not _is_diagram_document(graph_xml or "") or "<mxfile" not in (graph_xml or ""):
            all_fixes.append("Re-wrapped as mxfile document")
    retry_error = validate_mxcell_structure(fixed)
    if not retry_error:
        return XmlValidationResult(valid=True, error=None, fixed=fixed, fixes=all_fixes)

    # Never hand bare mxCells to the client — keep a document shell even when invalid
    return XmlValidationResult(
        valid=False,
        error=retry_error or struct_error,
        fixed=fixed if all_fixes else ensure_diagram_document(graph_xml or ""),
        fixes=all_fixes,
    )


def validate_and_fix_xml_legacy(graph_xml: str) -> tuple[str, list[str]]:
    result = validate_and_fix_xml(graph_xml)
    output = result.fixed if result.fixed is not None else graph_xml
    if result.valid and not result.fixed:
        output = graph_xml
    if not _is_diagram_document(output):
        output = ensure_diagram_document(output)
    return output, result.fixes


def choose_fixed_xml(original: str, result: XmlValidationResult) -> str:
    """Prefer a fixed document only when it remains a loadable diagram file."""
    candidate = result.fixed if result.fixed is not None else original
    if _is_diagram_document(candidate):
        try:
            ET.fromstring(candidate if "<mxfile" in candidate or candidate.lstrip().startswith("<mxGraphModel") else f"<root>{candidate}</root>")
            return ensure_diagram_document(candidate)
        except ET.ParseError:
            pass
    return ensure_diagram_document(original)
