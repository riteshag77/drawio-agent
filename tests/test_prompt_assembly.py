"""Prompt assembly: 3D XML pack + IR view packs (no legacy core/views)."""

import pytest

from app.adapters.outbound.llm.prompts.ir import (
    get_corridor_ir_system_prompt,
    get_network_ir_system_prompt,
)
from app.adapters.outbound.llm.prompts.system import (
    ESTIMATE_PROMPT_TOKENS,
    MAX_SYSTEM_PROMPT_TOKENS,
    get_xml_gen_system_prompt,
)


def test_3d_prompt_under_budget():
    prompt = get_xml_gen_system_prompt(platform="aws", three_d=True, user_hint="vpc lambda")
    tokens = ESTIMATE_PROMPT_TOKENS(prompt)
    assert tokens <= MAX_SYSTEM_PROMPT_TOKENS, f"3D prompt ~{tokens} tokens exceeds {MAX_SYSTEM_PROMPT_TOKENS}"


def test_non_3d_xml_prompt_raises():
    try:
        get_xml_gen_system_prompt(platform="aws")
        pytest.fail("expected ValueError for non-3D XML prompt")
    except ValueError as exc:
        assert "three_d=True" in str(exc)


def test_3d_skips_classify_and_uses_isometric():
    prompt = get_xml_gen_system_prompt(platform="aws", three_d=True)
    assert "mxgraph.aws3d" in prompt
    assert "isometricEdgeStyle" in prompt
    assert "Service Classification — AWS" not in prompt


def test_network_ir_loads_hierarchy_and_classify():
    prompt = get_network_ir_system_prompt(platform="aws", user_hint="lambda rds vpc")
    assert "NetworkIR" in prompt
    assert "external_actors" in prompt
    assert "Service Classification — AWS" in prompt
    assert "Entra ID" not in prompt
    assert "Cosmos DB" not in prompt


def test_gcp_ir_excludes_aws_stencils_from_classify():
    prompt = get_network_ir_system_prompt(platform="gcp")
    assert "Service Classification — GCP" in prompt
    assert "NEVER use mxgraph.aws4" in prompt


def test_logical_ir_skips_cloud_classify():
    prompt = get_corridor_ir_system_prompt("logical", platform="aws", user_hint="salesforce")
    assert "Service Classification — AWS" not in prompt
    assert "logical_" in prompt.lower() or "(SAAS)" in prompt
    assert "domain_experience" in prompt
    assert "Check Cache" in prompt
    assert "Cache Miss: Fetches via API" in prompt


def test_security_ir_requires_external_saas_zone():
    prompt = get_corridor_ir_system_prompt("security", platform="aws")
    assert "zone_external_saas" in prompt
    assert "Fetches Credentials" in prompt


def test_data_flow_ir_requires_saas_resilience_companions():
    prompt = get_corridor_ir_system_prompt("data_flow", platform="aws")
    assert "Dead-letter" in prompt or "DLQ" in prompt
    assert "Failed API Retries" in prompt
