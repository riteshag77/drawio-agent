"""Request payload schemas for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel


class PromptRequest(BaseModel):
    user_prompt: str
    provider: str = "openai"
    platform: str = "aws"
    diagram_mode: str = "auto"
    minimal_style: bool = False
    custom_system_message: str = ""
    context_text: str | None = None
    image_data: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider: str = "openai"
    validation_provider: str = "auto"
    platform: str = "aws"
    diagram_mode: str = "auto"
    current_architecture: dict | None = None
    previous_architecture: dict | None = None
    current_xml: str | None = None
    previous_xml: str | None = None
    minimal_style: bool = False
    custom_system_message: str = ""
    validation_feedback: str | None = None
    image_data: str | None = None
    context_text: str | None = None
    has_image_input: bool = False
    platform_convert: bool = False
    previous_platform: str | None = None


class ValidateDiagramRequest(BaseModel):
    image_data: str
    validation_provider: str = "auto"
    session_id: str | None = None


class ParseUrlRequest(BaseModel):
    url: str
