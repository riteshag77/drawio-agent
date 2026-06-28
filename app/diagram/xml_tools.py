"""Draw.io XML helpers — port of next-ai-draw-io lib/utils.ts (wrap, validate, auto-fix, edit ops)."""

from __future__ import annotations

import base64
import html
import re
import uuid
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote

from app.models import DiagramOperation

MAX_XML_SIZE = 1_000_000
STRUCTURAL_ATTRS = ("edge", "parent", "source", "target", "vertex", "connectable")
VALID_ENTITIES = frozenset({"lt", "gt", "amp", "quot", "apos"})


@dataclass
class XmlValidationResult:
    valid: bool
    error: str | None = None
    fixed: str | None = None
    fixes: list[str] = field(default_factory=list)

    @property
    def output_xml(self) -> str:
        return self.fixed if self.fixed is not None else ""


@dataclass
class OperationError:
    type: Literal["update", "add", "delete"]
    cell_id: str
    message: str


@dataclass
class ApplyOperationsResult:
    result: str
    errors: list[OperationError] = field(default_factory=list)


def is_mxcell_xml_complete(xml: str | None) -> bool:
    """True if XML ends with a complete mxCell (not truncated mid-stream)."""
    trimmed = (xml or "").strip()
    if not trimmed:
        return False
    last_self = trimmed.rfind("/>")
    last_close = trimmed.rfind("</mxCell>")
    last_valid = max(last_self, last_close)
    if last_valid == -1:
        return False
    end_offset = 9 if last_close > last_self else 2
    suffix = trimmed[last_valid + end_offset :]
    return bool(re.match(r"^(\s*</[^>]+>)*\s*$", suffix))


def extract_complete_mxcells(xml: str | None) -> str:
    """Return only complete mxCell elements from partial/streaming XML."""
    if not xml:
        return ""
    cells: list[tuple[int, str]] = []
    for m in re.finditer(r"<mxCell\s+[^>]*/>", xml):
        cells.append((m.start(), m.group(0)))
    for m in re.finditer(r"<mxCell\s+[^>]*>[\s\S]*?</mxCell>", xml):
        cells.append((m.start(), m.group(0)))
    cells.sort(key=lambda x: x[0])
    seen: set[int] = set()
    unique = []
    for idx, text in cells:
        if idx in seen:
            continue
        seen.add(idx)
        unique.append(text)
    return "\n".join(unique)


def format_xml(xml: str, indent: str = "  ") -> str:
    """Pretty-print XML with indentation."""
    if not xml or not xml.strip():
        return ""
    compact = re.sub(r">\s*<", "><", xml.strip())
    parts = re.split(r"(?<=>)|(?=<)", compact)
    parts = [p for p in parts if p]
    lines: list[str] = []
    pad = 0
    for node in parts:
        if re.match(r"^</\w", node):
            pad = max(0, pad - 1)
            lines.append(indent * pad + node)
        elif re.match(r"^<\w[^>]*/>$", node):
            lines.append(indent * pad + node)
        elif re.match(r"^<\w", node) and not node.endswith("/>"):
            lines.append(indent * pad + node)
            if not re.match(r"^<\?xml", node):
                pad += 1
        elif node.startswith("<"):
            lines.append(indent * pad + node)
        else:
            if lines:
                lines[-1] += node
            else:
                lines.append(node)
    return "\n".join(lines).strip()


def convert_to_legal_xml(xml_string: str) -> str:
    """Extract complete mxCell blocks into a legal <root> fragment."""
    regex = re.compile(r"<mxCell\b[^>]*(?:/>|>([\s\S]*?)</mxCell>)")
    parts = ['<root>\n']
    for match in regex.finditer(xml_string):
        cell = match.group(0)
        if not re.search(r'<Array\s+as="points">', cell):
            cell = re.sub(
                r"<mxPoint\b[^>]*/>",
                lambda m: m.group(0) if re.search(r"\sas=", m.group(0)) else "",
                cell,
            )
        cell = re.sub(
            r"&(?!lt;|gt;|amp;|quot;|apos;|#[0-9]+;|#x[0-9a-fA-F]+;)",
            "&amp;",
            cell,
        )
        if re.search(r'=\s*"[^"]*<[^"]*"', cell):
            cell = re.sub(
                r'=\s*"([^"]*)"',
                lambda m: f'="{html.escape(m.group(1), quote=True)}"',
                cell,
            )
        formatted = "\n".join("    " + line.strip() for line in cell.split("\n") if line.strip())
        parts.append(formatted + "\n")
    parts.append("</root>")
    return "".join(parts)


