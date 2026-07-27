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

There is no lint/format command configured (no `pyproject.toml`, `pytest.ini`, or ruff/black config) — don't invent one.

Config is `.env`-driven (`app/adapters/outbound/config/env_config.py`); at minimum one LLM API key must be set (e.g. `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`). See `docs/ENV_AND_MCP.md` for the full key list and MCP/renderer options.

## Architecture

Hexagonal/ports-and-adapters layout:

```
app/
  domain/entities/        # plain dataclasses/pydantic models (XmlDiagram, Mermaid) — no I/O
  domain/ir/              # diagram intermediate representation (NetworkIR) — pure, self-validating
  core/services/          # use cases: agent.py (LLM calls), xml_pipeline.py (orchestration), intent.py
  adapters/inbound/web/   # FastAPI routes.py + schemas.py + rate_limit.py
  adapters/outbound/      # everything that talks to the outside world:
    llm/                  # provider registry, langchain client wrapper, prompt builders, fallback.py
    diagram/              # draw.io XML validation/repair/MCP client
      compiler/           # IR → draw.io XML (deterministic; owns geometry + styles)
      geometry_spec.py    # canonical spacing constants — shared by compiler AND diagnostics
    utils/                # documents.py (attachment extraction), web.py, chat.py
    cost_estimator.py, well_architected.py, compliance.py, history_store.py
```

### Request flow (`POST /chat`, `/chat/stream`)

