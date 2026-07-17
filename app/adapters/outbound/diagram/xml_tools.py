"""Draw.io XML helpers \u2014 wrap, validate, auto-fix, choose."""

from __future__ import annotations

import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_XML_SIZE = 1_000_000
STRUCTURAL_ATTRS = ("edge", "parent", "source", "target", "vertex", "connectable")
_DIAGRAM_CLOSE_TAGS = frozenset({"root", "mxgraphmodel", "diagram", "mxfile"})
_SHAPE_TYPE_ATTR = re.compile(r'\bshape_type="([^"]+)"')
_UNQUOTED_ATTR = re.compile(
    r'(?<=[\s>])([a-zA-Z_][\w.-]*)=([^\s"\'<>`]+?)(?=\s|/?>|>)',
)
_FENCE_BLOCK = re.compile(
    r"```(?:xml)?\s*(<(?:mxfile|mxGraphModel)[\s\S]*?</(?:mxfile|mxGraphModel)>)\s*```",
    re.IGNORECASE,
)
_ATTR_SPACE = re.compile(
    r'([a-zA-Z_][\w:.-]*="[^"]*")([a-zA-Z_][\w:.-]*=)',
)


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


def _slice_first_diagram(text: str) -> str | None:
    """Return the first well-bounded mxfile/mxGraphModel document, if present."""
    for tag, end in (("<mxfile", "</mxfile>"), ("<mxGraphModel", "</mxGraphModel>")):
        start = text.find(tag)
        if start < 0:
            continue
        stop = text.find(end, start)
        if stop > start:
            return text[start:stop + len(end)].strip()
    return None


def _xml_from_json_obj(data: object) -> str | None:
    """Pull an mxfile/mxGraphModel string out of a JSON object (nested keys OK)."""
    if isinstance(data, str):
        if "<mxfile" in data or "<mxGraphModel" in data:
            return data
        return None
    if isinstance(data, dict):
        for key in ("xml", "diagram", "mxfile", "content", "args"):
            if key not in data:
                continue
            found = _xml_from_json_obj(data[key])
            if found:
                return found
        for val in data.values():
            found = _xml_from_json_obj(val)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _xml_from_json_obj(item)
            if found:
                return found
    return None


def coerce_drawio_xml(text: str) -> str:
    """Normalize JSON envelopes / escaped markup into a single diagram document.

    Handles the common failure where a verbose log blob
    ``{"args": {"xml": "...", "routing": "..."}, "xml_preview": "..."}``
    was saved as ``.drawio``: unescape quotes/newlines, take the first
    ``</mxfile>``, and drop trailing duplicate preview / JSON garbage.
    """
    trimmed = (text or "").strip()
    if not trimmed:
        return trimmed

    candidate = trimmed

    fence = _FENCE_BLOCK.search(candidate)
    if fence:
        candidate = fence.group(1).strip()

    if candidate.startswith("{") or candidate.startswith("["):
        try:
            extracted = _xml_from_json_obj(json.loads(candidate))
            if extracted:
                candidate = extracted.strip()
        except json.JSONDecodeError:
            pass

    # JSON-escaped XML (literal \" and \n) outside a parseable JSON object
    if '\\"' in candidate or "\\n" in candidate:
        if "<mxfile" not in candidate and "<mxGraphModel" not in candidate:
            candidate = (
                candidate.replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )
        elif '=\\"' in candidate:
            candidate = (
                candidate.replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )

    sliced = _slice_first_diagram(candidate)
    if sliced:
        return sliced
    return candidate


def ensure_diagram_document(xml: str, page_name: str = "Architecture") -> str:
    """Guarantee a single-root mxfile/mxGraphModel document for draw.io load."""
    trimmed = coerce_drawio_xml(xml or "")
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

    try:
        if "<mxfile" in graph_xml:
            tree = ET.fromstring(graph_xml)
            root = _find_diagram_root(tree)
            if root is None:
                return "XML parse error: missing <root> in mxfile."
            cells = root.findall("mxCell")
        else:
            parse_target = graph_xml if "<root>" in graph_xml else f"<root>{graph_xml}</root>"
            wrapper = ET.fromstring(parse_target)
            cells = wrapper.findall("mxCell") if wrapper.tag == "root" else list(wrapper.iter("mxCell"))
    except ET.ParseError as exc:
        return f"XML parse error: {exc}"

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


def _id_sort_key(cell_id: str) -> tuple[int, int | str]:
    if cell_id.isdigit():
        return (0, int(cell_id))
    return (1, cell_id)


