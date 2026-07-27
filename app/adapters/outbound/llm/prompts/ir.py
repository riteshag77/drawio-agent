"""System prompt assembly for IR generation — Network and the three corridor views.

Far smaller than the XML-generation prompts: everything those packs teach
about geometry, styles, parent wiring, padding, arcSize, pitch and container
fitting is now a compiler's job (or, for the corridor views, was *already*
a deterministic layout engine's job — see ``domain/ir/corridor.py``), so none
of it needs to be said. What remains is the part that is genuinely a judgement
call — which service belongs where, and what the story is.

One assembly function (``_assemble_ir_prompt``) backs all four views; the
public functions below just supply each view's specific pack list. That's the
prompt-side counterpart to sharing one ``CorridorIR``/compiler across three
views instead of writing three near-identical ones.

Reuses the same cache-breakpoint and hint-filtered icon catalog machinery as
``prompts.system`` so IR calls get identical prompt-cache behaviour.
"""

from __future__ import annotations

from app.adapters.outbound.llm.prompts.system import (
    CACHE_BREAKPOINT,
    MAX_CUSTOM_SYSTEM_MESSAGE_LEN,
    _filter_catalog,
    _get_reference_files,
    _load_prompt,
    _load_reference,
    _resolve_model_name,
    _warn_if_over_budget,
)

_ICON_PLATFORMS = frozenset({"aws", "gcp", "azure", "multi"})

_SHAPE_TYPE_GUIDE = (
    "## shape_type vocabulary\n"
    "Use the catalog below to pick accurate `shape_type` keys. Convert a catalog "
    "entry to a semantic key by lowercasing and replacing spaces with underscores "
    "(prefixed by platform), e.g. 'RDS' on AWS -> `aws_rds`. If a service is not in "
    "the catalog, still emit your best semantic guess — an unresolved key renders as "
    "a labelled grey placeholder, which is strictly better than a wrong icon."
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
) -> str:
    model_name = _resolve_model_name(model_id, provider)
    platform_key = (platform or "aws").strip().lower()
    if platform_key not in _ICON_PLATFORMS:
        platform_key = "aws"

    stable: list[str] = [_load_prompt(name) for name in view_prompt_files]
    if include_cloud_classify:
        # Classification guidance (which service is "app tier" vs "data tier" vs a
        # cross-cutting concern) is genuinely a judgement call, so the platform
        # classify pack still earns its tokens. The *icons* pack does not — stencil
        # selection moved to MCP, and geometry moved to the compiler.
        try:
            stable.append(_load_prompt(f"classify/{platform_key}.txt"))
        except FileNotFoundError:
            pass
    if include_saas_icons:
        try:
            saas_icons = _load_prompt("icons/saas.txt")
            if saas_icons:
                stable.append(saas_icons)
        except FileNotFoundError:
            pass

    volatile: list[str] = []
    if include_cloud_icon_catalog:
        for ref in _get_reference_files(platform_key, three_d=False):
            content = _load_reference(ref)
            if content:
                volatile.append(_SHAPE_TYPE_GUIDE + "\n\n" + _filter_catalog(content, user_hint))

    custom = (custom_system_message or "").strip()
    if custom:
        volatile.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")

    stable_text = "\n\n".join(p for p in stable if p).replace("{{MODEL_NAME}}", model_name)
    volatile_text = "\n\n".join(p for p in volatile if p).replace("{{MODEL_NAME}}", model_name)
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
        include_saas_icons=False,
        model_id=model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, user_hint=user_hint,
    )


# Per-corridor-view knobs: Logical uses its own logical_*/saas_* leaf vocabulary
# (no cloud classify pack, no cloud icon catalog needed); Security and
# Data-flow keep cloud shape_type on inventory services, same as Network.
_CORRIDOR_VIEW_CONFIG: dict[str, dict[str, bool]] = {
    "logical": {"include_cloud_classify": False, "include_cloud_icon_catalog": False},
    "security": {"include_cloud_classify": True, "include_cloud_icon_catalog": True},
    "data_flow": {"include_cloud_classify": True, "include_cloud_icon_catalog": True},
}


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
    )
