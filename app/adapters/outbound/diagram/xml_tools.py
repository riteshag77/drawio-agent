"""Draw.io XML helpers — wrap, validate, auto-fix, choose.

Parse recovery uses **lxml** (``XMLParser(recover=True)``) instead of a growing
pile of hand-rolled regexes. A thin preflight remains for draw.io-specific
cases libxml2 cannot safely infer:

- jammed attributes (``vertex="1"parent="1"``)
- raw HTML inside ``value="..."`` (Overview / Legend) — must be escaped
  *before* recover or the attribute is destroyed

Draw.io semantic repair (nested cells, geometry, parents) stays in
``_fix_xml_with_elementtree``.
"""

from __future__ import annotations

import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_XML_SIZE = 1_000_000
_SHAPE_TYPE_ATTR = re.compile(r'\bshape_type="([^"]+)"')
_FENCE_BLOCK = re.compile(
    r"```(?:xml)?\s*(<(?:mxfile|mxGraphModel)[\s\S]*?</(?:mxfile|mxGraphModel)>)\s*```",
    re.IGNORECASE,
)
# Insert space between adjacent quoted attributes the LLM jammed together.
_ATTR_SPACE = re.compile(
    r'([a-zA-Z_][\w:.-]*="[^"]*")([a-zA-Z_][\w:.-]*=)',
)
# value="..." up to the next real attribute (any name). Needed because Overview
# HTML may contain literal '"' that would truncate a naive match.
_VALUE_ATTR_SPAN = re.compile(
    r'value="(.*?)"(?=\s+[a-zA-Z_][\w:.-]*=")',
    re.DOTALL,
)
_BARE_AMP = re.compile(
    r"&(?!lt;|gt;|amp;|quot;|apos;|#[0-9]+;|#x[0-9a-fA-F]+;)",
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


def extract_page_by_name(xml: str, page_name: str) -> str | None:
    """Scope a multi-page mxfile down to the single ``<diagram>`` named ``page_name``.

    Surgical-edit plumbing (``generate_edit_plan`` / ``apply_diagram_edits``)
    has no concept of multiple pages — it walks ``mxCell``/``root`` assuming a
    single page. Feeding it a full multi-page ``current_xml`` (tab order is
    always Logical first, see ``VIEW_ORDER``) makes it silently read/write
    whichever page happens to be first, not the page the user is editing.
    Callers must scope ``current_xml`` to the active page with this before
    handing it to that code. Returns ``None`` when ``xml`` isn't a multi-page
    mxfile or no page matches ``page_name`` — caller should fall back to using
    ``xml`` as-is (already single-page, or nothing to scope).
    """
    trimmed = (xml or "").strip()
    if "<mxfile" not in trimmed:
        return None
    try:
        tree = ET.fromstring(trimmed)
    except ET.ParseError:
        return None
    diagrams = tree.findall("diagram")
    if len(diagrams) <= 1:
        return None
    target = next(
        (d for d in diagrams if (d.get("name") or "").strip().lower() == (page_name or "").strip().lower()),
        None,
    )
    if target is None:
        return None
    scoped = ET.Element("mxfile", tree.attrib)
    scoped.append(target)
    return ET.tostring(scoped, encoding="unicode")


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

    # Hoist illegally nested <mxCell> children to siblings under <root>.
    nested_found = True
    while nested_found:
        nested_found = False
        for cell in list(root.findall("mxCell")):
            for child in list(cell):
                if child.tag != "mxCell":
                    continue
                nested_found = True
                cell.remove(child)
                if not child.get("parent"):
                    child.set("parent", cell.get("id") or "1")
                root.append(child)
                fixes.append(
                    f"Hoisted nested mxCell '{child.get('id') or '?'}' out of "
                    f"'{cell.get('id') or '?'}'"
                )
        if nested_found:
            cells = root.findall("mxCell")

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


def _escape_drawio_value_markup(xml: str) -> tuple[str, list[str]]:
    """Escape raw ``<``/``>``/``"`` inside ``value="..."`` before XML parse/recover.

    draw.io Overview/Legend cells put HTML in attributes. libxml2 recover
    *destroys* those attributes unless they are well-formed first. Idempotent
    on already-escaped content (no raw ``<``/``"`` left to rewrite).
    """
    fixes: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        cleaned = inner.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        if cleaned != inner:
            fixes.append("Escaped stray markup inside a value attribute")
        return f'value="{cleaned}"'

    return _VALUE_ATTR_SPAN.sub(_repl, xml), fixes


def _recover_xml_with_lxml(xml: str) -> tuple[str | None, list[str]]:
    """Use libxml2 recovery parser to produce well-formed XML.

    Returns ``(recovered_xml, fixes)`` or ``(None, ...)`` if recovery fails.
    """
    try:
        from lxml import etree
    except ImportError:
        return None, ["lxml unavailable — cannot recover malformed XML"]

    try:
        parser = etree.XMLParser(recover=True, huge_tree=True)
        root = etree.fromstring((xml or "").encode("utf-8"), parser=parser)
        if root is None:
            return None, []
        recovered = etree.tostring(root, encoding="unicode")
        # Downstream (and draw.io) use ElementTree — require stdlib parse too.
        ET.fromstring(recovered)
        return recovered, ["Recovered well-formed XML via lxml"]
    except Exception:
        return None, []


def _preflight_drawio_xml(xml: str) -> tuple[str, list[str]]:
    """Tiny draw.io-specific text fixes that libraries cannot infer safely."""
    fixes: list[str] = []
    fixed = xml or ""

    if re.search(r'=\\"', fixed):
        fixed = fixed.replace('\\"', '"').replace("\\n", "\n")
        fixes.append("Fixed JSON-escaped XML")

    if re.match(r"^\s*<!\[CDATA\[", fixed):
        fixed = re.sub(r"^\s*<!\[CDATA\[", "", fixed)
        fixed = re.sub(r"\]\]>\s*$", "", fixed)
        fixes.append("Removed CDATA wrapper")

    spaced = _ATTR_SPACE.sub(r"\1 \2", fixed)
    if spaced != fixed:
        fixes.append("Added missing space between attributes")
        fixed = spaced

    escaped, esc_fixes = _escape_drawio_value_markup(fixed)
    if esc_fixes:
        fixes.extend(dict.fromkeys(esc_fixes))  # dedupe message
        fixed = escaped

    amped = _BARE_AMP.sub("&amp;", fixed)
    if amped != fixed:
        fixes.append("Escaped unescaped & characters")
        fixed = amped

    return fixed, fixes


def auto_fix_xml(graph_xml: str) -> tuple[str, list[str]]:
    """Make LLM/MCP draw.io XML loadable — library recover + thin preflight.

    Prefer **lxml recover** for structural damage (dangling tags, truncated
    cells, mismatched closers). Keep only the draw.io-specific preflight that
    libxml2 gets wrong (jammed attrs, HTML-in-``value``). Then apply
    ElementTree semantic fixes (hoist nested cells, geometry, parents).
    """
    fixes: list[str] = []
    fixed, pre_fixes = _preflight_drawio_xml(graph_xml or "")
    fixes.extend(pre_fixes)

    parseable = False
    try:
        ET.fromstring(fixed if fixed.lstrip().startswith("<") else f"<root>{fixed}</root>")
        parseable = True
    except ET.ParseError:
        parseable = False

    if not parseable:
        recovered, recover_fixes = _recover_xml_with_lxml(fixed)
        if recovered is not None:
            fixes.extend(recover_fixes)
            fixed = recovered
            parseable = True
        else:
            # Last text attempt: slice first mxfile/mxGraphModel and recover that.
            sliced = _slice_first_diagram(fixed)
            if sliced and sliced != fixed:
                recovered, recover_fixes = _recover_xml_with_lxml(sliced)
                if recovered is not None:
                    fixes.append("Extracted embedded diagram before recover")
                    fixes.extend(recover_fixes)
                    fixed = recovered
                    parseable = True

    if parseable:
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

    # Feed the already-ElementTree-fixed content forward rather than the raw
    # original — _fix_xml_with_elementtree only ran once above; auto_fix_xml's
    # own textual pre-fixes are no-ops on already-clean XML, so starting from
    # struct_fixed avoids redoing the same structural repair pass twice.
    fixed, fixes = auto_fix_xml(struct_fixed)
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


def guarantee_loadable_xml(xml: str, page_name: str = "Architecture") -> tuple[str, bool, dict[str, str] | None]:
    """Last-resort circuit breaker before XML leaves the service.

    Re-runs validate/auto_fix (lxml recover + draw.io preflight). If still
    unparseable, degrade to an empty-but-loadable canvas rather than shipping
    something draw.io rejects as "Not a diagram file".

    Returns ``(xml, ok, failure_detail)``.
    """
    candidate = ensure_diagram_document(xml, page_name)
    try:
        ET.fromstring(candidate)
        return candidate, True, None
    except ET.ParseError as exc:
        last_error, last_candidate = str(exc), candidate

    check = validate_and_fix_xml(xml)
    candidate = choose_fixed_xml(xml, check)
    try:
        ET.fromstring(candidate)
        return candidate, True, None
    except ET.ParseError as exc:
        last_error, last_candidate = str(exc), candidate

    return wrap_with_mxfile("", page_name), False, {"parse_error": last_error, "failing_xml": last_candidate}


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


_VENDOR_PREFIXES = ("aws_", "gcp_", "azure_")
_VENDOR_STENCIL_MAP = {"aws4": "aws_", "gcp2": "gcp_", "azure2": "azure_"}
_VENDOR_STENCIL_PATTERN = re.compile(r'mxgraph\.(aws4|gcp2|azure2)\.(\w+)')

#: Two different, narrower jobs than the name suggests — both scoped away
#: from find_service_keys' sources 1/2 (the LLM's own ground-truth key,
#: which must never be rewritten):
#:
#: 1. Genuine stencil-decode oddities (route_53, cache_node, alarm,
#:    data_encryption_key) where draw.io names the icon after what it
#:    depicts, not the service — find_service_keys' source 3 (the
#:    stencil-path fallback, only reached when a resolved leaf's
#:    shape_type attribute is already gone) uses these to normalize onto
#:    the current shape_type vocabulary.
#: 2. The load-balancer/gateway pairs below (aws_application_load_balancer
#:    -> aws_alb, azure_load_balancer -> azure_lb, azure_application_
#:    gateway -> azure_agw, gcp_cloud_load_balancing -> gcp_lb) are consumed
#:    almost entirely by drawio_mcp_client.py's _pick_style, which derives
#:    an acronym-vs-full-name table from this dict to recognize a query
#:    still using an old short form (a pre-catalog-rewrite diagram, a
#:    surgical edit touching legacy content) as relevant to a candidate
#:    stencil described by its full name. Confirmed live: these same four
#:    keys are ALSO the current, correct shape_type vocabulary
#:    (config/references/*.txt) — so when this alias step used to apply to
#:    every source indiscriminately (a separate, now-fixed bug — see
#:    find_service_keys' body), it took an already-correct
#:    shape_type="azure_application_gateway" from source 2 and silently
#:    downgraded it to the retired "azure_agw" on the way out, corrupting
#:    every downstream compliance/cost/WAF check for that leaf. Scoping the
#:    alias step to source 3 only (done in find_service_keys itself) is
#:    what actually fixes that — these four stay here for _pick_style's
#:    sake, and only affect find_service_keys' rare source-3 fallback
#:    (an already-resolved leaf with no shape_type left, e.g. an
#:    externally-uploaded diagram), not this app's own freshly-generated
#:    diagrams, which almost always have shape_type= present.
_SERVICE_KEY_ALIASES = {
    "aws_route_53": "aws_route53",  # Route 53's own stencil name has the underscore
    "aws_cache_node": "aws_elasticache",  # ElastiCache's stencil is named after its node type
    "aws_alarm": "aws_cloudwatch",  # CloudWatch's stencil is literally its Alarm icon
    "aws_cloudwatch_2": "aws_cloudwatch",  # AWS4 ships a "_2" alternate-design variant of several icons
    # KMS's aws4 stencil is named after the icon's visual (a key), not the
    # service — confirmed the ONLY entry ever cached for aws_kms (data/
    # mcp_style_cache.sqlite3). Without this alias, well_architected.py/
    # compliance.py never recognize a KMS node the LLM actually added and
    # MCP actually resolved, and keep reporting "no encryption service"
    # forever — confirmed via a live capture where the user applied this
    # exact fix twice because compliance kept re-flagging it as missing.
    "aws_data_encryption_key": "aws_kms",
    # Load-balancer/gateway acronyms — see the class-2 explanation above.
    "aws_application_load_balancer": "aws_alb",
    "aws_network_load_balancer": "aws_nlb",
    "azure_load_balancer": "azure_lb",
    "azure_application_gateway": "azure_agw",
    "gcp_cloud_load_balancing": "gcp_lb",
}
#: Stencil-path wrapper names, not real services — the regex fallback below
#: matches `mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<real-name>` on
#: BOTH the wrapper ("resourceIcon"/"productIcon") and the real target; only
#: the real target is a service.
_STENCIL_WRAPPER_NAMES = frozenset({"resourceicon", "producticon"})


def find_service_keys(xml: str) -> list[str]:
    """Return unique vendor-prefixed service keys (``aws_``/``gcp_``/``azure_``) referenced in draw.io XML.

    Shared by cost_estimator.py, well_architected.py, and compliance.py — all
    three need the same lookup. Combines three sources, most-trusted first:

    1. ``service="..."`` — the LLM's original semantic key, stamped
       permanently by ``apply_mcp_styles`` once a leaf is resolved. Ground
       truth: never wrong regardless of which vendor stencil MCP picked, so
       nothing downstream needs to guess or alias it.
    2. ``shape_type="..."`` — the same LLM-owned key, but only present before
       resolution (``apply_mcp_styles`` deletes it once a leaf gets a
       ``service=`` in its place); this is what a leaf MCP hasn't gotten to
       yet still carries.
    3. The resolved draw.io stencil path itself, via ``_SERVICE_KEY_ALIASES``
       — the only fallback for XML with neither of the above, e.g. a
       user-uploaded pre-existing diagram that never went through this app's
       own MCP resolution at all. draw.io's own catalog names icons after
       what they depict, not the service (KMS -> "data_encryption_key",
       ElastiCache -> "cache_node", CloudWatch -> "alarm"...), so this path
       needs a growing alias table and can silently under-detect a service
       whose stencil name isn't in it yet — sources 1/2 exist precisely so
       this app's own diagrams never depend on that table being complete.

    A document is routinely a genuine mix of all three — after a partial MCP
    resolution, or a surgical edit that added fresh (unresolved) nodes
    alongside already-resolved ones — so scanning only whichever source
    happens to be present, instead of all of them, silently under-detects
    part of the document.
    """
    # _SERVICE_KEY_ALIASES only belongs on source 3 (the stencil-path
    # fallback) — sources 1/2 are the LLM's own ground-truth key and must
    # never be rewritten. Confirmed live: applying it unconditionally to all
    # three sources (the previous shape here) took an already-correct
    # shape_type="azure_application_gateway" and silently rewrote it to the
    # alias table's *old* short form "azure_agw" on the way out, because
    # that string happens to also be a key in this dict — corrupting the one
    # source the function's own docstring calls "never wrong."
    keys: list[str] = []
    for m in re.finditer(r'\bservice\s*=\s*"([^"]+)"', xml):
        st = m.group(1).strip().lower()
        if st.startswith(_VENDOR_PREFIXES):
            keys.append(st)
    for m in re.finditer(r'shape_type\s*=\s*"([^"]+)"', xml):
        st = m.group(1).strip().lower()
        if st.startswith(_VENDOR_PREFIXES):
            keys.append(st)
    for m in _VENDOR_STENCIL_PATTERN.finditer(xml):
        name = m.group(2).lower()
        if name in _STENCIL_WRAPPER_NAMES:
            continue
        stencil_key = _VENDOR_STENCIL_MAP[m.group(1)] + name
        keys.append(_SERVICE_KEY_ALIASES.get(stencil_key, stencil_key))
    return list(dict.fromkeys(keys))


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


#: search_shapes returns inconsistently detailed styles per icon — confirmed
#: directly from live capture logs (the raw pre-merge MCP response): most
#: resourceIcon/productIcon stencils come back with rounded=0;strokeWidth=1;
#: labelPosition=center, but some omit all three. Since the merge below only
#: overrides keys the MCP style actually specifies, an icon missing these
#: fell back to the add_node placeholder's own rougher defaults (rounded=1,
#: no border) — visibly different from every other, more fully templated
#: icon on the same page even though both resolved "successfully." Filled in
#: only when NEITHER side specifies them, so an explicit value from either
#: the placeholder or MCP always wins.
_RESOURCE_ICON_DEFAULTS = {"rounded": "0", "strokeWidth": "1", "labelPosition": "center"}


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
    mcp_low = mcp.lower()
    if "resourceicon" in mcp_low or "producticon" in mcp_low:
        # MCP wins if it specifies one of these; otherwise force the clean
        # canonical default — NOT merged.setdefault(), which would be a
        # no-op here: the placeholder always already supplies rounded=1
        # (see diagram_edits._PLACEHOLDER_STYLE), so the key is never
        # actually "missing" from merged, only missing from what MCP itself
        # returned.
        for key, value in _RESOURCE_ICON_DEFAULTS.items():
            if key not in mcp_map:
                merged[key] = value
    return _format_style(merged)


def apply_mcp_styles(xml: str, style_overrides: dict[str, str]) -> str:
    """Merge MCP styles onto cells whose ``shape_type`` matches *style_overrides*.

    Uses ElementTree so MCP style strings with ``&`` / quotes are escaped safely.
    A resolved cell keeps its original semantic key permanently as ``service=``
    (find_service_keys() reads this first) and loses the transient
    ``shape_type`` (which only ever meant "still needs MCP resolution").
    Splitting these apart is what lets find_service_keys() trust the LLM's own
    answer instead of reverse-engineering the service from whatever stencil
    name MCP happened to pick — draw.io's own catalog names icons after what
    they depict (KMS -> "data_encryption_key", ElastiCache -> "cache_node",
    CloudWatch -> "alarm"...), an open-ended, ever-growing set no alias table
    can fully cover; confirmed by a live capture where a resolved KMS node
    (mxgraph.aws4.data_encryption_key) went undetected by well_architected.py/
    compliance.py until that one specific alias was added by hand. Cells
    whose shape_type is missing from *style_overrides* are left unchanged.
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

    # Multipage mxfiles reuse shape_type on every <diagram>/<root>. Apply to
    # all roots — first-root-only left Network grey when Logical was page 1.
    roots: list[ET.Element] = []
    if is_mxfile:
        roots = [r for r in tree.iter("root")]
        if not roots:
            found = tree.find("root")
            if found is not None:
                roots = [found]
    else:
        roots = [tree]

    if not roots:
        return xml

    changed = False
    for root in roots:
        for cell in root.iter("mxCell"):
            cid = cell.get("id") or ""
            # Never paint service icons onto briefing chrome.
            if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
                continue
            st = cell.get("shape_type")
            if not st:
                continue
            resolved = style_overrides.get(st)
            if not resolved:
                # MCP miss — keep placeholder style and shape_type for visibility/debug
                continue
            base_style = cell.get("style") or ""
            cell.set("style", merge_drawio_styles(base_style, resolved))
            cell.set("service", st)
            del cell.attrib["shape_type"]
            changed = True

    if not changed:
        return xml

    if is_mxfile:
        return ET.tostring(tree, encoding="unicode")
    root = roots[0]
    if "<root>" in trimmed:
        return ET.tostring(root, encoding="unicode")
    return "".join(ET.tostring(c, encoding="unicode") for c in root.findall("mxCell"))


# Container shrink-wrap after MCP icon merge (label band under bottom-labeled leaves).
_FIT_PAD_X = 24.0
_FIT_PAD_BOTTOM = 24.0
_FIT_LABEL_BAND = 28.0
_FIT_MIN_W = 120.0
_FIT_MIN_H = 80.0
_FIT_CHILD_MIN = 24.0  # clamp negative/near-zero child coords
_SIBLING_GAP = 24.0  # clear space between sibling containers (zones vs supporting groups)
_ZONE_TO_SUPPORTING_GAP = 70.0  # craft rule: supporting groups ≥70px below Zones
_SAME_ROW_TOL = 24.0  # treat tops within this as the same horizontal row
_CONTAINER_ARC_SIZE = "2"
_OVERVIEW_PANEL_ID = "overview_panel"
_OVERVIEW_PANEL_ARC_SIZE = "2"


def _is_container_cell(cell: ET.Element) -> bool:
    """True for group / swimlane / container=1 vertices (not edges or leaves)."""
    if cell.get("edge") == "1" or cell.get("vertex") != "1":
        return False
    style = cell.get("style") or ""
    if "container=1" in style or "swimlane" in style:
        return True
    if "mxgraph.aws4.group" in style:
        return True
    # Invisible group container style token
    parts = {p.split("=", 1)[0] for p in style.split(";") if p}
    return "group" in parts


def _cell_geometry(cell: ET.Element) -> tuple[float, float, float, float] | None:
    geom = cell.find("mxGeometry")
    if geom is None:
        return None
    try:
        x = float(geom.get("x") or 0)
        y = float(geom.get("y") or 0)
        w = float(geom.get("width") or 0)
        h = float(geom.get("height") or 0)
    except ValueError:
        return None
    return x, y, w, h


def _set_geom_xy(cell: ET.Element, *, x: float | None = None, y: float | None = None) -> bool:
    geom = cell.find("mxGeometry")
    if geom is None:
        return False
    if x is not None:
        geom.set("x", str(x))
    if y is not None:
        geom.set("y", str(y))
    return True


def _child_content_extent(cell: ET.Element) -> tuple[float, float, float, float] | None:
    """Return (x, y, right, bottom) including under-icon label band when needed."""
    geo = _cell_geometry(cell)
    if geo is None:
        return None
    x, y, w, h = geo
    style = cell.get("style") or ""
    label_band = _FIT_LABEL_BAND if "verticalLabelPosition=bottom" in style else 0.0
    return x, y, x + w, y + h + label_band


def _ranges_overlap(a0: float, a1: float, b0: float, b1: float, pad: float = 0.0) -> bool:
    return a0 < b1 + pad and b0 < a1 + pad


def _normalize_container_arc_size(cells: list[ET.Element]) -> bool:
    """Force every architecture container to use subtle corners (arcSize=2)."""
    changed = False
    for cell in cells:
        if not _is_container_cell(cell):
            continue
        if (cell.get("id") or "") == _OVERVIEW_PANEL_ID:
            continue
        style = cell.get("style") or ""
        parts = _parse_style(style)
        if parts.get("arcSize") == _CONTAINER_ARC_SIZE and parts.get("rounded", "1") == "1":
            continue
        # AWS group icon containers: set arcSize without forcing rounded=1
        if "mxgraph.aws4.group" in style or "grIcon=" in style:
            if parts.get("arcSize") == _CONTAINER_ARC_SIZE:
                continue
            parts["arcSize"] = _CONTAINER_ARC_SIZE
        else:
            parts["rounded"] = "1"
            parts["arcSize"] = _CONTAINER_ARC_SIZE
        cell.set("style", _format_style(parts))
        changed = True
    return changed


def _soften_overview_panel_corners(cells: list[ET.Element]) -> bool:
    changed = False
    for cell in cells:
        if cell.get("id") != _OVERVIEW_PANEL_ID:
            continue
        style = cell.get("style") or ""
        parts = _parse_style(style)
        if parts.get("arcSize") == _OVERVIEW_PANEL_ARC_SIZE and parts.get("rounded", "1") == "1":
            continue
        parts["rounded"] = "1"
        parts["arcSize"] = _OVERVIEW_PANEL_ARC_SIZE
        cell.set("style", _format_style(parts))
        changed = True
    return changed


def _fit_one_pass(
    containers: list[ET.Element],
    children: dict[str, list[ET.Element]],
) -> bool:
    changed = False
    for container in containers:
        cid = container.get("id") or ""
        kids = children.get(cid) or []
        geom = container.find("mxGeometry")
        if geom is None:
            continue
        if not kids:
            # Empty shell (e.g. unused AZ) — clamp to title-band minimum; never keep LLM mega-boxes.
            try:
                cur_w = float(geom.get("width") or 0)
                cur_h = float(geom.get("height") or 0)
            except ValueError:
                cur_w, cur_h = 0.0, 0.0
            need_w, need_h = _FIT_MIN_W, _FIT_MIN_H
            if cur_w > need_w + 0.5:
                geom.set("width", str(need_w))
                changed = True
            if cur_h > need_h + 0.5:
                geom.set("height", str(need_h))
                changed = True
            continue

        for kid in kids:
            geo = kid.find("mxGeometry")
            if geo is None:
                continue
            try:
                kx = float(geo.get("x") or 0)
                ky = float(geo.get("y") or 0)
            except ValueError:
                continue
            if kx < 0:
                geo.set("x", str(_FIT_CHILD_MIN))
                changed = True
            if ky < 0:
                geo.set("y", str(max(_FIT_CHILD_MIN, 48.0)))
                changed = True

        max_right = 0.0
        max_bottom = 0.0
        any_extent = False
        for kid in kids:
            extent = _child_content_extent(kid)
            if extent is None:
                continue
            _, _, right, bottom = extent
            max_right = max(max_right, right)
            max_bottom = max(max_bottom, bottom)
            any_extent = True
        if not any_extent:
            continue

        need_w = max(_FIT_MIN_W, max_right + _FIT_PAD_X)
        need_h = max(_FIT_MIN_H, max_bottom + _FIT_PAD_BOTTOM)
        geom = container.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_w = float(geom.get("width") or 0)
            cur_h = float(geom.get("height") or 0)
        except ValueError:
            cur_w, cur_h = 0.0, 0.0
        # Bidirectional: shrink oversized containers back down to their actual
        # need, not just grow undersized ones — an LLM-overestimated box (the
        # "huge empty region" anti-pattern) would otherwise stay oversized
        # through the entire deterministic repair pipeline forever.
        if abs(need_w - cur_w) > 0.5:
            geom.set("width", str(need_w))
            changed = True
        if abs(need_h - cur_h) > 0.5:
            geom.set("height", str(need_h))
            changed = True
    return changed


def _separate_sibling_containers(
    children: dict[str, list[ET.Element]],
    gap: float = _SIBLING_GAP,
) -> bool:
    """Push sibling containers apart so zones/subnets don't overlap supporting groups."""
    changed = False
    for kids in children.values():
        containers = [k for k in kids if _is_container_cell(k)]
        if len(containers) < 2:
            continue

        def _is_supporting(c: ET.Element) -> bool:
            value = (c.get("value") or "").strip().lower()
            first = value.split()[0] if value.split() else ""
            return first in {"security", "monitoring", "integration", "management"}

        def _is_zone_like(c: ET.Element) -> bool:
            value = (c.get("value") or "").strip().lower()
            return "zone" in value or value.startswith("az")

        # Vertical: lower box must clear upper box when they share an x-range
        ordered = sorted(
            containers,
            key=lambda c: (
                (_cell_geometry(c) or (0, 0, 0, 0))[1],
                (_cell_geometry(c) or (0, 0, 0, 0))[0],
            ),
        )
        for i in range(1, len(ordered)):
            gi = _cell_geometry(ordered[i])
            if gi is None:
                continue
            ix, iy, iw, ih = gi
            for j in range(i):
                gj = _cell_geometry(ordered[j])
                if gj is None:
                    continue
                jx, jy, jw, jh = gj
                # Peer AZs / side-by-side groups on the same row — skip vertical push
                if abs(iy - jy) <= _SAME_ROW_TOL:
                    continue
                if not _ranges_overlap(ix, ix + iw, jx, jx + jw):
                    continue
                use_gap = gap
                if _is_zone_like(ordered[j]) and _is_supporting(ordered[i]):
                    use_gap = max(gap, _ZONE_TO_SUPPORTING_GAP)
                need_y = jy + jh + use_gap
                if iy + 0.5 < need_y:
                    _set_geom_xy(ordered[i], y=need_y)
                    iy = need_y
                    changed = True

        # Horizontal: right box must clear left box when they share a y-range
        ordered_x = sorted(
            containers,
            key=lambda c: (
                (_cell_geometry(c) or (0, 0, 0, 0))[0],
                (_cell_geometry(c) or (0, 0, 0, 0))[1],
            ),
        )
        for i in range(1, len(ordered_x)):
            gi = _cell_geometry(ordered_x[i])
            if gi is None:
                continue
            ix, iy, iw, ih = gi
            for j in range(i):
                gj = _cell_geometry(ordered_x[j])
                if gj is None:
                    continue
                jx, jy, jw, jh = gj
                if not _ranges_overlap(iy, iy + ih, jy, jy + jh):
                    continue
                need_x = jx + jw + gap
                if ix + 0.5 < need_x:
                    _set_geom_xy(ordered_x[i], x=need_x)
                    ix = need_x
                    changed = True
    return changed


def _fit_root(root: ET.Element) -> bool:
    """Fit/separate/normalize containers inside one diagram <root>. Returns if changed."""
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells}

    # The IR compiler (compiler/corridor.py, compiler/network.py) is
    # geometrically authoritative for pages it produced — it sizes every
    # container to its children itself and marks the default layer
    # layout="ir". Re-fitting here would silently resize/reposition
    # already-correct containers using this function's own, separate
    # bounding-box computation (found while porting Logical: an
    # ExternalActor band beside the row made this shift a container's y by
    # 84px with zero warnings to show for it).
    default_layer = by_id.get("1")
    if default_layer is not None and default_layer.get("layout") == "ir":
        return False

    children: dict[str, list[ET.Element]] = {}
    for cell in cells:
        if cell.get("edge") == "1" or cell.get("vertex") != "1":
            continue
        pid = cell.get("parent")
        if not pid or pid not in by_id:
            continue
        children.setdefault(pid, []).append(cell)

    parent_map = {cid: (by_id[cid].get("parent") if cid in by_id else None) for cid in by_id}
    containers = [c for c in cells if _is_container_cell(c) and c.get("id")]
    containers.sort(key=lambda c: _cell_depth(c.get("id") or "", parent_map), reverse=True)

    changed = _fit_one_pass(containers, children)
    for _ in range(3):
        moved = _separate_sibling_containers(children)
        if not moved:
            break
        changed = True
        _fit_one_pass(containers, children)

    if _normalize_container_arc_size(cells):
        changed = True
    if _soften_overview_panel_corners(cells):
        changed = True
    return changed


