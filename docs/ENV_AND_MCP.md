# Environment and Draw.io MCP setup

## Quick start

1. Edit project-root [`.env`](../.env) (created for you; also see [`.env.example`](../.env.example)).
2. Set at least one LLM key, e.g. `OPENAI_API_KEY=sk-...`
3. Install deps and run:

```powershell
pip install -r requirements.txt
python run.py
```

Open `http://localhost:8000/`.

## What each part does

| Piece | Role |
| --- | --- |
| **LLM** (`.env` API keys) | Produces architecture JSON (nodes, edges, flow) |
| **Draw.io MCP** (`DRAWIO_MCP_URL`) | Resolves official icon styles via `search_shapes`, then local layout builds XML |
| **UI “Render” select** | `auto` / `mcp` / `local` — overrides `DIAGRAM_RENDERER` per request |
| **Cursor skills** | Manual publish-quality `.drawio` in Cursor chat (not called by the server) |

## `.env` keys

Project [`.env`](../.env) holds **all** local settings (LLM, server, layout, bands, scaffolds, MCP). [`.env.example`](../.env.example) is the same template without secrets.

Important groups:

```env
# LLM
OPENAI_API_KEY=
PROVIDER_DEFAULT=openai

# Server
HOST=0.0.0.0
PORT=8000

# Diagram: auto | mcp | local
DIAGRAM_RENDERER=auto
DRAWIO_MCP_URL=https://mcp.draw.io/mcp
DRAWIO_MCP_TIMEOUT=30

# Layout (required for local renderer — do not delete)
LEAF_NODE_W=60
LEAF_NODE_H=60
CONTAINER_PAD=28
IGW_VPC_Y=-30
FLOW_NOTES_GAP=48
BAND_GAP=24
SCAFFOLD_VPC_W=980
QUALITY_GATES_BLOCKING=true
```

- **`DIAGRAM_RENDERER=auto`** — try MCP; on failure use local styles (recommended).
- **`local`** — never call MCP (offline).
- **`mcp`** — prefer MCP; still falls back to local if the server errors.

If you overwrite `.env`, restore layout keys from `.env.example` or local defaults will still apply in code — but keeping them in `.env` matches the previous project setup.

## Web app flow

1. Chat in the browser → LLM builds architecture.
2. If renderer is `auto`/`mcp`, FastAPI calls Draw.io MCP `search_shapes` for each leaf `shape_type`.
3. Styles are injected into the existing layout engine → Draw.io XML → iframe.
4. Response includes `renderer_used` (`mcp` or `local`) and optional `mcp_preview_url`.

## Cursor chat (skills)

Same repo, separate path for stakeholder diagrams:

```text
Use the aws-architecture-diagram skill to create …
Use the azure-diagrams skill for …
Use the drawio-skill for multi-cloud …
```

Cursor MCP config: [`.cursor/mcp.json`](../.cursor/mcp.json) (agent-side). The **app** uses `DRAWIO_MCP_URL` from `.env` via the Python MCP client independently.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Chat fails with missing API key | Fill `OPENAI_API_KEY` (or another provider) in `.env` |
| Always `renderer_used: local` | Check network to `DRAWIO_MCP_URL`; `pip install mcp`; set Render to Auto |
| Icons wrong but XML loads | MCP returned no match — local catalogue used; try clearer shape names |
| Cursor MCP not connected | Cursor Settings → MCP → reload; confirm `.cursor/mcp.json` |
