"""Request payload schemas for the HTTP API."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


def _default_provider() -> str:
    """Prefer Claude for the rule-dense cloud/XML prompt; fall back to OpenAI
    if ANTHROPIC_API_KEY isn't configured, so an unconfigured deployment
    doesn't fail every request that omits ``provider``."""
    return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "openai"


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


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=50_000)


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


class ParseUrlRequest(BaseModel):
    url: str = Field(max_length=2048)
