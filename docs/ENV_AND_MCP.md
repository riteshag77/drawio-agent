# Environment and Draw.io MCP setup

## Quick start

1. Edit project-root [`.env`](../.env).
2. Set at least one LLM key, e.g. `OPENAI_API_KEY=sk-...`
3. Install deps and run:

```powershell
pip install -r requirements.txt
python run.py
```

Open `http://localhost:8000/`.

## Ownership (what each piece owns)

| Piece | Owns | Does **not** own |
| --- | --- | --- |
| **LLM** | Full draw.io XML (structure + coordinates + labels) | Official icon catalogue resolution |
| **Prompt CORE + platform packs** | Layout hierarchy, classification, icon mechanics | Runtime MCP style lookup |
| **Draw.io MCP** | `search_shapes` → icon styles; optional `create_diagram(libavoid)` | Spatial meaning of VPC/AZ/subnet |
| **UI Render select** | Per-request `auto` / `mcp` / `local` | — |
| **Cursor skills** | Manual publish-quality `.drawio` in Cursor | FastAPI runtime |

```text
LLM XML → validate/fix → (parallel) cost/WAF/compliance + MCP styles
         → (optional) libavoid → UI iframe
```

## `.env` keys

```env
# LLM
OPENAI_API_KEY=
PROVIDER_DEFAULT=openai
LLM_TIMEOUT=120
MAX_CONCURRENT_LLM=4

# Server
HOST=0.0.0.0
PORT=8000

# Diagram: auto | mcp | local
DIAGRAM_RENDERER=auto
DRAWIO_MCP_URL=https://mcp.draw.io/mcp
DRAWIO_MCP_TIMEOUT=30
DRAWIO_MCP_CONCURRENCY=4
DRAWIO_MCP_LIBAVOID=false
DRAWIO_STYLE_CACHE_PATH=data/mcp_style_cache.sqlite3
```

- **`DIAGRAM_RENDERER=auto`** — MCP icon styles (cached + concurrent). Set `DRAWIO_MCP_LIBAVOID=true` for an extra create_diagram hop.
- **`local`** — skip MCP (offline).
- Prompt assembly uses `config/xml_gen_core.txt` + platform classify/icon packs (not the legacy monolithic multi-cloud table).

## Web app flow

1. Chat → intent (cloud vs simple Mermaid).
2. Cloud: LLM emits draw.io XML from CORE + platform packs.
3. Validate/normalize XML; run cost/WAF/compliance in parallel with MCP icon resolve.
4. Response includes `xml`, optional insights, and step `logs`. Prefer `POST /chat/stream` for progress.

## Cursor chat (skills)

```text
Use the aws-architecture-diagram skill to create …
Use the azure-diagrams skill for …
Use the drawio-skill for multi-cloud …
```

App MCP URL is independent of Cursor MCP config (`.cursor/mcp.json`).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Chat fails with missing API key | Fill `OPENAI_API_KEY` (or another provider) in `.env` |
| Always `renderer_used: local` | Check network to `DRAWIO_MCP_URL`; `pip install mcp` |
| Icons wrong but XML loads | MCP miss — try clearer service names; cache in `DRAWIO_STYLE_CACHE_PATH` |
