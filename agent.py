import os
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from models import ArchitectureDiagram
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


def get_system_prompt():
    """Reads the system prompt from an external text file."""
    prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def generate_architecture_json(
    user_request: str,
    provider: str = "openai",
    platform: str = "aws",
) -> ArchitectureDiagram:
    """
    Generate an architecture diagram JSON using the selected AI provider.
    """
    system_prompt_text = get_system_prompt()
    platform = (platform or "aws").lower()
    platform_labels = {
        "aws": "Amazon Web Services (AWS)",
        "gcp": "Google Cloud Platform (GCP)",
        "azure": "Microsoft Azure",
        "multi": "Multi-Cloud / Hybrid",
    }
    platform_label = platform_labels.get(platform, platform_labels["aws"])

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt_text),
        ("user", "Target cloud platform: {platform}\n\nArchitecture request:\n{user_request}")
    ])

    if provider == "gemini":
        if not os.getenv("GOOGLE_API_KEY"):
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    elif provider == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
        llm = ChatAnthropic(model="claude-sonnet-4-20250514", temperature=0)
    else:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        llm = ChatOpenAI(model="gpt-4o", temperature=0)

    # Enforce structured output to match the ArchitectureDiagram Pydantic model
    structured_llm = llm.with_structured_output(ArchitectureDiagram)
    chain = prompt_template | structured_llm

    return chain.invoke({"user_request": user_request, "platform": platform_label})
