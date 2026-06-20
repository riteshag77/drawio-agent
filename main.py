from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response, JSONResponse
from pydantic import BaseModel
import asyncio
import uvicorn
from dotenv import load_dotenv

from agent import generate_architecture_json, MODEL_IDS, PLATFORM_LABELS
from draftsman import generate_xml
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


@app.post("/generate")
async def generate_diagram(request: PromptRequest):
    logger = StepLogger()
    platform = (request.platform or "aws").strip().lower()
    provider = (request.provider or "openai").strip().lower()

    try:
        logger.log(
            "request",
            "Generation request received",
            details={
                "platform": platform,
                "provider": provider,
                "model": MODEL_IDS.get(provider, MODEL_IDS["openai"]),
                "platform_label": PLATFORM_LABELS.get(platform, PLATFORM_LABELS["aws"]),
            },
        )

        architecture_data = await asyncio.to_thread(
            generate_architecture_json,
            request.user_prompt,
            request.provider,
            request.platform,
            logger,
        )

        logger.log("layout", "Running auto-layout and XML builder")
        xml_output = await asyncio.to_thread(
            generate_xml,
            architecture_data,
            request.platform,
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
            "logs": logger.to_list(),
        }
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
