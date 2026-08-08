"""Request payload schemas for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.adapters.outbound.llm.providers import default_provider_key as _default_provider


class PromptRequest(BaseModel):
    user_prompt: str = Field(max_length=50_000)
    provider: str = Field(default_factory=_default_provider)
    platform: str = "aws"
    diagram_mode: str = "auto"
    custom_system_message: str = Field(default="", max_length=10_000)
    diagram_renderer: str | None = None
    three_d: bool = False
    cost_estimate: bool = False
    well_architected: bool = False
    compliance: bool = False
    compliance_frameworks: list[str] | None = None
    libavoid: bool | None = None
    image_replicate: bool = False
    animated_connectors: bool = False
    suggestions: bool = False
    architecture_interview: bool = False
    diagram_views: list[str] | None = None
    adr_notes: bool = False
    raid_register: bool = False
    interview_answers: dict[str, str] | None = None


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=200_000)


class AttachmentDocument(BaseModel):
    filename: str = Field(max_length=255)
    mime: str
    data: str  # base64-encoded file bytes


class CustomProviderConfig(BaseModel):
    """A per-request bring-your-own-key provider — never persisted server-side.

    Stored client-side only (browser localStorage); sent fresh on each request
    that opts into it. See app/adapters/outbound/llm/providers.py's
    using_custom_provider/_spec_from_custom_payload for how this becomes an
    ephemeral (uncached) ProviderSpec for the duration of one request.
    """

    label: str = Field(default="Custom", max_length=80)
    client: Literal["openai", "anthropic", "google"]
    api_key: str = Field(max_length=500)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(max_length=200)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=100)
    provider: str = Field(default_factory=_default_provider)
    model: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Selects one of `provider`'s registered `models` (e.g. NVIDIA NIM offers "
            "several under one provider entry). Ignored for single-model providers and "
            "for custom_provider (which already names its own model)."
        ),
    )
    platform: str = "aws"
    diagram_mode: str = "auto"
    custom_system_message: str = Field(default="", max_length=10_000)
    applied_suggestions: list[str] = []
    diagram_renderer: str | None = None
    three_d: bool = False
    cost_estimate: bool = False
    well_architected: bool = False
    compliance: bool = False
    compliance_frameworks: list[str] | None = None
    context_text: str | None = Field(default=None, max_length=20_000)
    attachment_documents: list[AttachmentDocument] | None = Field(default=None, max_length=3)
    current_xml: str | None = Field(default=None, max_length=1_500_000)
    libavoid: bool | None = None
    image_replicate: bool = False
    image_mode: Literal["as_is", "cloud"] = Field(
        default="as_is",
        description=(
            "How an uploaded image/PDF is turned into a diagram. 'as_is' (default) "
            "reproduces its shapes/lines/layout faithfully via the generic IR path, "
            "with no cloud-service interpretation. 'cloud' routes it through the "
            "existing cloud IR pipeline instead (fidelity governed by image_replicate)."
        ),
    )
    animated_connectors: bool = False
    suggestions: bool = False
    architecture_interview: bool = False
    diagram_views: list[str] | None = None
    adr_notes: bool = False
    raid_register: bool = False
    save_captures: bool = False
    interview_answers: dict[str, str] | None = None
    custom_provider: CustomProviderConfig | None = Field(
        default=None,
        description=(
            "A user-supplied provider (own API key/base_url/model) for this request only. "
            "When present, overrides `provider` — the pipeline routes to it regardless of "
            "what `provider` is set to."
        ),
    )
    session_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Client-supplied conversation id. When present, each successful generation is "
            "appended as a new version under this id and is retrievable via /history."
        ),
    )


class AnalyzeRequest(BaseModel):
    """Re-run Well-Architected/compliance checks against an already-generated
    diagram — e.g. the user turns the toggle on after the diagram already
    exists, rather than only picking up the setting on the next chat turn."""

    xml: str = Field(max_length=1_500_000)
    platform: str = "aws"
    provider: str = Field(default_factory=_default_provider)
    cost_estimate: bool = False
    well_architected: bool = True
    compliance: bool = True
    compliance_frameworks: list[str] | None = None
    suggestions: bool = False
    view: str = "network"
    human_prompt: str = Field(default="", max_length=4000)
    applied_suggestions: list[str] = []


class ParseUrlRequest(BaseModel):
    url: str = Field(max_length=2048)


class TemplateCreate(BaseModel):
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=400)
    prompt: str = Field(max_length=20_000)


class TemplateUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=400)
    prompt: str | None = Field(default=None, max_length=20_000)
    pinned: bool | None = None


class ValidateVisualRequest(BaseModel):
    image_base64: str = Field(max_length=8_000_000)
    xml: str = Field(max_length=1_500_000)
    provider: str = Field(default_factory=_default_provider)
