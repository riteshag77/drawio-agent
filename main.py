import ssl_config  # noqa: F401 — trust OS CA store before any HTTPS clients

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response, JSONResponse
from pydantic import BaseModel
import asyncio
import uvicorn
from dotenv import load_dotenv

from agent import (
    generate_architecture_from_chat,
    generate_architecture_json,
    MODEL_IDS,
    PLATFORM_LABELS,
)
from draftsman import generate_xml
from models import ArchitectureDiagram
from pipeline_log import StepLogger

# Force load the .env file immediately
load_dotenv()

app = FastAPI(title="Draw.io AI Architect")

# Mount static files and automatically redirect the root URL to the UI
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


class PromptRequest(BaseModel):
    user_prompt: str
    provider: str = "openai"
    platform: str = "aws"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider: str = "openai"
    platform: str = "aws"
    current_architecture: dict | None = None


async def _run_pipeline(
    *,
    user_prompt: str | None,
    messages: list[dict[str, str]] | None,
    provider: str,
    platform: str,
    current_architecture: ArchitectureDiagram | None,
    logger: StepLogger,
) -> dict:
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()

    logger.log(
        "request",
        "Generation request received",
        details={
            "platform": platform,
            "provider": provider,
            "model": MODEL_IDS.get(provider, MODEL_IDS["openai"]),
            "platform_label": PLATFORM_LABELS.get(platform, PLATFORM_LABELS["aws"]),
            "chat_turns": len(messages) if messages else 1,
        },
    )

    if messages:
        architecture_data, assistant_message = await asyncio.to_thread(
            generate_architecture_from_chat,
            messages,
            provider,
            platform,
            current_architecture,
            logger,
        )
    else:
        architecture_data = await asyncio.to_thread(
            generate_architecture_json,
            user_prompt or "",
            provider,
            platform,
            logger,
            None,
            current_architecture,
        )
        assistant_message = (
            f"Generated diagram with {len(architecture_data.nodes)} nodes "
            f"and {len(architecture_data.edges)} edges."
        )

    logger.log("layout", "Running auto-layout and XML builder")
    xml_output = await asyncio.to_thread(
        generate_xml,
        architecture_data,
        platform,
        logger,
    )

    logger.log(
        "complete",
        "Pipeline finished successfully",
        level="success",
        details={"xml_chars": len(xml_output)},
    )

    return {
        "status": "success",
        "suggestions": architecture_data.suggestions,
        "xml": xml_output,
        "architecture": architecture_data.model_dump(),
        "assistant_message": assistant_message,
        "logs": logger.to_list(),
    }


@app.post("/generate")
async def generate_diagram(request: PromptRequest):
    logger = StepLogger()
    try:
        result = await _run_pipeline(
            user_prompt=request.user_prompt,
            messages=None,
            provider=request.provider,
            platform=request.platform,
            current_architecture=None,
            logger=logger,
        )
        return result
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/chat")
async def chat_diagram(request: ChatRequest):
    logger = StepLogger()
    platform = (request.platform or "aws").strip().lower()
    provider = (request.provider or "openai").strip().lower()

    if not request.messages:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "messages must not be empty"},
        )
    if request.messages[-1].role != "user":
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Last message must be from the user"},
        )

    current_architecture = None
    if request.current_architecture:
        try:
            current_architecture = ArchitectureDiagram.model_validate(request.current_architecture)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": f"Invalid current_architecture: {e}"},
            )

    try:
        messages = [m.model_dump() for m in request.messages]
        result = await _run_pipeline(
            user_prompt=None,
            messages=messages,
            provider=provider,
            platform=platform,
            current_architecture=current_architecture,
            logger=logger,
        )
        return result
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/download")
async def download_diagram(request: PromptRequest):
    """Generate and return the diagram as a downloadable .drawio file."""
    try:
        architecture_data = generate_architecture_json(request.user_prompt, request.provider, request.platform)
        xml_output = generate_xml(architecture_data, request.platform)
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