def _page_size(tree: ET.Element) -> tuple[float, float]:
    """Return (pageWidth, pageHeight) from mxGraphModel, with sensible defaults."""
    model = tree.find(".//mxGraphModel")
    if model is None and tree.tag == "mxGraphModel":
        model = tree
    if model is None:
        return 2400.0, 1400.0
    try:
        w = float(model.get("pageWidth") or 2400)
    except ValueError:
        w = 2400.0
    try:
        h = float(model.get("pageHeight") or 1400)
    except ValueError:
        h = 1400.0
    return w, h


def _is_canvas_background_cell(cell: ET.Element, page_w: float, page_h: float) -> bool:
    """True for full-page opaque backdrop cells that must stay behind all shapes.

    LLMs often emit an unlabeled rectangle covering the page (``fillColor=#F5F5F5``).
    After id assignment + reorder, that cell can end up last among siblings and paint
    *over* the diagram in draw.io (later cells = higher z-order).
    """
    if cell.get("edge") == "1" or cell.get("vertex") != "1":
        return False
    if cell.get("parent") not in (None, "1"):
        return False
    if (cell.get("value") or "").strip():
        return False
    style = cell.get("style") or ""
    if "container=1" in style or "group" in style or "swimlane" in style:
        return False
    geom = cell.find("mxGeometry")
    if geom is None:
        return False
    try:
        w = float(geom.get("width") or 0)
        h = float(geom.get("height") or 0)
        x = float(geom.get("x") or 0)
        y = float(geom.get("y") or 0)
    except ValueError:
        return False
    # Near-origin and covers most of the page (or a very large absolute area)
    covers_page = w >= page_w * 0.8 and h >= page_h * 0.8
    huge = w >= 1500 and h >= 1000
    return x <= 10 and y <= 10 and (covers_page or huge)


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

    page_w, page_h = _page_size(tree)

    cell_map: dict[str, ET.Element] = {}
    used_ids: set[str] = set()
    for cell in cells:
        cid = cell.get("id")
        if cid:
            cell_map[cid] = cell
            used_ids.add(cid)

    next_id = 2
    for existing in used_ids:
        if existing.isdigit():
            next_id = max(next_id, int(existing) + 1)

    def _alloc_id() -> str:
        nonlocal next_id
        while str(next_id) in used_ids:
            next_id += 1
        new_id = str(next_id)
        used_ids.add(new_id)
        next_id += 1
        return new_id

    # Prefer low ids for canvas backgrounds so they sort behind shapes.
    for cell in cells:
        if cell.get("id"):
            continue
        if _is_canvas_background_cell(cell, page_w, page_h):
            # Try id "2" first when free
            if "2" not in used_ids:
                new_id = "2"
                used_ids.add("2")
            else:
                new_id = _alloc_id()
            cell.set("id", new_id)
            cell_map[new_id] = cell
            fixes.append(f"Assigned missing id '{new_id}'")

    for cell in cells:
        if cell.get("id"):
            continue
        new_id = _alloc_id()
        cell.set("id", new_id)
        cell_map[new_id] = cell
        fixes.append(f"Assigned missing id '{new_id}'")

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

    def _z_rank(cell: ET.Element) -> int:
        cid = cell.get("id") or ""
        if cid in ("0", "1"):
            return 0
        if cell.get("edge") == "1":
            return 3
        if _is_canvas_background_cell(cell, page_w, page_h):
            return 1
        return 2

    ordered = sorted(
        cells,
        key=lambda c: (
            _z_rank(c),
            _cell_depth(c.get("id") or "", parent_map),
            _id_sort_key(c.get("id") or ""),
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
        # Only strip non-diagram closers (LLM junk). Keep </root></mxGraphModel></diagram></mxfile>.
        closers = re.findall(r"</([^>\s/]+)>", suffix)
        if (
            closers
            and re.match(r"^(\s*</[^>]+>)+\s*$", suffix)
            and not any(t.lower() in _DIAGRAM_CLOSE_TAGS for t in closers)
        ):
            fixed = fixed[: last_valid + end_offset]
            fixes.append("Stripped trailing LLM wrapper tags")

    stripped, changed = _strip_llm_wrapper_tags(fixed)
    if changed:
        fixes.append("stripped LLM wrapper tags")
        fixed = stripped

    before_amp = fixed
    fixed = re.sub(
        r"&(?!lt;|gt;|amp;|quot;|apos;|#[0-9]+;|#x[0-9a-fA-F]+;)",
        "&amp;",
        fixed,
    )
    if fixed != before_amp:
        fixes.append("Escaped unescaped & characters")

    new_fixed = _UNQUOTED_ATTR.sub(r'\1="\2"', fixed)
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

    spaced = _ATTR_SPACE.sub(r"\1 \2", fixed)
    if spaced != fixed:
        fixes.append("Added missing space between attributes")
        fixed = spaced

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


def normalize_platform_styles(xml: str, platform: str) -> str:
    """Rewrite known bad stencil namespaces for GCP / Azure / multi.

    LLMs often invent ``mxgraph.gcp2020`` (unloaded) or copy AWS ``resourceIcon``
    onto Azure. Fix the high-confidence string mistakes before MCP enrichment.
    """
    if not xml:
        return xml
    platform = (platform or "aws").strip().lower()
    out = xml
    if platform in ("gcp", "multi"):
        if "mxgraph.gcp2020." in out:
            out = out.replace("mxgraph.gcp2020.", "mxgraph.gcp2.")
    if platform in ("azure", "multi"):
        out = re.sub(
            r"shape=mxgraph\.azure2\.resourceIcon;?",
            "",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(
            r"resIcon=mxgraph\.azure2\.[^;\"\s]+;?",
            "",
            out,
            flags=re.IGNORECASE,
        )
    return out


def extract_shape_types(xml: str) -> list[str]:
    """Return unique ``shape_type`` attribute values from draw.io XML."""
    if not xml or "shape_type=" not in xml:
        return []
    return sorted({m.group(1).strip() for m in _SHAPE_TYPE_ATTR.finditer(xml) if m.group(1).strip()})


# Presentation keys the LLM owns on service leaves — never wiped by MCP.
_STYLE_PRESERVE_KEYS = frozenset({
    "verticalLabelPosition",
    "verticalAlign",
    "align",
    "fontSize",
    "fontColor",
    "fontStyle",
    "html",
    "aspect",
    "whiteSpace",
    "labelBackgroundColor",
})


def _parse_style(style: str) -> dict[str, str]:
    """Parse a draw.io style string into an ordered key→value map."""
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, val = part.partition("=")
            key = key.strip()
            if key:
                out[key] = val.strip()
        else:
            # Flag-only tokens (e.g. ``image`` or ``html=1`` already handled)
            out[part] = ""
    return out


def _format_style(parts: dict[str, str]) -> str:
    chunks: list[str] = []
    for key, val in parts.items():
        if val == "":
            chunks.append(key)
        else:
            chunks.append(f"{key}={val}")
    text = ";".join(chunks)
    return text if text.endswith(";") or not text else text + ";"


def merge_drawio_styles(base: str, mcp: str) -> str:
    """Merge MCP stencil style into an LLM placeholder style.

    MCP values win for stencil / fill keys. LLM values are restored for
    presentation keys (label position, fonts, aspect) so icons stay labeled.
    """
    if not (mcp or "").strip():
        return base or ""
    if not (base or "").strip():
        return mcp if mcp.endswith(";") else mcp + ";"

    base_map = _parse_style(base)
    mcp_map = _parse_style(mcp)
    merged = {**base_map, **mcp_map}
    for key in _STYLE_PRESERVE_KEYS:
        if key in base_map:
            merged[key] = base_map[key]
    return _format_style(merged)


def apply_mcp_styles(xml: str, style_overrides: dict[str, str]) -> str:
    """Merge MCP styles onto cells whose ``shape_type`` matches *style_overrides*.

    Uses ElementTree so MCP style strings with ``&`` / quotes are escaped safely.
    Matching ``shape_type`` attributes are removed after a successful merge.
    Cells whose shape_type is missing from *style_overrides* are left unchanged.
    """
    if not style_overrides or not xml:
        return xml

    trimmed = (xml or "").strip()
    is_mxfile = "<mxfile" in trimmed
    parse_src = trimmed
    if not is_mxfile and "<root>" not in trimmed:
        parse_src = f"<root>{trimmed}</root>"

    try:
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return xml

    root = _find_diagram_root(tree) if is_mxfile else tree
    if root is None:
        root = tree.find("root")
    if root is None:
        return xml

    changed = False
    for cell in root.findall("mxCell"):
        st = cell.get("shape_type")
        if not st:
            continue
        resolved = style_overrides.get(st)
        if not resolved:
            # MCP miss — keep placeholder style and shape_type for visibility/debug
            continue
        base_style = cell.get("style") or ""
        cell.set("style", merge_drawio_styles(base_style, resolved))
        del cell.attrib["shape_type"]
        changed = True

    if not changed:
        return xml

    if is_mxfile:
        return ET.tostring(tree, encoding="unicode")
    if "<root>" in trimmed:
        return ET.tostring(root, encoding="unicode")
    return "".join(ET.tostring(c, encoding="unicode") for c in root.findall("mxCell"))
