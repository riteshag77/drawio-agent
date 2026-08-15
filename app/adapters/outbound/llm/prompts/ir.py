"""System prompt assembly for IR generation — every view that emits an IR:
Network, the three corridor views (Logical/Security/Data-flow), ArchiMate, and
Generic.

Far smaller than the XML-generation prompts: everything those packs teach
about geometry, styles, parent wiring, padding, arcSize, pitch and container
fitting is now a compiler's job (or, for the corridor views, was *already*
a deterministic layout engine's job — see ``domain/ir/corridor.py``), so none
of it needs to be said. What remains is the part that is genuinely a judgement
call — which service belongs where, and what the story is.

One assembly function (``_assemble_ir_prompt``) backs every view; the public
functions below just supply each view's specific pack list and icon knobs.
That's the prompt-side counterpart to sharing one ``CorridorIR``/compiler
across three views instead of writing three near-identical ones.

Reuses the same cache-breakpoint and hint-filtered icon catalog machinery as
``prompts.system`` so IR calls get identical prompt-cache behaviour.
"""

from __future__ import annotations

from app.adapters.outbound.governance_store import list_rules as _list_governance_rules
from app.adapters.outbound.llm.prompts.system import (
    CACHE_BREAKPOINT,
    MAX_CUSTOM_SYSTEM_MESSAGE_LEN,
    _filter_catalog,
    _get_reference_files,
    _load_prompt,
    _load_reference,
    _warn_if_over_budget,
)

_ICON_PLATFORMS = frozenset({"aws", "gcp", "azure", "multi"})

# The catalogs now list ready-to-use keys in the `<platform>_<service>` form, so
# this no longer asks the model to derive one. The old wording taught a
# conversion ("'RDS' on AWS -> aws_rds") against an AWS catalog that contained
# neither 'RDS' nor a key column — it was a stencil table — so the guide and the
# file it introduced disagreed. See docs/PROMPT_BATTLE_TEST.md finding A1.
_SHAPE_TYPE_GUIDE = (
    "## shape_type vocabulary\n"
    "Pick each leaf's `shape_type` from the catalog below and copy the key "
    "verbatim — the keys are already in the form the IR expects. If a service is "
    "not listed, emit your best semantic guess in the same `<platform>_<service>` "
    "form; an unresolved key renders as a labelled grey placeholder, which is "
    "strictly better than a confidently wrong icon."
)


def _governance_rules_block(governance_view: str, platform_key: str) -> str:
    """Enabled house-style rules for this view/platform, worded exactly as the
    post-generation corrective pass would word them (``governance.py``'s
    ``findings_to_warnings``) — so a first pass that reads this can satisfy the
    same rule the corrective pass would otherwise have to catch.

    Deliberately placed in the *stable* prefix by the caller: enabled rules
    rarely change request-to-request, so this is a fixed cost on a cache miss
    and near-free on a cache hit, rather than a per-request tax. Best-effort —
    matches every other governance call site in this codebase (see
    ``core/services/agent.py``): a store error means one diagram doesn't get
    the extra context, never a broken request. The deterministic post-hoc
    check in ``core/services/governance.py`` stays the source of truth either
    way — this can only help the first pass, never replace the check.
    """
    if not governance_view:
        return ""
    try:
        rules = _list_governance_rules(governance_view, enabled_only=True)
    except Exception:
        return ""
    messages = [
        r.message.strip() for r in rules
        if r.message.strip() and r.platform in ("any", platform_key)
    ]
    if not messages:
        return ""
    bullets = "\n".join(f"- {m}" for m in messages)
    return (
        "## House-style rules (org policy — satisfy these on this first pass;\n"
        "they are checked afterward and a miss costs an extra corrective call)\n"
        f"{bullets}"
    )


def _assemble_ir_prompt(
    *,
    view_prompt_files: list[str],
    include_cloud_classify: bool,
    include_cloud_icon_catalog: bool,
    include_saas_icons: bool,
    model_id: str | None,
    provider: str | None,
    platform: str,
    custom_system_message: str,
    user_hint: str,
    governance_view: str = "",
) -> str:
    platform_key = (platform or "aws").strip().lower()
    if platform_key not in _ICON_PLATFORMS:
        platform_key = "aws"

    # A missing pack is a packaging/config error, not a runtime condition, so it
    # fails loudly and identically for every pack. Previously the view packs
    # raised while classify and SaaS were swallowed by `except FileNotFoundError`
    # — meaning a typo in a view name 500'd the request while a missing classify
    # pack silently produced a degraded prompt nobody could see from the outside.
    # Both are deployment mistakes; neither should be invisible.
    stable: list[str] = [_load_prompt(name) for name in view_prompt_files]
    if include_cloud_classify:
        # Classification guidance (which service is "app tier" vs "data tier" vs a
        # cross-cutting concern) is genuinely a judgement call, so the platform
        # classify pack still earns its tokens. The *icons* pack does not — stencil
        # selection moved to MCP, and geometry moved to the compiler.
        stable.append(_load_prompt(f"classify/{platform_key}.txt"))
    if include_saas_icons:
        saas_icons = _load_prompt("icons/saas.txt")
        if saas_icons:
            stable.append(saas_icons)
        # The prose guide above only names 3 example vendors inline — the real
        # curated catalog (19+ vendors, each with a distinct branded icon on
        # disk) was never wired into any prompt at all. Confirmed live: a
        # market-data vendor and a clearinghouse both got labelled with the
        # real Salesforce/Stripe logos — worse than a wrong shape_type key,
        # since these keys DO resolve to a specific, recognizable, wrong
        # brand rather than a generic placeholder. Small enough (~40 lines)
        # to include whole, no hint-filtering needed.
        saas_catalog = _load_reference("saas.txt")
        if saas_catalog:
            stable.append(saas_catalog)
    governance_block = _governance_rules_block(governance_view, platform_key)
    if governance_block:
        stable.append(governance_block)

    volatile: list[str] = []
    if include_cloud_icon_catalog:
        for ref in _get_reference_files(platform_key, three_d=False):
            content = _load_reference(ref)
            if content:
                volatile.append(_SHAPE_TYPE_GUIDE + "\n\n" + _filter_catalog(content, user_hint))

    custom = (custom_system_message or "").strip()
    if custom:
        volatile.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")

    stable_text = "\n\n".join(p for p in stable if p)
    volatile_text = "\n\n".join(p for p in volatile if p)
    _warn_if_over_budget(f"{stable_text}\n\n{volatile_text}")
    return f"{stable_text}\n\n{CACHE_BREAKPOINT}{volatile_text}"


