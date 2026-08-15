# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An AI-powered cloud architecture diagram generator. Users describe infrastructure in natural language (AWS/GCP/Azure/multi-cloud) and get back a live, editable draw.io diagram, with optional cost, Well-Architected, and compliance insights. FastAPI backend (`app/`) + vanilla HTML/JS frontend (`static/`) embedding a draw.io iframe.

## Commands

```powershell
pip install -r requirements.txt
python run.py                 # dev server (uvicorn, reload=True) at http://localhost:8000/
```

```bash
pytest                                  # full suite
pytest tests/test_documents.py -q       # single file
pytest tests/test_documents.py::PdfExtractionTests::test_corrupt_pdf_is_an_error -q  # single test
```

Quality gates (all run in CI, `.github/workflows/ci.yml`):

```bash
ruff check .        # narrow ruleset — see the rationale in pyproject.toml
lint-imports        # architecture boundaries (import-linter)
pytest --cov        # suite + the coverage floor
```

`pyproject.toml` holds all three configs. Two things there are deliberate and
should not be "tidied": the ruff ruleset is narrow on purpose (a blanket one
flags ~250 style disagreements with deliberate choices and buries the real
findings), and `[tool.importlinter]`'s `ignore_imports` is a *frozen* list of
pre-existing `core -> adapters` violations — it exists to stop new ones, and is
meant to shrink, never grow.