1. `routes._run_chat` classifies intent via `core/services/intent.classify_intent` — `"simple"` (generic diagram, no cloud vocabulary) routes to the lightweight Mermaid path (`agent.generate_mermaid_diagram`); anything else ("cloud") goes to the full pipeline. **Only the cloud/full path forwards `attachment_documents`/`context_text` to the LLM** — if attachments are present, intent is forced away from `"simple"` regardless of the classifier's answer, since the Mermaid path can't use them.
2. `xml_pipeline.run_xml_pipeline` orchestrates the full path:
   - `agent.generate_xml_diagram` → LLM emits draw.io XML directly (structure, geometry, edges, leaf `shape_type` placeholders) via `llm_client.call_llm`.
   - `xml_tools.validate_and_fix_xml` / `ensure_diagram_document` → structural normalization.
   - `xml_diagnostics.diagnose_xml` / `repair_xml` → deterministic checks (dangling edges, duplicate ids, overlaps, missing labels, parent cycles, geometry) with auto-fixers only — **no LLM repair** for format/structure.
   - In parallel (`asyncio.gather`): cost estimate, Well-Architected assessment, compliance assessment, and MCP icon resolution (`drawio_mcp_client.resolve_shape_styles` merges official icon styles into the LLM's placeholder leaves, preserving label attributes — see the LLM/MCP/prompt-pack ownership split below).
3. Response includes `xml`, `renderer_used` (`mcp` or `local`), `llm_usage` (token/cost accounting distinct from the depicted architecture's estimated cloud spend), and `logs`.

### Two generation paths (`DIAGRAM_IR_ENABLED`)

There are now two ways the primary page gets produced. Know which one you're touching:

- **Direct XML** (default): the LLM writes mxCells itself, then ~5k lines of deterministic
  repair (`xml_diagnostics.py`, `placement_fixers.py`, `xml_tools.py`) correct what it got
  wrong. Handles every view, edit mode, and 3D.
- **IR + compiler** (`DIAGRAM_IR_ENABLED=true`, Network view only, fresh generations only):
  the LLM emits a Pydantic-validated `NetworkIR` (`app/domain/ir/`) and
  `adapters/outbound/diagram/compiler/` deterministically renders the XML. Geometry, styles
  and parent wiring become code, so the repair layer has nothing to fix.

Rules of thumb when editing:
- A **layout/spacing** rule for the IR path is a constant in `geometry_spec.py`, not prompt
  prose. That module is the single source of truth — `xml_diagnostics.py` imports the same
  constants, which is what keeps compiler output diagnostically clean by construction.
- A **structural invariant** (nesting, "no edges on cross-cutting groups", no empty subnets)
  belongs in the IR types or `NetworkIR.semantic_errors()`, not in a prompt paragraph.
- The IR path must never introduce a new failure mode: `xml_pipeline._should_use_ir_path`
  gates eligibility and any exception falls back to direct XML.
- Responses carry `generation_path` (`ir` | `direct_xml`) — distinct from `renderer_used`
  (`mcp` | `local`), which is about icon resolution only.

### LLM/MCP/prompt ownership split (important when touching diagram generation)

This split is deliberate — don't blur it:
- **LLM** owns structure: parent/child nesting, geometry, container/group styles, edges, and a leaf's `shape_type` + grey placeholder style. It does **not** invent icon stencils (`resIcon=`, `mxgraph.gcp2`, Azure SVG paths).
- **Prompt packs** (`config/xml_gen/`: `core.txt` + `views/` including `network.txt` + `classify/` + `icons/` + `docs/`) own layout/classification rules, kept token-efficient by splitting shared CORE rules from Network hierarchy and platform-specific icon packs.
- **Draw.io MCP** (`drawio_mcp_client.py`) owns resolving official leaf icon styles at runtime via `search_shapes`, merged into (not overwriting) the LLM's leaf style. MCP misses leave the placeholder and log `mcp_miss` rather than half-overwriting a style.
- `DIAGRAM_RENDERER` env/request setting: `auto`/`mcp` (resolve + optional libavoid routing) vs `local` (skip MCP, offline, placeholders stay unless the LLM invented a stencil).
- Style resolution is cached (`diagram/style_cache.py`, SQLite at `data/mcp_style_cache.sqlite3`) — after changing MCP pick logic or prompts, set `DRAWIO_STYLE_CACHE_RESET=true` once (then back to `false`) or delete the cache file.

### Provider abstraction

`config/providers.json` is the source of truth for LLM providers (including OpenAI-compatible gateways like OpenRouter/OpenCode/Cursor) — adding a provider means editing that JSON, not `providers.py`, unless it needs a genuinely new client library beyond `openai`/`anthropic`/`google` (see `_CLIENT_CLASSES` in `app/adapters/outbound/llm/providers.py`). Each provider spec declares its `structured_output` mode (`function_calling` | `json_mode` | `none`) since not all providers support the same structured-output mechanism — `llm_client.call_llm` branches on this.

### Attachments (PDF/image/text context)

`app/adapters/outbound/utils/documents.py:extract_document` classifies an upload and never raises — every failure (corrupt, encrypted, oversized, malicious, unsupported) becomes a `kind="error"` result so one bad attachment doesn't break the batch. Plain text/markdown/JSON/YAML → `kind="text"` (inlined into the prompt as `context_text`). PDFs and images → `kind="vision"`, base64-encoded and sent to the LLM as a native multimodal content block (`llm_client._build_human_message`) rather than locally extracted — PDFs are validated (active-content markers, corruption, encryption) but never text-extracted locally, since that would silently drop embedded diagrams/images the model needs to see. Only `ChatRequest` (`/chat`, `/chat/stream`) carries attachment fields; `PromptRequest` (`/generate`, `/download`) has none.

### Logging

`core/logging.StepLogger` accumulates structured step entries (`step`, `message`, `level`, `details`) surfaced both in the JSON response (`logs`) and via SSE progress events on `/chat/stream`. LLM token usage/cost is logged as `step="usage"` entries and summarized separately from the diagram's own estimated cloud cost.

## Cursor skills (not part of the FastAPI runtime)

`.cursor/skills/` contains standalone Cursor agent skills (`aws-architecture-diagram`, `azure-diagrams`, `drawio-skill`) for producing publish-quality `.drawio` files directly in Cursor chat, independent of this app's MCP config (`.cursor/mcp.json` is separate from `DRAWIO_MCP_URL`). See `docs/CLOUD_DIAGRAM_SKILLS.md`.
