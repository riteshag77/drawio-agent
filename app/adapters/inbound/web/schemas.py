"""Request payload schemas for the HTTP API."""

from __future__ import annotations

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


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=100)
    provider: str = Field(default_factory=_default_provider)
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
    animated_connectors: bool = False
    suggestions: bool = False
    architecture_interview: bool = False
    diagram_views: list[str] | None = None
    adr_notes: bool = False
    raid_register: bool = False
    save_captures: bool = False
    interview_answers: dict[str, str] | None = None
    session_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Client-supplied conversation id. When present, each successful generation is "
            "appended as a new version under this id and is retrievable via /history."
        ),
    )


class ParseUrlRequest(BaseModel):
    url: str = Field(max_length=2048)
