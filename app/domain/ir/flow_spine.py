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
#: Cap leaves taken from a single corridor so a dense private zone does not
#: become a full mesh — enough to show the story, not every peer call.
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
    return True


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
