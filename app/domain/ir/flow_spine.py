"""Deterministic request-path spine when the LLM leaves ``flows`` empty.

Live captures (gemini flash-lite and similar) repeatedly returned schema-valid
IRs with leaves/actors but ``flows: []``. Two corrective LLM passes did not
fix it, and the pipeline fell back to direct XML — visibly worse geometry and
wasted tokens. Filling a minimal L→R spine in code keeps the IR+compiler path
alive; the model still owns grouping and service choice.
"""

from __future__ import annotations

from typing import Any

from app.domain.ir.base import Flow

_LEFT_KINDS = frozenset({"users", "saas_ingress", "on_prem"})
_RIGHT_KINDS = frozenset({"saas_egress"})
#: How many leaves of a corridor the *spine* threads through. Everything past
#: this still gets connected — see ``_attach_remaining`` — just not by being
#: strung into the request path, which is what would turn a dense private zone
#: into a chain implying calls that don't happen.
_MAX_LEAVES_PER_CORRIDOR = 3


def synthesize_spine_flows(ir: Any) -> bool:
    """Populate ``ir.flows`` with a minimal journey spine. Returns True if filled."""
    if getattr(ir, "flows", None):
        return False

    chain: list[str] = []
    for actor in getattr(ir, "external_actors", []) or []:
        if actor.kind in _LEFT_KINDS:
            chain.append(actor.id)

    if hasattr(ir, "corridors"):
        chain.extend(_corridor_chain(ir))
    elif hasattr(ir, "cloud"):
        chain.extend(_network_chain(ir))
    else:
        return False

    for actor in getattr(ir, "external_actors", []) or []:
        if actor.kind in _RIGHT_KINDS:
            chain.append(actor.id)

    ordered = _dedupe(chain)
    if len(ordered) < 2:
        return False

    ir.flows = [
        Flow(source=ordered[i], target=ordered[i + 1], primary=True, label="")
        for i in range(len(ordered) - 1)
    ]
    _attach_remaining(ir, set(ordered))
    return True


def _attach_remaining(ir: Any, on_spine: set[str]) -> None:
    """Give every leaf the spine skipped one edge into its own corridor.

    Without this the spine *creates* orphans: it threads at most
    ``_MAX_LEAVES_PER_CORRIDOR`` leaves per corridor, so a corridor holding
    eight left five of them touching nothing — and Logical treats an
    unconnected leaf as a hard error, so a flow-less IR with wide corridors
    failed the whole request. That was reachable before and is more reachable
    now that nothing caps corridor width.

    One edge per stranded leaf, from the first leaf of its own corridor, and
    ``primary=False`` so the request path drawn through the spine stays the
    emphasized line. Not a chain through the strays: they are peers within a
    domain, and stringing them serially would draw a call sequence the model
    never described. Not a mesh either — this adds exactly one edge per leaf.
    """
    if not hasattr(ir, "corridors"):
        # Network's own chain already visits one leaf per tier and its
        # unvisited leaves are legal: that view carries meaning through
        # container nesting, so a leaf sitting in a subnet with no edge is
        # connected *by where it is*. Only the corridor views make it an error.
        return
    for corridor in list(ir.corridors) + list(getattr(ir, "anchored", []) or []):
        anchor = next((lf.id for lf in corridor.leaves if lf.id in on_spine), None)
        if anchor is None:
            continue
        for leaf in corridor.leaves:
            if leaf.id not in on_spine:
                ir.flows.append(Flow(source=anchor, target=leaf.id, primary=False, label=""))


def _corridor_chain(ir: Any) -> list[str]:
    ids: list[str] = []
    for corridor in list(ir.corridors) + list(getattr(ir, "anchored", []) or []):
        for leaf in corridor.leaves[:_MAX_LEAVES_PER_CORRIDOR]:
            ids.append(leaf.id)
    return ids


def _network_chain(ir: Any) -> list[str]:
    forbidden = ir.no_edge_ids() if hasattr(ir, "no_edge_ids") else set()
    ids: list[str] = []
    edge = ir.cloud.edge_layer
    if edge is not None:
        for leaf in edge.leaves[:_MAX_LEAVES_PER_CORRIDOR]:
            if leaf.id not in forbidden:
                ids.append(leaf.id)

    for region in ir.cloud.regions:
        for vpc in region.vpcs:
            if vpc.gateway is not None and vpc.gateway.id not in forbidden:
                ids.append(vpc.gateway.id)
            by_tier: dict[str, list[str]] = {"public": [], "app": [], "data": []}
            for zone in vpc.zones:
                for subnet in zone.subnets:
                    bucket = by_tier.setdefault(subnet.tier, [])
                    for leaf in subnet.leaves:
                        if leaf.id not in forbidden:
                            bucket.append(leaf.id)
            for tier in ("public", "app", "data"):
                for leaf_id in by_tier.get(tier, [])[:1]:
                    ids.append(leaf_id)
    return ids


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
