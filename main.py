from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

from agent import generate_architecture_json
from draftsman import generate_xml

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
    try:
        # Pass the prompt, the chosen AI provider, and target cloud platform to LangChain
        architecture_data = generate_architecture_json(request.user_prompt, request.provider, request.platform)

        # Convert JSON to XML, customized with the platform theme
        xml_output = generate_xml(architecture_data, request.platform)

        return {
            "status": "success",
            "suggestions": architecture_data.suggestions,
            "xml": xml_output
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
