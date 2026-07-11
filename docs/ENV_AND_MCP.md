# Environment and Draw.io MCP setup

## Quick start

1. Edit project-root [`.env`](../.env) (created for you; also see [`.env.example`](../.env.example) if present).
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
| **LLM** | Architecture JSON (nodes, edges, labels, `parent_id`) | x/y coordinates, icon styles |
| **Local layout** | Domain topology + coarse placement: users outside cloud, AZ wrap, Public→App→Data stack, bands, IGW clamp, leaf size `48×48` | Edge path geometry, official AWS/GCP/Azure icon catalogue |
| **Draw.io MCP** | `search_shapes` → icon styles; `create_diagram(routing=libavoid)` → edge routing **only when it returns mxGraph XML** | VPC/AZ/subnet spatial meaning (do not use `postLayout=elk`) |
| **UI “Render” select** | Per-request `auto` / `mcp` / `local` override of `DIAGRAM_RENDERER` | — |
| **Cursor skills** | Manual publish-quality `.drawio` in Cursor chat | FastAPI runtime |

```text
LLM JSON → topology repair → local AZ/tier placement → MCP styles
         → (optional) MCP libavoid XML if returned → UI iframe
```

If MCP returns only a preview URL (no XML), the app **keeps local edges** and does not pretend routing improved.

## `.env` keys

Project [`.env`](../.env) holds local settings (LLM, server, layout, bands, scaffolds, MCP).

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

# Layout (local domain placement — do not delete)
LEAF_NODE_W=48
LEAF_NODE_H=48
CONTAINER_PAD=28
IGW_VPC_Y=-30
QUALITY_GATES_BLOCKING=true
```

- **`DIAGRAM_RENDERER=auto`** — MCP styles + try libavoid XML; fall back to local styles/edges.
- **`local`** — never call MCP (offline).
- **`mcp`** — same as auto for styles/routing; still falls back if MCP errors.

## Web app flow

1. Chat → LLM builds architecture.
2. Topology repair (users outside cloud, AZ hierarchy, bands).
3. Local layout places containers/leaves.
4. If renderer is `auto`/`mcp`, FastAPI calls `search_shapes` for leaf styles, then `create_diagram` with `routing=libavoid`.
5. Use returned XML only when present; otherwise keep local XML.
6. Response includes `renderer_used` and optional `mcp_preview_url` (only when routed XML was applied).

## Cursor chat (skills)

Same repo, separate path for stakeholder diagrams:

```text
Use the aws-architecture-diagram skill to create …
Use the azure-diagrams skill for …
Use the drawio-skill for multi-cloud …
```

Cursor MCP config: [`.cursor/mcp.json`](../.cursor/mcp.json). The **app** uses `DRAWIO_MCP_URL` from `.env` independently.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Chat fails with missing API key | Fill `OPENAI_API_KEY` (or another provider) in `.env` |
| Always `renderer_used: local` | Check network to `DRAWIO_MCP_URL`; `pip install mcp`; set Render to Auto |
| Icons wrong but XML loads | MCP returned no match — local catalogue fallback; try clearer shape names |
| Edges still look local | MCP did not return XML (preview-only) — expected; containment layout stays local |
| Cursor MCP not connected | Cursor Settings → MCP → reload; confirm `.cursor/mcp.json` |
