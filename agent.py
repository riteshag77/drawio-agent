import os
from typing import TYPE_CHECKING

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage   # ← replaces ChatPromptTemplate
from models import ArchitectureDiagram
from dotenv import load_dotenv

if TYPE_CHECKING:
    from pipeline_log import StepLogger

from cursor_provider import generate_with_cursor

load_dotenv()

# ── Model names ────────────────────────────────────────────────────────────────
# claude-sonnet-4-20250514 was retired 15 Jun 2026; use claude-sonnet-4-6 instead
MODEL_IDS = {
    "openai":  "gpt-4o",
    "gemini":  "gemini-2.5-flash",
    "claude":  "claude-sonnet-4-6",          # ← BUG 1 FIX: was claude-sonnet-4-20250514
    "cursor":  os.getenv("CURSOR_MODEL", "composer-2.5"),
}

SUPPORTED_PROVIDERS = ("openai", "gemini", "claude", "cursor")

PLATFORM_LABELS = {
    "aws":   "Amazon Web Services (AWS)",
    "gcp":   "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}


def get_system_prompt() -> str:
    """
    Reads the system prompt from an external file.

    No brace-escaping needed here — we pass this directly as a SystemMessage,
    so LangChain's template engine never sees it.   ← BUG 2 FIX
    """
    prompt_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt"
    )
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def _build_llm(provider: str):
    """Instantiate the correct LangChain LLM for the requested provider."""
    provider = provider.lower().strip()

    if provider == "gemini":
        if not os.getenv("GOOGLE_API_KEY"):
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        return ChatGoogleGenerativeAI(model=MODEL_IDS["gemini"], temperature=0)

    if provider == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
        return ChatAnthropic(model=MODEL_IDS["claude"], temperature=0)

    # Default: OpenAI
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    return ChatOpenAI(model=MODEL_IDS["openai"], temperature=0)


def generate_architecture_json(
    user_request: str,
    provider: str = "openai",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
) -> ArchitectureDiagram:
    """
    Generate an architecture diagram JSON using the selected AI provider.

    Args:
        user_request: Natural-language description of the architecture to generate.
        provider:     "openai" | "gemini" | "claude" | "cursor"
        platform:     "aws" | "gcp" | "azure" | "multi"

    Returns:
        A validated ArchitectureDiagram Pydantic model instance.
    """
    # BUG 4 FIX: use a different local name to avoid shadowing the parameter
    platform_key = (platform or "aws").strip().lower()
    platform_label = PLATFORM_LABELS.get(platform_key, PLATFORM_LABELS["aws"])
    provider_key = (provider or "openai").strip().lower()

    if logger:
        logger.log(
            "prompt",
            "Prepared LLM request",
            details={
                "platform": platform_key,
                "platform_label": platform_label,
                "provider": provider_key,
                "prompt_chars": len(user_request),
            },
        )

    system_prompt = get_system_prompt()
    if logger:
        logger.log(
            "system_prompt",
            "Loaded system prompt",
            details={"chars": len(system_prompt), "path": "system_prompt.txt"},
        )

    human_content = (
        f"Target cloud platform: {platform_label}\n\n"
        f"Architecture request:\n{user_request}"
    )

    if provider_key == "cursor":
        if logger:
            logger.log(
                "llm",
                "Using Cursor SDK provider",
                details={"model": MODEL_IDS["cursor"]},
            )
        return generate_with_cursor(
            system_prompt=system_prompt,
            user_prompt=human_content,
            model=MODEL_IDS["cursor"],
            logger=logger,
        )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]

    if logger:
        model_id = MODEL_IDS.get(provider_key, MODEL_IDS["openai"])
        logger.log(
            "llm",
            f"Initializing {provider_key} client",
            details={"model": model_id, "temperature": 0},
        )

    llm = _build_llm(provider)

    # BUG 3 FIX: invoke structured_llm directly instead of piping through
    # a RunnableSequence (prompt_template | structured_llm).
    # Direct invocation avoids ChatPromptValue → message conversion edge cases
    # that can break Claude and Gemini's tool-calling paths.
    structured_llm = llm.with_structured_output(ArchitectureDiagram)

    if logger:
        logger.log("llm", "Calling LLM for structured architecture JSON")

    result = structured_llm.invoke(messages)

    if logger:
        containers = sum(1 for node in result.nodes if node.is_container)
        logger.log(
            "architecture",
            "Architecture JSON validated",
            level="success",
            details={
                "nodes": len(result.nodes),
                "containers": containers,
                "services": len(result.nodes) - containers,
                "edges": len(result.edges),
                "suggestions": len(result.suggestions),
                "node_ids": [node.id for node in result.nodes],
            },
        )

    return result