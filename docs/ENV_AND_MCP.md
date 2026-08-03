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
NVIDIA_API_KEY=
# Optional overrides for the default NVIDIA NIM provider (provider key: nvidia) — https://build.nvidia.com
# NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
# NVIDIA_MODEL=z-ai/glm-5.2
# Other build.nvidia.com catalog models are registered as separate provider keys
# sharing the same NVIDIA_API_KEY (no extra env vars needed):
#   nvidia_deepseek_pro    -> deepseek-ai/deepseek-v4-pro
#   nvidia_deepseek_flash  -> deepseek-ai/deepseek-v4-flash
#   nvidia_minimax_m2_7    -> minimaxai/minimax-m2.7
#   nvidia_minimax_m3      -> minimaxai/minimax-m3
PROVIDER_DEFAULT=gemini
LLM_TIMEOUT=120
MAX_CONCURRENT_LLM=5
# Multi-view + ADR/RAID fills 5 LLM slots. Raise to 6–7 if view repairs are common.
# Cost/WAF/MCP enrichment does not consume LLM slots.
VIEW_QUALITY_MIN_SCORE=75
# Optional provider override for Logical/Security/Data flow calls (empty = same as primary)
VIEW_LLM_PROVIDER=
LOG_FORMAT=text   # text | json
DRAWIO_AGENT_LOG_DIR=logs
LOG_RETENTION_DAYS=14

# Server
HOST=0.0.0.0
PORT=8000
APP_VERSION=1.0.0

# Generation path: IR + compiler vs direct XML
DIAGRAM_IR_ENABLED=true
DIAGRAM_IR_VIEWS=network,security,data_flow,logical

# Diagram history (versioning / restore)
DIAGRAM_HISTORY_ENABLED=true
DIAGRAM_HISTORY_PATH=data/diagram_history.sqlite3
DIAGRAM_HISTORY_MAX_VERSIONS=50

# Resilience
PROVIDER_FALLBACK_ORDER=          # e.g. gemini,openai — empty disables failover
RATE_LIMIT_PER_MINUTE=0           # per-client generation cap; 0 disables
TRUST_PROXY_HEADERS=false         # honour X-Forwarded-For for client identity

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
- Prompt assembly uses `config/xml_gen/ir/` (+ `classify/{platform}.txt` + `icons/saas.txt` + `prompt_3d.txt` + `docs/` + `gap_analysis.txt`). Legacy `core.txt` / `views/` XML packs are removed.
- **`DIAGRAM_VLM_QA`** — optional post-compile visual QA (default `false`). When on, runs layout diagnostics and at most one edge re-anchor corrective pass, then ships anyway (fail-open). Geometry stays with the IR compiler; this never regenerates XML via the LLM.
- **`MAX_CONCURRENT_LLM`** — caps simultaneous blocking LLM calls (default 5). Multi-view runs launch Logical/Security/Data flow (+ optional ADR/RAID) in parallel via `asyncio.gather` + `run_llm_sync`. Cost/WAF/MCP do not take LLM slots.
- **`VIEW_QUALITY_MIN_SCORE`** — focused views score against viewpoint rules. Below the gate, the pipeline runs **one IR corrective retry**, then keeps the best attempt and marks the feature degraded (primary page still succeeds). **`VIEW_LLM_PROVIDER`** optionally routes view calls to a different provider key.
- **Pricing** — LLM token rates and cloud service monthly estimates live in [`config/pricing.json`](../config/pricing.json). Vendor list prices are stored in **USD**; estimates shown in the UI are converted to **AUD** via `usd_to_aud` (edit that rate or the model/service rows there — no code change). Both the toolbar LLM cost chip and the Cost insights panel use `A$`.
- **`LOG_FORMAT=json`** — emit one JSON object per log line for structured log ingestion.
- **`DRAWIO_AGENT_LOG_DIR`** — directory for log files (default `logs`). Rotates daily at midnight into `drawio-agent.log.YYYY-MM-DD`; the active file stays at `drawio-agent.log`.
- **`LOG_RETENTION_DAYS`** — how many rotated daily log files to keep before the oldest is deleted (default `14`).
- **`APP_VERSION`** — exposed on `/health` (default `1.0.0`).

## IR generation path (`DIAGRAM_IR_ENABLED`)

