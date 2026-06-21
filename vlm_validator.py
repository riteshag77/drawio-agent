"""
VLM-based diagram validation — port of next-ai-draw-io app/api/validate-diagram/route.ts
"""

from __future__ import annotations

import base64
import os
import re
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from validation_models import DEFAULT_VALID_RESULT, ValidationResult
from validation_prompts import VALIDATION_SYSTEM_PROMPT

if TYPE_CHECKING:
    from pipeline_log import StepLogger

MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB decoded
DEFAULT_TIMEOUT_S = 10


def _parse_image_data(image_data: str) -> bytes:
    """Accept data:image/png;base64,... or raw base64."""
    raw = (image_data or "").strip()
    if not raw:
        raise ValueError("Missing imageData")

    if raw.startswith("data:"):
        match = re.match(r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$", raw, re.DOTALL)
        if not match:
            raise ValueError("Invalid image data URL format")
        raw = match.group(1)

    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 image data") from exc

    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image too large (max {MAX_IMAGE_BYTES // (1024 * 1024)}MB)")

    return decoded


def _build_vision_llm():
    """Pick a vision-capable model (Gemini preferred, then OpenAI)."""
    if os.getenv("GOOGLE_API_KEY"):
        model = os.getenv("VALIDATION_MODEL", "gemini-2.5-flash")
        return ChatGoogleGenerativeAI(model=model, temperature=0)

    if os.getenv("OPENAI_API_KEY"):
        model = os.getenv("VALIDATION_MODEL", "gpt-4o")
        return ChatOpenAI(model=model, temperature=0)

    return None


def validate_diagram_image(
    image_data: str,
    logger: "StepLogger | None" = None,
) -> ValidationResult:
    """
    Analyze a rendered diagram PNG with a vision model.
    Fail-open (returns valid) when VLM is disabled or unavailable — matches next-ai-draw-io.
    """
    if os.getenv("ENABLE_VLM_VALIDATION", "true").lower() == "false":
        if logger:
            logger.log("validation", "VLM validation disabled (ENABLE_VLM_VALIDATION=false)", level="info")
        return DEFAULT_VALID_RESULT.model_copy()

    llm = _build_vision_llm()
    if llm is None:
        if logger:
            logger.log(
                "validation",
                "No vision model configured — skipping VLM validation",
                "warn",
            )
        return DEFAULT_VALID_RESULT.model_copy()

    try:
        image_bytes = _parse_image_data(image_data)
    except ValueError as exc:
        if logger:
            logger.log("validation", str(exc), level="error")
        raise

    if logger:
        logger.log(
            "validation",
            "Running VLM diagram validation",
            details={"image_bytes": len(image_bytes)},
        )

    structured = llm.with_structured_output(ValidationResult)
    b64 = base64.b64encode(image_bytes).decode("ascii")

    # LangChain multimodal content blocks
    human_content: list[dict] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        },
        {
            "type": "text",
            "text": "Please analyze this diagram for visual quality issues.",
        },
    ]

    try:
        result: ValidationResult = structured.invoke([
            SystemMessage(content=VALIDATION_SYSTEM_PROMPT),
            HumanMessage(content=human_content),  # type: ignore[arg-type]
        ])
    except Exception as exc:
        if logger:
            logger.log(
                "validation",
                f"VLM validation error (fail-open): {exc}",
                "warn",
            )
        return DEFAULT_VALID_RESULT.model_copy()

    if logger:
        logger.log(
            "validation",
            "VLM validation complete",
            level="success" if result.valid else "warn",
            details={
                "valid": result.valid,
                "issues": len(result.issues),
                "critical": sum(1 for i in result.issues if i.severity == "critical"),
            },
        )

    return result