def extract_diagram_xml(svg_or_data: str) -> str:
    """
    Decode draw.io compressed diagram from SVG export data URL or raw base64 payload.
    Port of extractDiagramXML (utils.ts).
    """
    raw = svg_or_data
    if raw.startswith("data:"):
        if "," in raw:
            raw = raw.split(",", 1)[1]
        else:
            raw = raw[26:] if len(raw) > 26 else raw

    if raw.strip().startswith("<"):
        svg_string = raw
    else:
        try:
            svg_string = base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:
            svg_string = raw

    content_match = re.search(r'content="([^"]*)"', svg_string)
    if not content_match:
        content_match = re.search(r"content='([^']*)'", svg_string)
    if not content_match:
        raise ValueError("No content attribute found in SVG export")

    encoded = html.unescape(content_match.group(1))
    diagram_match = re.search(r"<diagram[^>]*>([^<]+)</diagram>", encoded)
    if not diagram_match:
        raise ValueError("No diagram element in encoded content")

    b64_data = diagram_match.group(1).strip()
    binary = base64.b64decode(b64_data)
    decompressed = zlib.decompress(binary, wbits=-15)
    decoded = decompressed.decode("utf-8")
    return unquote(decoded)


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
        return trimmed

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


def _parse_mxcell_fragment(new_xml: str) -> ET.Element:
    wrapped = f"<wrapper>{new_xml.strip()}</wrapper>"
    doc = ET.fromstring(wrapped)
    cell = doc.find("mxCell")
    if cell is None:
        raise ValueError("new_xml must contain an mxCell element")
    return cell


def _collect_descendants(root: ET.Element, cell_id: str) -> set[str]:
    to_delete: set[str] = set()

    def collect(cid: str) -> None:
        if cid in to_delete or cid in ("0", "1"):
            return
        to_delete.add(cid)
        for child in root.findall(f'./mxCell[@parent="{cid}"]'):
            child_id = child.get("id")
            if child_id:
                collect(child_id)

    collect(cell_id)
    for cid in list(to_delete):
        for edge in root.findall(f'./mxCell[@source="{cid}"]'):
            eid = edge.get("id")
            if eid and eid not in ("0", "1"):
                collect(eid)
        for edge in root.findall(f'./mxCell[@target="{cid}"]'):
            eid = edge.get("id")
            if eid and eid not in ("0", "1"):
                collect(eid)
    return to_delete


