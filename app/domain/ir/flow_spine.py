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
#: How many leaves of a *Network* tier the spine threads through. Corridor views
#: deliberately have no such limit — see ``_corridor_chain``.
_MAX_LEAVES_PER_TIER = 3


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
    # Tell the layout these edges are ours. Flow-graph heuristics must not read
    # them — see ``IRModel.flows_are_synthesized``.
    ir.mark_flows_synthesized()
    return True


def _corridor_chain(ir: Any) -> list[str]:
    """Every leaf of every corridor, in declaration order.

    Deliberately not capped. The cap was three per corridor, which had two
    consequences on a wider corridor, both bad. It left the rest touching
    nothing — and an unconnected leaf is a hard Logical error, so the spine
    built to rescue a flow-less IR failed it instead. And attaching the
    stragglers some other way (a fan from the corridor's first leaf) made
    things worse still: the layout ranks leaves from their edges, so a fan puts
    every straggler one rank below its anchor and the corridor's reading order
    stops being the order the model declared.

    A chain restores it exactly: rank *is* declaration index, so ``_tile_order``
    returns the same grid it would produce for a corridor with no edges at all.
    A chain was never the "full mesh" the cap warned about either — it is one
    edge per leaf, the same count the fan added.

    All of this is invention, and that is the point of the module: the model
    returned no flows, so nothing here is a claim about the real call graph.
    Given that, the arrangement that reads cleanly and preserves the one signal
    the model *did* give — the order it listed things in — is the right one.
    """
    ids: list[str] = []
    for corridor in list(ir.corridors) + list(getattr(ir, "anchored", []) or []):
        ids.extend(leaf.id for leaf in corridor.leaves)
    return ids


def _network_chain(ir: Any) -> list[str]:
    forbidden = ir.no_edge_ids() if hasattr(ir, "no_edge_ids") else set()
    ids: list[str] = []
    edge = ir.cloud.edge_layer
    if edge is not None:
        for leaf in edge.leaves[:_MAX_LEAVES_PER_TIER]:
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