def get_network_ir_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    user_hint: str = "",
) -> str:
    """Assemble the NetworkIR system prompt with a cache breakpoint."""
    return _assemble_ir_prompt(
        view_prompt_files=["ir/network.txt"],
        include_cloud_classify=True,
        include_cloud_icon_catalog=True,
        include_saas_icons=True,
        model_id=model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, user_hint=user_hint,
        governance_view="network",
    )


# Per-corridor-view knobs: Logical uses its own logical_*/saas_* leaf vocabulary
# (no cloud classify pack, no cloud icon catalog needed); Security and
# Data-flow keep cloud shape_type on inventory services, same as Network.
_CORRIDOR_VIEW_CONFIG: dict[str, dict[str, bool]] = {
    "logical": {"include_cloud_classify": False, "include_cloud_icon_catalog": False},
    "security": {"include_cloud_classify": True, "include_cloud_icon_catalog": True},
    "data_flow": {"include_cloud_classify": True, "include_cloud_icon_catalog": True},
}


def get_archimate_ir_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    user_hint: str = "",
) -> str:
    """Assemble the ArchimateIR system prompt.

    Deliberately not routed through ``_CORRIDOR_VIEW_CONFIG``: ArchiMate is not
    a corridor view (no row/shelf vocabulary, no ``corridor_core.txt``), so it
    gets its own single-pack entry point the way Network does.

    No cloud classify pack, no cloud icon catalog, no SaaS icons — none of them
    applies. ArchiMate's element vocabulary is a *closed notation*, and its
    "icons" are notation glyphs the compiler resolves from
    ``ARCHIMATE_ELEMENT_TABLE``, so a cloud-service catalog would only invite
    the model to reach for keys that have no meaning on this view.

    The pack is loaded only when ArchiMate is among the requested views —
    that falls out of the existing per-view assembly, since this function is
    only called from ``agent.generate_archimate_ir``.
    """
    return _assemble_ir_prompt(
        view_prompt_files=["ir/archimate.txt"],
        include_cloud_classify=False,
        include_cloud_icon_catalog=False,
        include_saas_icons=False,
        model_id=model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, user_hint=user_hint,
        governance_view="archimate",
    )


def get_generic_ir_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    custom_system_message: str = "",
    user_hint: str = "",
) -> str:
    """Assemble the GenericDiagramIR system prompt — no cloud classify pack,
    no cloud icon catalog, no SaaS icons: none of that applies to an as-is
    replication of a non-cloud image."""
    return _assemble_ir_prompt(
        view_prompt_files=["ir/generic.txt"],
        include_cloud_classify=False,
        include_cloud_icon_catalog=False,
        include_saas_icons=False,
        model_id=model_id, provider=provider, platform="generic",
        custom_system_message=custom_system_message, user_hint=user_hint,
    )


def get_simple_ir_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    custom_system_message: str = "",
    user_hint: str = "",
) -> str:
    """Assemble the SimpleDiagramIR system prompt — the non-cloud path.

    Same three exclusions as Generic and ArchiMate (no cloud classify pack, no
    cloud icon catalog, no SaaS icons): this path exists precisely for requests
    that are *not* cloud architecture, so a catalog of AWS/GCP/Azure service
    keys would only invite the model to reach for vocabulary the schema has no
    field to hold.

    No ``governance_view`` either. House-style rules are written about cloud
    architecture (naming, tiering, boundary placement) and are stored per cloud
    view; there is no rule set that means anything about a bar chart.
    """
    return _assemble_ir_prompt(
        view_prompt_files=["ir/simple.txt"],
        include_cloud_classify=False,
        include_cloud_icon_catalog=False,
        include_saas_icons=False,
        model_id=model_id, provider=provider, platform="generic",
        custom_system_message=custom_system_message, user_hint=user_hint,
    )


def get_corridor_ir_system_prompt(
    view: str,
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    user_hint: str = "",
) -> str:
    """Assemble the CorridorIR system prompt for Logical / Security / Data-flow."""
    view_key = (view or "").strip().lower()
    if view_key not in _CORRIDOR_VIEW_CONFIG:
        raise ValueError(f"get_corridor_ir_system_prompt: unknown corridor view {view!r}")
    knobs = _CORRIDOR_VIEW_CONFIG[view_key]
    return _assemble_ir_prompt(
        view_prompt_files=["ir/corridor_core.txt", f"ir/{view_key}.txt"],
        include_cloud_classify=knobs["include_cloud_classify"],
        include_cloud_icon_catalog=knobs["include_cloud_icon_catalog"],
        include_saas_icons=True,
        model_id=model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, user_hint=user_hint,
        governance_view=view_key,
    )