def fit_containers_to_children(xml: str) -> str:
    """Grow containers to fit children, then keep sibling containers from overlapping.

    Processes deepest containers first. Does not move children between parents or
    change leaf sizes/styles — only container width/height, sibling x/y when
    needed for gaps, and ``arcSize=2`` on all architecture containers.

    Multipage mxfiles: applies to **every** ``<root>`` (same as ``apply_mcp_styles``),
    so Network is not skipped when Logical is page 1.
    """
    if not (xml or "").strip() or "<mxCell" not in xml:
        return xml

    trimmed = xml.strip()
    is_mxfile = "<mxfile" in trimmed
    parse_src = trimmed
    if not is_mxfile and "<root>" not in trimmed:
        parse_src = f"<root>{trimmed}</root>"

    try:
        tree = ET.fromstring(parse_src)
    except ET.ParseError:
        return xml

    roots: list[ET.Element] = []
    if is_mxfile:
        roots = [r for r in tree.iter("root")]
        if not roots:
            found = tree.find("root")
            if found is not None:
                roots = [found]
    else:
        roots = [tree]

    if not roots:
        return xml

    changed = False
    for root in roots:
        if _fit_root(root):
            changed = True

    if not changed:
        return xml

    if is_mxfile:
        return ET.tostring(tree, encoding="unicode")
    root = roots[0]
    if "<root>" in trimmed:
        return ET.tostring(root, encoding="unicode")
    return "".join(ET.tostring(c, encoding="unicode") for c in root.findall("mxCell"))
