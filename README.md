# Draw.io AI Architect

AI-powered cloud architecture diagrams. Describe infrastructure in natural language and get a live, editable Draw.io diagram (AWS / GCP / Azure / multi-cloud), with optional cost, Well-Architected, and compliance insights.

## Run locally

1. Configure [`.env`](.env) (see **[docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md)**).
2. Set at least one LLM API key (`OPENAI_API_KEY`, etc.).
3. Install and start:

```powershell
pip install -r requirements.txt
python run.py
```

Open `http://localhost:8000/`. Use **Render** for `Auto (MCP)` / `MCP` / `Local`.

| Piece | Role |
| --- | --- |
| **LLM** | Emits draw.io **XML** directly (structure, labels, coordinates) |
| **Prompt packs** | CORE layout rules + per-platform icon/classification packs (token-efficient) |
| **Draw.io MCP** | Official icon styles via `search_shapes`; optional libavoid routing |
| **Cursor skills** | Optional publish-quality `.drawio` in agent chat ([docs/CLOUD_DIAGRAM_SKILLS.md](docs/CLOUD_DIAGRAM_SKILLS.md)) |

---

## Architecture Overview

```
┌──────────────┐  POST /chat[/stream]  ┌─────────────────────────────┐
│  Frontend    │ ────────────────────> │  FastAPI (app/)              │
│  HTML/JS +   │                       │  intent → XML or Mermaid    │
│  draw.io     │ <──────────────────── │  xml_pipeline               │
│  iframe      │  xml + insights       │    LLM → validate →         │
└──────────────┘                       │    cost/WAF/compliance ∥ MCP│
                                       └─────────────────────────────┘
```

Pipeline: [`app/core/services/xml_pipeline.py`](app/core/services/xml_pipeline.py)  
Prompts: [`config/xml_gen_core.txt`](config/xml_gen_core.txt) + `xml_gen_classify_*.txt` + `xml_gen_icons_*.txt`

### API

| Endpoint | Purpose |
|----------|---------|
| `POST /chat` | Multi-turn diagram generation |
| `POST /chat/stream` | Same with SSE progress events |
| `POST /generate` | Single-turn generate |
| `POST /download` | Return `.drawio` XML attachment |
| `POST /parse-url` | Fetch URL text for context |
| `GET /health` | Health check |

### UI notes

- Toast notifications for errors/success (visible without opening logs)
- Insights tabs: Cost | WAF | Compliance | Logs
- Export: Download `.drawio`, PNG, Copy XML
- Session version chips restore prior successful XML
- File attach: PDF/text only (image multimodal is backlog)

### Backlog (not wired yet)

- `current_xml` / `context_text` into the LLM human prompt
- Terraform import API
- Multimodal image → diagram

For environment keys and MCP ownership, see [docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md).
