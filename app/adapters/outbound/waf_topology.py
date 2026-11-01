"""Read a compiled diagram's *shape* — nesting and wiring — not just its parts.

``well_architected.py`` answers "is there a WAF icon on the page?". Most real
Well-Architected questions are not about presence at all:

    presence            topology
    ------------------  ------------------------------------------------
    "no WAF detected"   "the WAF is not on the path from the internet"
    "no load balancer"  "the database sits in a public subnet"
    "no cache"          "only one AZ, so there is nothing to fail over to"
    "KMS detected"      "this flow carries PII with no transport protection"

Everything needed to ask the right-hand column is already in the compiled XML.
Containers nest (``parent``), subnets carry their ``tier``, leaves carry
``shape_type``, and edges carry ``classification``/``protection`` — all stamped
by ``compiler/emit.py`` as attributes precisely so they survive into the
artifact rather than living only in the IR.

Reading the XML rather than the IR is deliberate. The IR exists only on a fresh
generation; a surgical edit, a 3D page and a diagram restored from history have
none. An assessment that requires the IR would silently skip exactly the
diagrams a user has been iterating on — and what matters is what is *drawn*,
which is the artifact, not the request that produced it.

Nothing here interprets colour or label text. A "Data Subnet" is a subnet whose
``tier`` attribute says ``data``; inferring it from a red stroke or the word
"Data" would break the first time a palette or a caption changed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    """One vertex, with the analysis-relevant attributes emit.py stamps."""

    id: str
    label: str
    shape_type: str
    tier: str
    parent: str
    criticality: str
    actor_kind: str = ""

    @property
    def is_external(self) -> bool:
        """Outside the architecture's own boundary — a user, a partner, a SaaS."""
        return bool(self.actor_kind)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str
    classification: str
    protection: str


@dataclass
class Topology:
    """A whole page, indexed for the questions the checks actually ask."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def ancestors(self, node_id: str) -> list[Node]:
        """Every container above ``node_id``, innermost first.

        Iterative and cycle-guarded: ``parent`` comes from a document that a
        surgical edit or an LLM repair may have touched, so a cycle is a
        malformed input to tolerate, not an invariant to trust.
        """
        out: list[Node] = []
        seen: set[str] = {node_id}
        current = self.nodes.get(node_id)
        while current is not None and current.parent not in seen:
            seen.add(current.parent)
            parent = self.nodes.get(current.parent)
            if parent is None:
                break
            out.append(parent)
            current = parent
        return out

    def tier_of(self, node_id: str) -> str:
        """The subnet tier a leaf sits in, or "" when it is not in one."""
        for ancestor in self.ancestors(node_id):
            if ancestor.tier:
                return ancestor.tier
        return ""

    def leaves(self) -> list[Node]:
        """Nodes that are not themselves a parent — the services, not the boxes."""
        parents = {n.parent for n in self.nodes.values()}
        return [n for n in self.nodes.values() if n.id not in parents]

    def leaves_in_tier(self, tier: str) -> list[Node]:
        return [n for n in self.leaves() if self.tier_of(n.id) == tier]

    def external_actors(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.is_external]

    def paths_from(
        self, start: str, *, stop_at: set[str], max_depth: int = 12,
    ) -> list[list[str]]:
        """Every forward path out of ``start``, cut where it enters ``stop_at``.

        Returns the node ids along each path *including* the stopping node, so
        a caller can ask both "where does traffic from the internet end up?"
        and "what did it pass through on the way?".

        Breadth-limited and visited-guarded per path: an architecture diagram
        is not a DAG (a retry arrow, a bidirectional link and a hub-and-spoke
        all produce cycles), and a path walker that assumes otherwise hangs on
        exactly the diagrams that most need checking. ``max_depth`` bounds the
        pathological fan-out case rather than describing a real limit.
        """
        outgoing: dict[str, list[str]] = {}
        for edge in self.edges:
            outgoing.setdefault(edge.source, []).append(edge.target)

        complete: list[list[str]] = []
        queue: list[list[str]] = [[start]]
        while queue:
            path = queue.pop()
            if len(path) > max_depth:
                continue
            tail = path[-1]
            nexts = [n for n in outgoing.get(tail, []) if n not in path]
            if not nexts:
                if len(path) > 1:
                    complete.append(path)
                continue
            for nxt in nexts:
                extended = [*path, nxt]
                if nxt in stop_at:
                    complete.append(extended)
                else:
                    queue.append(extended)
        return complete

    def zone_count(self) -> int:
        """How many availability zones the page draws.

        A zone is a container holding at least one tiered subnet — derived
        rather than matched on an id or a label, since neither is stable across
        platforms ("us-east-1a", "AZ-1", "Zone 1").
        """
        zones = {
            ancestor.id
            for node in self.nodes.values() if node.tier
            for ancestor in self.ancestors(node.id)[:1]
        }
        return len(zones)


def read_topology(xml: str) -> Topology:
    """Parse ``xml`` into a ``Topology``. Never raises: an unparseable or
    partial document yields an empty topology, and every check built on it then
    finds nothing — the assessment degrades to the presence-only checks rather
    than failing a request that had already produced a diagram."""
    topology = Topology()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return topology

    for cell in root.iter("mxCell"):
        cell_id = cell.get("id") or ""
        if not cell_id or cell_id in ("0", "1"):
            continue
        if cell.get("edge") == "1":
            source, target = cell.get("source"), cell.get("target")
            if source and target:
                topology.edges.append(Edge(
                    source=source, target=target, label=cell.get("value") or "",
                    classification=cell.get("classification") or "none",
                    protection=cell.get("protection") or "none",
                ))
            continue
        topology.nodes[cell_id] = Node(
            id=cell_id,
            label=cell.get("value") or "",
            shape_type=_shape_type(cell),
            tier=cell.get("tier") or "",
            parent=cell.get("parent") or "",
            criticality=cell.get("criticality") or "standard",
            actor_kind=cell.get("actor_kind") or "",
        )
    return topology


def _shape_type(cell: ET.Element) -> str:
    """``shape_type`` as an attribute, falling back to the style.

    Both spellings occur in the wild: the compiler stamps the attribute, but a
    surgically edited or hand-touched cell can carry it inside ``style`` only.
    ``xml_tools.find_service_keys`` already tolerates both, and an assessment
    that saw one and not the other would disagree with the service list shown
    beside it in the same panel.
    """
    attr = cell.get("shape_type")
    if attr:
        return attr
    style = cell.get("style") or ""
    for part in style.split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "shape_type":
            return value.strip().strip('"')
    return ""