def apply_diagram_operations(
    xml_content: str,
    operations: list[DiagramOperation],
) -> ApplyOperationsResult:
    """Apply ID-based update/add/delete operations — port of applyDiagramOperations."""
    errors: list[OperationError] = []
    try:
        tree = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        return ApplyOperationsResult(
            result=xml_content,
            errors=[OperationError(type="update", cell_id="", message=f"XML parse error: {exc}")],
        )

    root = _find_diagram_root(tree)
    if root is None:
        return ApplyOperationsResult(
            result=xml_content,
            errors=[OperationError(type="update", cell_id="", message="Could not find <root> element")],
        )

    cell_map: dict[str, ET.Element] = {}
    for cell in root.findall("mxCell"):
        cid = cell.get("id")
        if cid:
            cell_map[cid] = cell

    for op in operations:
        op_type = op.operation
        cell_id = op.cell_id

        if op_type == "update":
            existing = cell_map.get(cell_id)
            if existing is None:
                errors.append(OperationError("update", cell_id, f'Cell with id="{cell_id}" not found'))
                continue
            if not op.new_xml:
                errors.append(OperationError("update", cell_id, "new_xml is required for update"))
                continue
            try:
                new_cell = _parse_mxcell_fragment(op.new_xml)
            except ValueError as exc:
                errors.append(OperationError("update", cell_id, str(exc)))
                continue
            if new_cell.get("id") != cell_id:
                errors.append(
                    OperationError(
                        "update",
                        cell_id,
                        f'ID mismatch: cell_id is "{cell_id}" but new_xml has id="{new_cell.get("id")}"',
                    )
                )
                continue
            idx = list(root).index(existing)
            root.remove(existing)
            root.insert(idx, new_cell)
            cell_map[cell_id] = new_cell

        elif op_type == "add":
            if cell_id in cell_map:
                errors.append(OperationError("add", cell_id, f'Cell with id="{cell_id}" already exists'))
                continue
            if not op.new_xml:
                errors.append(OperationError("add", cell_id, "new_xml is required for add"))
                continue
            try:
                new_cell = _parse_mxcell_fragment(op.new_xml)
            except ValueError as exc:
                errors.append(OperationError("add", cell_id, str(exc)))
                continue
            if new_cell.get("id") != cell_id:
                errors.append(
                    OperationError(
                        "add",
                        cell_id,
                        f'ID mismatch: cell_id is "{cell_id}" but new_xml has id="{new_cell.get("id")}"',
                    )
                )
                continue
            root.append(new_cell)
            cell_map[cell_id] = new_cell

        elif op_type == "delete":
            if cell_id in ("0", "1"):
                errors.append(OperationError("delete", cell_id, f'Cannot delete root cell "{cell_id}"'))
                continue
            if cell_id not in cell_map:
                continue
            for del_id in _collect_descendants(root, cell_id):
                cell = cell_map.get(del_id)
                if cell is not None:
                    root.remove(cell)
                    cell_map.pop(del_id, None)

    result = ET.tostring(tree, encoding="unicode")
    return ApplyOperationsResult(result=result, errors=errors)


def _strip_llm_wrapper_tags(xml: str) -> tuple[str, bool]:
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
    current = parent_map.get(cell_id)
    while current and current in parent_map:
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
        key=lambda c: (_cell_depth(c.get("id") or "", parent_map), c.get("id") or ""),
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
    """Apply safe structural fixes — expanded port of autoFixXml."""
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
    dup_ids: set[str] = set()

    def _rename_dup_id(match: re.Match[str]) -> str:
        cid = match.group(1)
        if cid not in dup_ids:
            count = seen.get(cid, 0)
            seen[cid] = count + 1
            if count == 0:
                return match.group(0)
            dup_ids.add(cid)
            return f'id="{cid}_dup{count}"'
        count = seen.get(cid, 0) + 1
        seen[cid] = count
        return f'id="{cid}_dup{count}"'

    renamed = re.sub(r'\bid="([^"]+)"', _rename_dup_id, fixed)
    if renamed != fixed:
        fixes.append("Renamed duplicate ID(s)")
        fixed = renamed

    struct_fixed, struct_fixes = _fix_xml_with_elementtree(fixed)
    if struct_fixes:
        fixes.extend(struct_fixes)
        fixed = struct_fixed

    return fixed, fixes


def validate_and_fix_xml(graph_xml: str) -> XmlValidationResult:
    struct_fixed, struct_fixes = _fix_xml_with_elementtree(graph_xml)
    struct_error = validate_mxcell_structure(struct_fixed)
    if not struct_error:
        return XmlValidationResult(
            valid=True,
            error=None,
            fixed=struct_fixed if struct_fixes else None,
            fixes=struct_fixes,
        )

    fixed, fixes = auto_fix_xml(graph_xml)
    all_fixes = struct_fixes + [f for f in fixes if f not in struct_fixes]
    retry_error = validate_mxcell_structure(fixed)
    if not retry_error:
        return XmlValidationResult(valid=True, error=None, fixed=fixed, fixes=all_fixes)

    return XmlValidationResult(
        valid=False,
        error=retry_error or struct_error,
        fixed=fixed if all_fixes else None,
        fixes=all_fixes,
    )


def validate_and_fix_xml_legacy(graph_xml: str) -> tuple[str, list[str]]:
    result = validate_and_fix_xml(graph_xml)
    output = result.fixed if result.fixed is not None else graph_xml
    if result.valid and not result.fixed:
        output = graph_xml
    return output, result.fixes
