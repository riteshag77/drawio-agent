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
| **LLM** | Structure, `parent`, geometry, container/group styles, edges, leaf **placeholder** + `shape_type` | Official leaf icon stencils (`resIcon` / `mxgraph.gcp2` / Azure SVG) |
| **Prompt CORE + platform packs** | Layout hierarchy, classification, group styles | Runtime MCP style lookup |
| **Draw.io MCP** | `search_shapes` → leaf icon styles (merged into placeholder); optional `create_diagram(libavoid)` | Nesting / VPC spatial meaning |
| **App merge** | Merges MCP stencil into leaf `style` while keeping LLM label attrs | — |
| **UI Render select** | Per-request `auto` / `mcp` / `local` | — |
| **Cursor skills** | Manual publish-quality `.drawio` in Cursor | FastAPI runtime |

```text
LLM XML (placeholder + shape_type on leaves)
  → validate/fix
  → (parallel) cost/WAF/compliance ∥ MCP search_shapes
  → merge MCP styles into leaves (preserve labels)
  → (default) libavoid → UI iframe
```

## `.env` keys

```env
# LLM
OPENAI_API_KEY=
# OPENAI_MODEL=gpt-4o
ANTHROPIC_API_KEY=
# CLAUDE_MODEL=claude-sonnet-4-6
GOOGLE_API_KEY=
# GEMINI_MODEL=gemini-2.5-flash
OPENROUTER_API_KEY=
OPENCODE_API_KEY=
MOONSHOT_API_KEY=
# Optional overrides for Kimi K3 (provider key: kimi)
# MOONSHOT_BASE_URL=https://api.moonshot.ai/v1
# KIMI_MODEL=kimi-k3
PROVIDER_DEFAULT=gemini
LLM_TIMEOUT=120
MAX_CONCURRENT_LLM=5
# Multi-view + ADR/RAID fills 5 LLM slots. Raise to 6–7 if view repairs are common.
# Cost/WAF/MCP enrichment does not consume LLM slots.
VIEW_QUALITY_MIN_SCORE=75
VIEW_REPAIR_MAX_PASSES=1
# Optional provider override for Logical/Security/Data flow calls (empty = same as primary)
VIEW_LLM_PROVIDER=
LOG_FORMAT=text   # text | json
DRAWIO_AGENT_LOG_DIR=logs
LOG_RETENTION_DAYS=14
XML_REPAIR_MAX_ATTEMPTS=1

# Server
HOST=0.0.0.0
PORT=8000
APP_VERSION=1.0.0

# Diagram: auto | mcp | local
DIAGRAM_RENDERER=auto
DRAWIO_MCP_URL=https://mcp.draw.io/mcp
DRAWIO_MCP_TIMEOUT=30
DRAWIO_MCP_CONCURRENCY=4
DRAWIO_MCP_LIBAVOID=true
DRAWIO_STYLE_CACHE_PATH=data/mcp_style_cache.sqlite3
# Set to true once after icon-logic changes, then set back to false
DRAWIO_STYLE_CACHE_RESET=false
```

- **`DIAGRAM_RENDERER=auto`** — MCP icon styles (cached + concurrent). Libavoid edge routing runs by default when MCP is used (`DRAWIO_MCP_LIBAVOID=true`); set `false` to skip the extra hop. Per-request override: `libavoid` on `/chat`.
- **`local`** — skip MCP (offline); leaves stay as placeholders unless the LLM invented stencils.
- Agent MCP wrapper (optional): with the API running, `python -m app.mcp_server` exposes `create_architecture_diagram` / `edit_architecture_diagram` / `get_providers` for Cursor (`DRAWIO_AGENT_URL` defaults to `http://127.0.0.1:8000`).
- **`DRAWIO_STYLE_CACHE_RESET=true`** — clears memory + SQLite style cache at process startup (use after changing MCP pick logic or prompts). Turn off afterward so cache stays warm.
- Manual clear: delete `data/mcp_style_cache.sqlite3` and restart.
- Prompt assembly uses `config/xml_gen/` packs (`core.txt` shared craft + `network.txt` for Network nesting + `classify/{platform}.txt` + `icons/{platform}.txt` + `views/` + `docs/`).
- **`MAX_CONCURRENT_LLM`** — caps simultaneous blocking LLM calls (default 5). Multi-view runs launch Logical/Security/Data flow (+ optional ADR/RAID) in parallel via `asyncio.gather` + `run_llm_sync`; each view's optional quality-repair hop reuses the same semaphore inside that view's task (does not serialize the three authors). Cost/WAF/MCP do not take LLM slots.
- **`VIEW_QUALITY_MIN_SCORE`** / **`VIEW_REPAIR_MAX_PASSES`** — focused views score against viewpoint rules; below the min score triggers at most N LLM repair passes (0 disables). **`VIEW_LLM_PROVIDER`** optionally routes view calls to a different provider key.
- **Pricing** — LLM token rates and cloud service monthly estimates live in [`config/pricing.json`](../config/pricing.json). Vendor list prices are stored in **USD**; estimates shown in the UI are converted to **AUD** via `usd_to_aud` (edit that rate or the model/service rows there — no code change). Both the toolbar LLM cost chip and the Cost insights panel use `A$`.
- **`LOG_FORMAT=json`** — emit one JSON object per log line for structured log ingestion.
- **`DRAWIO_AGENT_LOG_DIR`** — directory for log files (default `logs`). Rotates daily at midnight into `drawio-agent.log.YYYY-MM-DD`; the active file stays at `drawio-agent.log`.
- **`LOG_RETENTION_DAYS`** — how many rotated daily log files to keep before the oldest is deleted (default `14`).
- **`XML_REPAIR_MAX_ATTEMPTS`** — max corrective LLM repair passes when deterministic repair leaves structural errors unresolved (default `1`). Bounded so a diagram whose errors the model can't actually fix doesn't loop through repeated LLM calls burning API budget; raise it only if you want extra attempts on a flaky provider.
- **`APP_VERSION`** — exposed on `/health` (default `1.0.0`).

## `/ready` and `/health` endpoints

- **`GET /health`** — always returns `{"status": "ok", "version": "..."}`.
- **`GET /ready`** — returns 200 if at least one LLM API key is set and the style-cache path is writable; returns 503 otherwise.

## Web app flow

1. Chat → intent (cloud vs simple Mermaid).
2. Cloud: LLM emits draw.io XML; service leaves use `shape_type` + grey placeholder style.
3. Validate/normalize; MCP resolves official icons and **merges** them into leaf styles (label attrs kept).
4. MCP misses leave the placeholder and log `mcp_miss` — no half-overwritten styles.
5. Prefer `POST /chat/stream` for progress.

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
| Grey placeholder icons | Check pipeline logs for `mcp_miss`; clearer `shape_type` names help |
| Icons wrong after a prompt change | Set `DRAWIO_STYLE_CACHE_RESET=true` once, or delete `data/mcp_style_cache.sqlite3`, then restart |
| Blank AWS icons | Ensure leaves use `shape_type` placeholders, not invented `resIcon=` (LLM+MCP ownership split) |
