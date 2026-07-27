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

# Generation path: IR + compiler vs direct XML
DIAGRAM_IR_ENABLED=false
DIAGRAM_IR_VIEWS=network

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
- Prompt assembly uses `config/xml_gen/` packs (`core.txt` shared craft + `views/network.txt` for Network nesting + `classify/{platform}.txt` + `icons/{platform}.txt` + other `views/` + `docs/`).
- **`MAX_CONCURRENT_LLM`** — caps simultaneous blocking LLM calls (default 5). Multi-view runs launch Logical/Security/Data flow (+ optional ADR/RAID) in parallel via `asyncio.gather` + `run_llm_sync`; each view's optional quality-repair hop reuses the same semaphore inside that view's task (does not serialize the three authors). Cost/WAF/MCP do not take LLM slots.
- **`VIEW_QUALITY_MIN_SCORE`** / **`VIEW_REPAIR_MAX_PASSES`** — focused views score against viewpoint rules; below the min score triggers at most N LLM repair passes (0 disables). **`VIEW_LLM_PROVIDER`** optionally routes view calls to a different provider key.
- **Pricing** — LLM token rates and cloud service monthly estimates live in [`config/pricing.json`](../config/pricing.json). Vendor list prices are stored in **USD**; estimates shown in the UI are converted to **AUD** via `usd_to_aud` (edit that rate or the model/service rows there — no code change). Both the toolbar LLM cost chip and the Cost insights panel use `A$`.
- **`LOG_FORMAT=json`** — emit one JSON object per log line for structured log ingestion.
- **`DRAWIO_AGENT_LOG_DIR`** — directory for log files (default `logs`). Rotates daily at midnight into `drawio-agent.log.YYYY-MM-DD`; the active file stays at `drawio-agent.log`.
- **`LOG_RETENTION_DAYS`** — how many rotated daily log files to keep before the oldest is deleted (default `14`).
- **`XML_REPAIR_MAX_ATTEMPTS`** — max corrective LLM repair passes when deterministic repair leaves structural errors unresolved (default `1`). Bounded so a diagram whose errors the model can't actually fix doesn't loop through repeated LLM calls burning API budget; raise it only if you want extra attempts on a flaky provider.
- **`APP_VERSION`** — exposed on `/health` (default `1.0.0`).

## IR generation path (`DIAGRAM_IR_ENABLED`)

An alternative to having the LLM write draw.io XML directly, for **all four**
views (Network, Logical, Security, Data flow). The model instead emits a
small, closed, Pydantic-validated IR (`app/domain/ir/`) — never coordinates,
styles, or colours — and a deterministic compiler
(`app/adapters/outbound/diagram/compiler/`) renders the mxCells.

The IR shape differs by view, because the underlying layout problem does:

| View | IR type | Shape | What already handled geometry |
| --- | --- | --- | --- |
| Network | `NetworkIR` | strict container nesting (Cloud→Region→VPC→AZ→Subnet) | nothing — geometry was previously the LLM's job and load-bearing |
| Logical / Security / Data flow | `LogicalIR` / `SecurityIR` / `DataFlowIR`, sharing `CorridorIR` | a same-rank row of corridors + optional shelves anchored below | `logical_layout.py`'s rank-and-order layout engine, called unconditionally by `repair_xml` for these three page names — it already discarded whatever geometry the LLM wrote |

That distinction matters: Network's IR exists to eliminate a *correctness*
risk (bad geometry shipping to users). The corridor views' IR exists to
eliminate *waste* (prompt tokens teaching pixel arithmetic a downstream engine
was always going to throw away) and to make *grouping* mistakes (a leaf in the
wrong domain/zone/phase, a flow into a zone it must never reach) structural
validation errors instead of self-check items an LLM can skip.

Shared building blocks (the DRY payoff of splitting compiler concerns into
small modules rather than one per view):

| Concern | Shared in |
| --- | --- |
| `Leaf`, `Flow`, `ExternalActor` | `domain/ir/base.py` |
| Corridor/shelf shape, id-vocabulary validation | `domain/ir/corridor.py` |
| Geometry constants (pitch, padding, gaps) | `diagram/geometry_spec.py` — read by **both** the compiler and `xml_diagnostics`, so they can't drift apart |
| mxCell emission | `diagram/compiler/emit.py` |
| Styles | `diagram/compiler/theme.py` |
| LLM-call + corrective-retry loop | `core/services/agent.py`'s `_generate_ir_with_corrections` — one implementation, used by `generate_network_ir` and `generate_corridor_ir` |
| view → IR type dispatch | `domain/ir.IR_TYPES_BY_VIEW` |

Everything downstream of generation — validation, MCP icon resolution,
libavoid, cost/WAF/compliance enrichment, the `guarantee_loadable_xml` circuit
breaker, and (for the corridor views) the existing layout/scoring/repair
pipeline — is identical regardless of which path produced the page; the IR
path just hands the same `XmlDiagram` to it.

- **Scope**: fresh generations only. Edit mode (`current_xml` present) and 3D
  fall through to direct XML for every view — the IR cannot express "preserve
  these exact ids and coordinates" or isometric projection.
- **Both roles**: a view generated via IR whether it's the *primary* page or a
  *secondary* (extra) page in a multi-view request — same eligibility check,
  same generator, same fallback contract in both call sites.
- **Failure behaviour**: any error in the IR path (primary or extra-view) logs
  a warning and falls back to direct XML for that page. It can never turn a
  working request into a failed one.
- **No wasted repair passes**: an extra view generated via IR skips the
  LLM repair-pass loop (`VIEW_REPAIR_MAX_PASSES`) entirely — the invariants
  `score_view` checks are already guaranteed by IR validation, so a low score
  there would mean a scoring rule needs attention, not the diagram.
- **Observability**: responses carry `generation_path` (`ir` | `direct_xml`)
  for the primary page, distinct from `renderer_used` (`mcp` | `local`, which
  is about icon resolution only).
- Enable with `DIAGRAM_IR_ENABLED=true`; `DIAGRAM_IR_VIEWS` (comma-separated)
  picks which views use it, e.g. `DIAGRAM_IR_VIEWS=network,logical,security,data_flow`.
  Default is `network` only — the three corridor views are opt-in until you've
  run them against real traffic.

**Known pre-existing gap in the shared layout engine** (found while building
this, not introduced by it): when Data-flow's `phase_consume` *and*
`phase_saas` are both present (both legitimately anchor under `phase_store`,
stacked), `logical_layout.apply_layered_corridor_layout` can land them at
identical coordinates when combined with SaaS-leaf hoisting — confirmed by
isolating the layout function (stacks correctly alone) versus the full
`enforce_view_journey_columns` pipeline (collides). Separately, `phase_control`
(meant to anchor below the *whole* row) has no such placement mode in the
engine at all — it falls into the main ranked row instead. Both predate this
IR work and affect the direct-XML path too (just rarely triggered, since free-
form LLM geometry seldom hits the exact structural conditions needed). Left
unfixed deliberately — pinned as a regression test
(`test_corridor_compiler.py::test_data_flow_double_shelf_has_a_known_preexisting_layout_engine_gap`)
rather than patched blind; a fix belongs in its own reviewed change against
`logical_layout.py`/`placement_fixers.py`.

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
