"""Prompt assembly token-budget and platform-pack tests."""

from app.adapters.outbound.llm.prompts.system import (
    ESTIMATE_PROMPT_TOKENS,
    MAX_SYSTEM_PROMPT_TOKENS,
    get_xml_gen_system_prompt,
)


def test_aws_prompt_under_budget():
    prompt = get_xml_gen_system_prompt(platform="aws", user_hint="lambda rds vpc")
    tokens = ESTIMATE_PROMPT_TOKENS(prompt)
    assert tokens <= MAX_SYSTEM_PROMPT_TOKENS, f"AWS prompt ~{tokens} tokens exceeds {MAX_SYSTEM_PROMPT_TOKENS}"


def test_aws_excludes_azure_gcp_columns():
    prompt = get_xml_gen_system_prompt(platform="aws")
    assert "Entra ID" not in prompt
    assert "Cosmos DB" not in prompt
    assert "Cloud Armor" not in prompt
    assert "Service Classification — AWS" in prompt


def test_gcp_excludes_aws_icons_pack_rules_from_classify():
    prompt = get_xml_gen_system_prompt(platform="gcp")
    assert "Service Classification — GCP" in prompt
    assert "mxgraph.aws4.resourceIcon" not in prompt or "NEVER use mxgraph.aws4" in prompt


def test_3d_skips_aws4_icon_pack():
    prompt = get_xml_gen_system_prompt(platform="aws", three_d=True)
    assert "mxgraph.aws3d" in prompt
    assert "mxgraph.aws4.resourceIcon" not in prompt
    assert "isometricEdgeStyle" in prompt
    assert "Service Classification — AWS" not in prompt
    assert "Canonical Container Hierarchy" not in prompt


def test_network_pack_loaded_for_primary():
    prompt = get_xml_gen_system_prompt(platform="aws", primary_view="network")
    assert "Canonical Container Hierarchy" in prompt
    assert "Cross-Cutting Groups" in prompt
    assert "xml_gen_network" not in prompt  # content loaded, not path/filename


def test_logical_primary_skips_network_hierarchy():
    prompt = get_xml_gen_system_prompt(platform="aws", primary_view="logical")
    assert "Canonical Container Hierarchy" not in prompt
    assert "Bounded Contexts" in prompt or "business capabilities" in prompt.lower()
    assert "Service Classification — AWS" in prompt


def test_core_excludes_network_hierarchy():
    from app.adapters.outbound.llm.prompts.system import _load_prompt

    core = _load_prompt("core.txt")
    assert "Canonical Container Hierarchy" not in core
    assert "Availability Zone" not in core
    assert "Cross-Cutting Groups" not in core
    assert "180–220" in core
    assert "overview_panel" in core


def test_no_style_instructions_file_loaded():
    """CORE already includes edge colors; style_instructions must not be appended."""
    prompt = get_xml_gen_system_prompt(platform="aws")
    # style_instructions had a "## Visual style" header — should not appear twice-ish;
    # CORE uses "Edge colors by platform" instead.
    assert prompt.count("## Visual style") == 0
