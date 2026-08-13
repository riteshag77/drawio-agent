"""Parse model responses into structured payloads."""

from __future__ import annotations

import re


def extract_mermaid(text: str) -> str:
    """Pull Mermaid source from fenced or plain model output."""
    if not text or not text.strip():
        raise ValueError("Empty Mermaid response from model.")
    stripped = text.strip()
    fenced = re.search(r"```(?:mermaid)?\s*([\s\S]*?)```", stripped)
    return fenced.group(1).strip() if fenced else stripped


# Not real diagram nodes: diagram-type/direction keywords, control-flow
# keywords (sequence/state diagrams), and Mermaid's own start/end marker.
_MERMAID_NON_NODE_KEYWORDS = frozenset({
    "flowchart", "graph", "sequencediagram", "statediagram", "statediagram-v2",
    "lr", "rl", "td", "tb", "bt",
    "participant", "actor", "note", "loop", "alt", "else", "opt", "par",
    "and", "end", "activate", "deactivate", "class", "click", "style",
    "over", "right", "left", "of",
})

_ARROW = r"(?:-->|-\.->|==>|---|->>|-->>)"

# An identifier on the *source* side of a shape or an edge: `A[`, `A(`, `A{`,
# or `A -->`. [ \t]* rather than \s* deliberately — the lookahead must not
# cross a newline, or "stateDiagram-v2" followed (several lines later) by any
# bracket at all miscounts the diagram-type declaration itself as a node.
_MERMAID_SOURCE_TOKEN = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\b(?=[ \t]*(?:[\[({]|" + _ARROW + r"))"
)

# The identifier on the *target* side of an edge: `--> B`, `->>B: text`, or
# `-->|Yes| D` (a flowchart edge label sits between the arrow and its
# target). Without this, any node that only ever appears as a target — a
# chain's last node, a sequence diagram's message recipient — was invisible:
# _MERMAID_SOURCE_TOKEN only ever looks to a token's right, never its left.
_MERMAID_TARGET_TOKEN = re.compile(
    _ARROW + r"[ \t]*(?:\|[^|\n]*\|[ \t]*)?([A-Za-z_][A-Za-z0-9_]*)\b"
)


def estimate_mermaid_node_count(mermaid_source: str) -> int:
    """Best-effort count of distinct node/participant identifiers.

    Approximate by design, not a real Mermaid parser: it looks for an
    identifier on either side of a shape opener (``[``/``(``/``{``) or an
    edge/message operator (``-->``, ``->>``, ...), which covers the
    flowchart, sequenceDiagram and stateDiagram-v2 forms the prompt allows
    without needing three separate grammars. Good enough for the warning this
    feeds — see ``docs/PROMPT_BATTLE_TEST.md`` C2 — not for anything that
    would reject a diagram over it. A participant declared but never used in
    any message or edge is the one case this can still miss.
    """
    tokens = {
        m.group(1) for m in _MERMAID_SOURCE_TOKEN.finditer(mermaid_source)
    } | {
        m.group(1) for m in _MERMAID_TARGET_TOKEN.finditer(mermaid_source)
    }
    tokens = {t for t in tokens if t.lower() not in _MERMAID_NON_NODE_KEYWORDS}
    tokens.discard("*")  # stateDiagram-v2's [*] start/end marker, not a real node
    return len(tokens)
