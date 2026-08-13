"""Prompt assembly: 3D XML pack + IR view packs (no legacy core/views).

Two classes of guard here, beyond the per-view content assertions:

* **Budget** — every IR assembly must fit ``MAX_SYSTEM_PROMPT_TOKENS``, not just
  the 3D one. Network is the largest and was previously untested.
* **Vocabulary** — an IR prompt must never contain draw.io XML-era syntax.
  The IR packs forbid the model from emitting ``resIcon=``/``mxgraph.``/
  ``parent=``, so showing it that syntax is at best wasted cache prefix and at
  worst an invitation to leak it into IR JSON. These prompts are program logic
  with no other automated safety net, so the rule is asserted rather than
  trusted.
"""

import pathlib
import re

import pytest

from app.adapters.outbound.llm.prompts.ir import (
    get_archimate_ir_system_prompt,
    get_corridor_ir_system_prompt,
    get_generic_ir_system_prompt,
    get_network_ir_system_prompt,
)
from app.adapters.outbound.llm.prompts.system import (
    ESTIMATE_PROMPT_TOKENS,
    MAX_SYSTEM_PROMPT_TOKENS,
    get_xml_gen_system_prompt,
)

#: Every IR assembly, with a hint chosen to pull in a realistic catalog slice.
#: Platforms are spread across the entries so each catalog is exercised.
IR_ASSEMBLIES = {
    "network/aws": lambda: get_network_ir_system_prompt(platform="aws", user_hint="lambda rds vpc s3"),
    "network/gcp": lambda: get_network_ir_system_prompt(platform="gcp", user_hint="cloud run spanner"),
    "network/azure": lambda: get_network_ir_system_prompt(platform="azure", user_hint="aks cosmos"),
    "network/multi": lambda: get_network_ir_system_prompt(platform="multi", user_hint="aws gcp hybrid"),
    "logical": lambda: get_corridor_ir_system_prompt("logical", platform="aws", user_hint="salesforce portal"),
    "security": lambda: get_corridor_ir_system_prompt("security", platform="aws", user_hint="iam kms waf"),
    "data_flow": lambda: get_corridor_ir_system_prompt("data_flow", platform="aws", user_hint="kinesis s3 etl"),
    "archimate": lambda: get_archimate_ir_system_prompt(user_hint="business capability"),
    "generic": lambda: get_generic_ir_system_prompt(user_hint="flowchart boxes"),
}

#: draw.io XML-era syntax. Every IR pack explicitly forbids the model from
#: emitting these, so none of them belongs in an IR prompt.
FORBIDDEN_IN_IR_PROMPTS = (
    "resIcon=",
    "prIcon=",
    'style="shape=',
    "mxgraph.",
    'parent="1"',
)


def test_3d_prompt_under_budget():
    prompt = get_xml_gen_system_prompt(platform="aws", three_d=True, user_hint="vpc lambda")
    tokens = ESTIMATE_PROMPT_TOKENS(prompt)
    assert tokens <= MAX_SYSTEM_PROMPT_TOKENS, f"3D prompt ~{tokens} tokens exceeds {MAX_SYSTEM_PROMPT_TOKENS}"


@pytest.mark.parametrize("name", sorted(IR_ASSEMBLIES))
def test_ir_assembly_under_budget(name):
    """Network is the largest assembly and was never budget-checked."""
    tokens = ESTIMATE_PROMPT_TOKENS(IR_ASSEMBLIES[name]())
    assert tokens <= MAX_SYSTEM_PROMPT_TOKENS, (
        f"{name} IR prompt ~{tokens} tokens exceeds {MAX_SYSTEM_PROMPT_TOKENS}"
    )


#: Files injected into IR prompts as *content to copy from*. These are data, so
#: XML-era syntax in them is always wrong — there is no legitimate reason for a
#: catalog the model picks `shape_type` keys out of to mention a stencil path.
IR_INJECTED_FILES = [
    "config/references/aws_stencils.txt",
    "config/references/gcp-icons-common.txt",
    "config/references/azure-icons-common.txt",
    "config/references/multi-icons-top.txt",
    "config/xml_gen/classify/aws.txt",
    "config/xml_gen/classify/gcp.txt",
    "config/xml_gen/classify/azure.txt",
    "config/xml_gen/classify/multi.txt",
]

#: Words that turn a mention into a prohibition. Matched on word boundaries
#: against a markdown-stripped line, because the packs write "Do **not** invent
#: `resIcon=`" and a plain substring test misses the emphasis markers.
_NEGATION_RE = re.compile(r"\b(never|not|no|don't|avoid|forbidden?|instead of)\b", re.I)


def _is_prohibition(line: str) -> bool:
    return bool(_NEGATION_RE.search(re.sub(r"[*_`]", "", line)))


@pytest.mark.parametrize("path", IR_INJECTED_FILES)
def test_injected_catalogs_are_ir_native(path):
    """The AWS catalog was an XML-mode stencil table — literally
    ``style="shape=mxgraph.aws4.resourceIcon;resIcon=..."`` — injected straight
    into Network/Security/Data-flow, and the classify packs still spoke in
    ``parent="1"``. Catalogs are copied from, so any XML-era token is teaching
    the model the exact format the view packs forbid.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    found = [token for token in FORBIDDEN_IN_IR_PROMPTS if token in text]
    assert not found, f"{path} teaches draw.io XML-era syntax: {found}"


@pytest.mark.parametrize("name", sorted(IR_ASSEMBLIES))
def test_ir_prompts_only_mention_xml_syntax_to_forbid_it(name):
    """The assembled prompt may *prohibit* XML-era syntax but never present it.

    ``ir/network.txt`` says "Never a draw.io style, stencil path, or
    ``resIcon=`` string" and ``icons/saas.txt`` says "Do **not** invent
    ``resIcon=``" — both are correct and worth keeping, so this allows a token
    on a line that also carries a negation, and fails on a bare mention.
    """
    offenders = []
    for line in IR_ASSEMBLIES[name]().splitlines():
        if not any(token in line for token in FORBIDDEN_IN_IR_PROMPTS):
            continue
        if _is_prohibition(line):
            continue  # a prohibition, which is the point
        offenders.append(line.strip()[:110])
    assert not offenders, f"{name} IR prompt presents XML-era syntax:\n  " + "\n  ".join(offenders)


def test_3d_prompt_still_carries_stencil_syntax():
    """The inverse guard: 3D is a direct-XML path and *needs* stencil styles.

    Without this, the rule above could be 'satisfied' by stripping stencil
    vocabulary from the one path that legitimately requires it.
    """
    prompt = get_xml_gen_system_prompt(platform="aws", three_d=True)
    assert "mxgraph.aws3d" in prompt


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


def test_platform_catalogs_do_not_bleed_across_platforms():
    """Was asserted via 'NEVER use mxgraph.aws4', which is itself XML-era
    vocabulary the IR prompt should not contain. Platform isolation is better
    checked on the service names actually in each catalog."""
    gcp = get_network_ir_system_prompt(platform="gcp", user_hint="compute engine spanner")
    assert "Service Classification — GCP" in gcp
    assert "Service Classification — AWS" not in gcp

    aws = get_network_ir_system_prompt(platform="aws", user_hint="lambda dynamodb")
    assert "aws_lambda" in aws
    assert "gcp_compute_engine" not in aws


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