Config is `.env`-driven and single-sourced in `app/adapters/outbound/config/env_config.py` — read it via `get_config().<setting>` at the point of use, never as an import-time constant (a snapshot taken before `init_config()` can't be refreshed in modules that already imported it). `app/core/config.py` only owns the one-time `.env`/admin-override load and the static `BASE_DIR`/`STATIC_DIR` paths. At minimum one LLM API key must be set (e.g. `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`). See `docs/ENV_AND_MCP.md` for the full key list and MCP/renderer options.

## Architecture

Hexagonal/ports-and-adapters layout:

```
app/
  domain/entities/        # plain dataclasses/pydantic models (XmlDiagram, DiagramEditPlan) — no I/O
  domain/ir/              # diagram IR — NetworkIR + shared CorridorIR (Logical/Security/Data flow),
                          # GenericDiagramIR (as-is image trace), SimpleDiagramIR (non-cloud), self-validating
  core/services/          # use cases: agent.py (LLM calls), xml_pipeline.py (orchestration), intent.py
  adapters/inbound/web/   # FastAPI routes.py + schemas.py + rate_limit.py
  adapters/outbound/      # everything that talks to the outside world:
    llm/                  # provider registry, langchain client wrapper, prompt builders, fallback.py
    diagram/              # draw.io XML validation/repair/MCP client
      compiler/           # IR → draw.io XML (deterministic; owns geometry + styles)
                          # chart.py plots bar/line/pie/gantt; sequence.py draws lifelines;
                          # simple.py dispatches the non-cloud kinds
      geometry_spec.py    # canonical spacing constants — shared by compiler AND diagnostics
      elk_bridge.py       # ELK layout: long-lived sidecar (tools/elk_layout/serve.js),
                          # falling back to a per-call layout.js spawn
    utils/                # documents.py (attachment extraction), web.py, chat.py
    cost_estimator.py, well_architected.py, compliance.py, history_store.py
    sqlite_util.py        # tuned sqlite3.connect (WAL + busy timeout) — use for every store
```

### Request flow (`POST /chat`, `/chat/stream`)

1. `routes._run_chat` classifies intent via `core/services/intent.classify_intent` — `"simple"` (non-cloud: flowchart/org chart/state machine/ER/mindmap, a sequence diagram, or a bar/line/pie/gantt chart) routes to `xml_pipeline.run_simple_pipeline`; anything else ("cloud") goes to the full pipeline. **Only the cloud/full path forwards `attachment_documents`/`context_text` to the LLM** — if attachments are present, intent is forced away from `"simple"` regardless of the classifier's answer, since the simple path can't use them.
2. `xml_pipeline.run_xml_pipeline` orchestrates the full path:
   - Fresh cloud → `agent.generate_xml_diagram_from_ir` (Pydantic IR → compiler XML).
   - Edit → surgical `DiagramEditPlan` when small; otherwise IR regenerate.
   - 3D → `generate_xml_diagram` with `prompt_3d.txt` only.
   - `xml_tools` / `xml_diagnostics.repair_xml` → structural hygiene + edge
     ports/overview only (**no LLM repair**, no geometry re-layout on `layout="ir"`).
   - Optional `DIAGRAM_VLM_QA` → fail-open visual checklist + one edge re-anchor.
   - In parallel: cost, Well-Architected, compliance, MCP icon resolution.
3. Response includes `xml`, `renderer_used` (`mcp` or `local`), `generation_path` (`ir` | `direct_xml` | `generic_ir` | `simple_ir`), `llm_usage`, and `logs`.

**Every path returns draw.io XML.** The `"simple"` path used to return Mermaid
source instead, for the frontend to hand the embed as `{action: "import",
format: "mermaid"}` — draw.io has no `import` action, so it replied
`{error: "unknownMessage"}` and drew nothing, for every simple diagram this app
ever produced. The embed accepts mxGraph XML and nothing else; do not add a
response shape that assumes otherwise.

### Generation paths (`DIAGRAM_IR_ENABLED`)

- **IR + compiler** (default for **fresh** cloud; `DIAGRAM_IR_VIEWS` per view):
  LLM emits IR; `adapters/outbound/diagram/compiler/` renders XML (ELK for
  corridor views). Leaf size is always `geometry_spec.LEAF_W/H`. Failure fails
  the request — no silent direct-XML fallback. `compiler/generic.py` (the
  as-is image-replication path, a sibling schema — see below) is the sole
  deliberate exception: it sizes/positions leaves from the LLM's `size_hint`/
  `x`/`y` hints instead, since its purpose is source-image fidelity, not
  cloud design-system consistency.
- **Simple (non-cloud)**: `SimpleDiagramIR` — one discriminated schema, six
  `kind`s. `"flow"` (flowchart, org chart, state machine, ER, mindmap) reuses
  `compiler/generic.py`, whose `_place_topological` already lays out an
  unpositioned node graph from its edges; `"bar"`/`"line"`/`"pie"`/`"gantt"`
  go to `compiler/chart.py`, which plots rather than lays out. `"sequence"` goes
  to `compiler/sequence.py`: its vertical axis is *time*, so message order is
  meaning and nothing may reflow it, and its activation bars are **derived**
  from sync/reply pairing rather than declared. Charts and sequences emit **no
  edges at all** — gridlines, axes and even sloped line segments are
  rectangles (the last via `rotation=`), and a sequence message is a
  `shape=line` shaft plus a `triangle` head — because `remove_dangling_edges`
  would delete a gridline and `fix_edge_anchors_from_geometry` would re-anchor
  every message to its lifeline's face midpoint. Chart and sequence cell ids
  (lifelines included, never the model's participant id) are sequence-first
  and zero-padded so
  `validate_and_fix_xml`'s id-ordered cell sort cannot re-layer the page
  (it once restacked gridlines on top of the bars).
  A follow-up turn on a simple diagram **regenerates the IR**, never a surgical
  edit: a bar's height is *derived* from its value, so patching cells relabels
  the bar without re-plotting it (measured: label read 200, bar stayed drawn at
  90, and the reply claimed success). The client echoes `current_diagram_type`
  back so `classify_intent` keeps the turn on this path — a follow-up like
  "change Q3 to 200" has no vocabulary of its own and otherwise took the cloud
  tie-break. `agent._simple_revision_block` feeds the current label inventory
  back in so a regenerate keeps whatever it wasn't asked to change.
- **Surgical edit** / **3D**: only remaining direct-XML paths (no `core.txt` /
  `views/` packs).

Rules of thumb:
- Layout/spacing → `geometry_spec.py` + `logical_layout.layout_corridor_ir`.
  Structural invariants → IR types / `semantic_errors()` / `corridor.py`.
  Do not resize leaves after compile.
- Prompts live in `config/xml_gen/ir/` (incl. `simple.txt` for the non-cloud
  path) (+ `classify/`, `icons/saas.txt`, `prompt_3d.txt`, `docs/`,
  `gap_analysis.txt`).
- Chart colour is a validated categorical palette in
  `compiler/chart_theme.py` — assigned in fixed slot order, never cycled,
  which is why `SimpleDiagramIR` caps series and pie slices at 6. Re-run the
  `dataviz` skill's `validate_palette.js` before changing a hue.
- `generation_path=direct_xml` means surgical edit or 3D, not a failed IR.

### LLM/MCP/prompt ownership split

- **LLM** owns IR structure (nesting, flows, `shape_type` keys) — not icon stencils.
- **Prompt packs** (`config/xml_gen/ir/` + `classify/` + `icons/saas.txt`) own judgment rules.
- **Draw.io MCP** resolves official leaf icon styles via `search_shapes`; misses log `mcp_miss`.
- `DIAGRAM_RENDERER` env/request setting: `auto`/`mcp` (resolve + optional libavoid routing) vs `local` (skip MCP, offline, placeholders stay unless the LLM invented a stencil).
- Style resolution is cached (`diagram/style_cache.py`, SQLite at `data/mcp_style_cache.sqlite3`) — after changing MCP pick logic or prompts, set `DRAWIO_STYLE_CACHE_RESET=true` once (then back to `false`) or delete the cache file.

### Provider abstraction

`config/providers.json` is the source of truth for LLM providers (including OpenAI-compatible gateways like OpenRouter/OpenCode/Cursor) — adding a provider means editing that JSON, not `providers.py`, unless it needs a genuinely new client library beyond `openai`/`anthropic`/`google` (see `_CLIENT_CLASSES` in `app/adapters/outbound/llm/providers.py`). Each provider spec declares its `structured_output` mode (`function_calling` | `json_mode` | `none`) since not all providers support the same structured-output mechanism — `llm_client.call_llm` branches on this.

### Attachments (PDF/image/text context)

`app/adapters/outbound/utils/documents.py:extract_document` classifies an upload and never raises — every failure (corrupt, encrypted, oversized, malicious, unsupported) becomes a `kind="error"` result so one bad attachment doesn't break the batch. Plain text/markdown/JSON/YAML → `kind="text"` (inlined into the prompt as `context_text`). PDFs and images → `kind="vision"`, base64-encoded and sent to the LLM as a native multimodal content block (`llm_client._build_human_message`) rather than locally extracted — PDFs are validated (active-content markers, corruption, encryption) but never text-extracted locally, since that would silently drop embedded diagrams/images the model needs to see. Only `ChatRequest` (`/chat`, `/chat/stream`) carries attachment fields; `PromptRequest` (`/generate`, `/download`) has none.

### Logging

`core/logging.StepLogger` accumulates structured step entries (`step`, `message`, `level`, `details`) surfaced both in the JSON response (`logs`) and via SSE progress events on `/chat/stream`. LLM token usage/cost is logged as `step="usage"` entries and summarized separately from the diagram's own estimated cloud cost.

Time a stage with `with logger.span("step", "message", **details):` (also works as `async with`) — it logs one entry carrying `duration_ms` and accumulates a per-step total. `xml_pipeline._build_response` emits the breakdown as a final `step="timings"` entry, so every response carries a stage profile without any call site threading a new field through. Every provider call is already timed inside `llm_client.call_llm` (`step="llm_call"`), covering the corrective re-ask and each backoff retry.

LLM calls run on a bounded private thread pool (`llm/concurrency.py`, sized by `MAX_CONCURRENT_LLM`), never on the default executor. This is deliberate: a semaphore around `asyncio.to_thread` releases its permit when a timed-out request is cancelled while the provider call keeps running, so the cap leaks (measured: 4 concurrent calls at a cap of 2). Keep LLM work on that pool — the store/document `asyncio.to_thread` calls rely on the default executor staying free.

## Cursor skills (not part of the FastAPI runtime)

`.cursor/skills/` contains standalone Cursor agent skills (`aws-architecture-diagram`, `azure-diagrams`, `drawio-skill`) for producing publish-quality `.drawio` files directly in Cursor chat, independent of this app's MCP config (`.cursor/mcp.json` is separate from `DRAWIO_MCP_URL`). See `docs/CLOUD_DIAGRAM_SKILLS.md`.