Fresh cloud diagrams are **IR-only**: the model emits a closed, Pydantic-validated
IR (`app/domain/ir/`) — never coordinates, styles, or colours — and a deterministic
compiler (`app/adapters/outbound/diagram/compiler/`) renders the mxCells. Geometry
is owned by the compiler + ELK (`logical_layout.layout_corridor_ir` for corridor
views); `repair_xml` is structural hygiene + edge ports/overview only — it does
**not** re-layout IR pages.

| View | IR type | Shape | Who computes final geometry |
| --- | --- | --- | --- |
| Network | `NetworkIR` | strict container nesting (Cloud→Region→VPC→AZ→Subnet) | `compiler/network.py` + ELK |
| Logical / Security / Data flow | `LogicalIR` / `SecurityIR` / `DataFlowIR` (`CorridorIR`) | same-rank corridor row + optional shelves | `compiler/corridor.py` `run_layered` via `layout_corridor_ir` |

Leaf size is always `geometry_spec.LEAF_W/H` (78×78) with bottom labels. Post-passes
(`assign_logical_shape_types`, SaaS tagging) may change `shape_type`/style strings
but must not resize boxes.

Shared building blocks:

| Concern | Shared in |
| --- | --- |
| `Leaf`, `Flow`, `ExternalActor` | `domain/ir/base.py` |
| Corridor/shelf shape, id-vocabulary validation | `domain/ir/corridor.py` |
| Geometry constants (pitch, padding, gaps) | `diagram/geometry_spec.py` |
| mxCell emission | `diagram/compiler/emit.py` |
| Styles | `diagram/compiler/theme.py` + `palette.py` |
| Edge exit/entry + local routing | `edge_routing.fix_edge_anchors_from_geometry` |
| LLM-call + corrective-retry loop | `core/services/agent.py` `_generate_ir_with_corrections` |
| view → IR type dispatch | `domain/ir.IR_TYPES_BY_VIEW` |

- **Scope**: fresh generations only. Edit mode uses surgical XML ops when small;
  larger redesigns regenerate via IR. 3D uses `prompt_3d.txt` (structural repair only).
- **Failure behaviour**: fresh generation is IR-only — errors fail the request
  (no silent direct-XML fallback).
- **Observability**: `generation_path` is `ir` | `direct_xml` (surgical/3D);
  `renderer_used` is `mcp` | `local` (icons only).
- Enable with `DIAGRAM_IR_ENABLED=true` (default); `DIAGRAM_IR_VIEWS` defaults to
  `network,security,data_flow,logical`.

**Layout invariants (corridor views):** equalized row Y/H band after ELK;
`INTER_CONTAINER_GAP` (~110px) between columns; shelves stack under anchors;
External Actors align to the corridor band top.

## Diagram history (`/history`)

Every successful generation is versioned per session when the request carries a
`session_id`. Backed by SQLite (`DIAGRAM_HISTORY_PATH`), pruned to
`DIAGRAM_HISTORY_MAX_VERSIONS` per session. Writes are best-effort — a history
failure never affects the diagram response.

| Endpoint | Purpose |
| --- | --- |
| `GET /history/sessions` | Sessions with stored versions, newest activity first |
| `GET /history/sessions/{id}` | Version metadata for a session (no XML payloads) |
| `GET /history/versions/{id}` | One version including full XML, for restore |
| `DELETE /history/sessions/{id}` | Remove a session's history |

Distinct from `CAPTURES_DIR` (opt-in full request+response debug capture).

## Resilience

- **`PROVIDER_FALLBACK_ORDER`** — comma-separated provider keys tried in order
  when the primary fails. Applied at the `LlmPort` boundary, so every LLM call
  (primary generation, focused views, ADR/RAID, gap analysis) inherits it.
  Fails over only on vendor-attributable errors — quota/429, transient 5xx,
  missing API key — never on schema-validation failures, which every provider
  would fail identically. Empty (default) preserves current behaviour exactly.
- **`RATE_LIMIT_PER_MINUTE`** — per-client cap on `/chat`, `/chat/stream`,
  `/generate`, `/download`. Distinct from `MAX_CONCURRENT_LLM`, which caps total
  in-flight LLM calls process-wide but provides no fairness between clients.
  Sliding window held in memory; with multiple workers each process enforces its
  own share. Health/readiness/static/history stay reachable when throttled.
  Clients are keyed by `X-API-Key` when present, else client IP.
- **`TRUST_PROXY_HEADERS`** — when true, `X-Forwarded-For` identifies the client
  for rate limiting. Leave false unless the app is genuinely behind a trusted
  proxy: an untrusted client can otherwise forge the header and bypass limits.

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
