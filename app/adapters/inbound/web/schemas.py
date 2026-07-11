"""Request payload schemas for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromptRequest(BaseModel):
    user_prompt: str = Field(max_length=50_000)
    provider: str = "openai"
    platform: str = "aws"
    diagram_mode: str = "auto"
    minimal_style: bool = False
    custom_system_message: str = Field(default="", max_length=10_000)
    context_text: str | None = Field(default=None, max_length=50_000)
    image_data: str | None = None
    diagram_renderer: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=50_000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(max_length=100)
    provider: str = "openai"
    validation_provider: str = "auto"
    platform: str = "aws"
    diagram_mode: str = "auto"
    current_architecture: dict | None = None
    previous_architecture: dict | None = None
    current_xml: str | None = Field(default=None, max_length=500_000)
    previous_xml: str | None = Field(default=None, max_length=500_000)
    minimal_style: bool = False
    custom_system_message: str = Field(default="", max_length=10_000)
    validation_feedback: str | None = Field(default=None, max_length=20_000)
    image_data: str | None = None
    context_text: str | None = Field(default=None, max_length=50_000)
    has_image_input: bool = False
    platform_convert: bool = False
    previous_platform: str | None = None
    applied_suggestions: list[str] = []
    diagram_renderer: str | None = None


class ValidateDiagramRequest(BaseModel):
    image_data: str
    validation_provider: str = "auto"
    session_id: str | None = None


class ParseUrlRequest(BaseModel):
    url: str = Field(max_length=2048)
