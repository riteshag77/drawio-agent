# Draw.io AI Architect

An AI-powered cloud architecture diagram generator. Describe your infrastructure in natural language and get a live, editable Draw.io diagram with numbered flows, multi-cloud support, and visual validation.

## Run locally

1. Configure [`.env`](.env) (see **[docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md)** for keys and ownership).
2. Set at least one LLM API key (`OPENAI_API_KEY`, etc.).
3. Install and start:

```powershell
pip install -r requirements.txt
python run.py
```

Open `http://localhost:8000/`. Use the **Render** dropdown for `Auto (MCP)` / `MCP` / `Local`.

| Piece | Role |
| --- | --- |
| **LLM** | Architecture JSON (nodes, edges, `parent_id`) — no coordinates |
| **Local layout** | Topology repair + AZ/subnet placement (users outside cloud, vertical tiers) |
| **Draw.io MCP** | Official icon styles; libavoid edge XML **only when MCP returns XML** |
| **Cursor skills** | Optional publish-quality `.drawio` in agent chat ([docs/CLOUD_DIAGRAM_SKILLS.md](docs/CLOUD_DIAGRAM_SKILLS.md)) |

---

## Architecture Overview

```
┌──────────────┐   POST /chat    ┌─────────────────────────────┐
│  Frontend    │ ──────────────> │  FastAPI (app/)              │
│  HTML/JS +   │                 │                             │
│  draw.io     │ <────────────── │  core/services/pipeline.py  │
│  iframe      │  xml + arch     │           │                 │
└──────────────┘                 │           ▼                 │
                                 │  ┌─────────────────────┐    │
                                 │  │ LLM → Architecture  │───>│ providers
                                 │  │ JSON (no x/y)       │    │
                                 │  └─────────┬───────────┘    │
                                 │            ▼                │
                                 │  ┌─────────────────────┐    │
                                 │  │ Topology repair     │    │
                                 │  │ users outside cloud │    │
                                 │  │ AZ wrap, bands      │    │
                                 │  └─────────┬───────────┘    │
                                 │            ▼                │
                                 │  ┌─────────────────────┐    │
                                 │  │ Local layout        │    │
                                 │  │ AZ columns, tiers   │    │
                                 │  │ shrink-wrap, IGW    │    │
                                 │  └─────────┬───────────┘    │
                                 │            ▼                │
                                 │  ┌─────────────────────┐    │
                                 │  │ MCP (auto/mcp)      │───>│ mcp.draw.io
                                 │  │ search_shapes       │    │
                                 │  │ libavoid if XML     │    │
                                 │  └─────────┬───────────┘    │
                                 │            ▼                │
                                 │  quality gates + XML        │
                                 └─────────────────────────────┘
```

**Ownership (do not duplicate):**

| Own locally | Delegate to MCP / Draw.io |
| --- | --- |
| Users outside cloud/account | Icon catalogue (`search_shapes`) |
| VPC → AZ → subnet hierarchy | Edge path geometry (`libavoid` when XML returned) |
| Public → App → Data vertical stack | — |
| Cross-cutting bands, IGW clamp | — |

Do **not** use MCP `postLayout=elk` for cloud diagrams — it breaks AZ/subnet containment.

**Data flow for a typical request:**

1. User sends a chat message; frontend posts `POST /chat` (platform, history, optional current architecture/XML).
2. Pipeline classifies intent and calls the LLM for architecture JSON (structure only).
3. **Topology repair**: force entry actors outside cloud; insert AZs; move WAF/CW/etc. into bands; ensure IGW.
4. **Local layout**: strip LLM coordinates; place AZs side-by-side; stack subnet tiers; shrink-wrap containers; dock bands.
5. **MCP** (if Render is Auto/MCP): resolve leaf icon styles; optionally replace XML with libavoid-routed XML when returned.
6. Quality gates / layout validation; response includes `xml`, `architecture`, `renderer_used`, `flow_steps`, `logs`.
7. Frontend loads XML into the Draw.io iframe.

Details: [docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md).

---

## Directory Structure

Canonical implementation lives under **`app/adapters/`** and **`app/core/`**. Paths like `app/diagram/` and `app/llm/` are thin re-export shims for older imports/tests.

```
drawio-agent/
│
├── run.py                        # Dev entry point: `python run.py`
├── requirements.txt
├── .env                          # LLM keys, DIAGRAM_RENDERER, layout constants
├── docs/
│   ├── ENV_AND_MCP.md            # Ownership + env keys
│   ├── CLOUD_DIAGRAM_SKILLS.md   # Cursor/Kiro skills
│   └── KIRO_AWS_DIAGRAMS.md
│
├── app/
│   ├── main.py                   # FastAPI app factory
│   ├── core/
│   │   ├── services/pipeline.py  # Chat → LLM → render orchestration
│   │   ├── logging.py
│   │   └── ssl_trust.py
│   ├── domain/entities/          # ArchitectureDiagram, Node, Edge, …
│   ├── adapters/
│   │   ├── inbound/web/          # routes.py, schemas.py
│   │   └── outbound/
│   │       ├── config/env_config.py
│   │       ├── llm/              # providers, prompts, response_parse
│   │       ├── patterns/         # three_tier_web.json, …
│   │       ├── validation/       # structural + visual
│   │       └── diagram/          # layout, repair, MCP, XML
│   │           ├── draftsman.py          # sanitize → repair → layout → XML
│   │           ├── topology_repair.py    # users outside, AZ wrap, IGW
│   │           ├── diagram_normalize.py  # bands, flow renumber
│   │           ├── layout.py             # AZ columns, vertical tiers
│   │           ├── service_bands.py      # docked Security/Logging/…
│   │           ├── layout_validate.py    # hard containment checks
│   │           ├── edge_router.py        # fixed mid-face anchors only
│   │           ├── styles.py             # local fallback catalogue
│   │           ├── drawio_mcp_client.py  # search_shapes, create_diagram
│   │           ├── mcp_renderer.py       # styles + optional libavoid XML
│   │           └── xml_render.py / xml_tools.py
│   ├── diagram/, llm/, api/, …   # compatibility re-exports
│   └── …
│
├── static/                       # index.html, app.js, styles.css
├── tests/
└── .cursor/skills/               # aws-architecture-diagram, azure-diagrams, drawio-skill
```

---

## Step-by-Step Code Walkthrough

### Step 1: Application Entry Point (`run.py` / `app/main.py`)

The server starts via `run.py:main()` which calls `uvicorn.run()` on the `app` object created by `app/main.py:create_app()`.

`create_app()` does four things:
1. **CORS middleware** — allows the frontend origin (configured via `CORS_ORIGINS` env var)
2. **Request ID middleware** — attaches a `request_id` to every request state for log correlation
3. **Static file mount** — serves `/static/index.html`, `app.js`, and `styles.css` at the `/static/` URL prefix
4. **Router inclusion** — registers routes from `app/adapters/inbound/web/routes.py`

### Step 2: API Layer (`app/adapters/inbound/web/`)

**Schemas** (`schemas.py`) define the Pydantic request models:

- `ChatRequest` — `messages`, provider, platform, optional architecture/XML, `diagram_renderer` (`auto`/`mcp`/`local`), validation feedback, image data, platform conversion flags
- `ValidateDiagramRequest` — image data and validation provider
- `ParseUrlRequest` — URL to fetch

**Routes** (`routes.py`) handle HTTP endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Redirects to `/static/index.html` |
| `GET /health` | Returns service status |
| `POST /chat` | **Main endpoint** — multi-turn diagram generation/editing |
| `POST /generate` | Single-turn generation (legacy) |
| `POST /validate-diagram` | VLM diagram image validation |
| `POST /parse-url` | Extract text from a URL |

The `/chat` handler:
1. Validates messages are non-empty and end with a user message
2. Optionally parses `current_architecture` / `previous_architecture` from JSON
3. Calls the appropriate pipeline based on context:
   - **XML edit mode** — if `current_xml` exists and has real diagram content
   - **Mermaid mode** — if intent classification returns `"simple"`
   - **Full generation** — otherwise, calls the cloud architecture pipeline

### Step 3: Pipeline Orchestration (`app/core/services/pipeline.py`)

The pipeline is the central orchestrator (shim: `app/services/pipeline.py`). Main cloud path:

```
chat messages
      │
      ▼
┌─────────────────────┐
│ LLM retry loop      │  Architecture JSON + structural validation
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ render_architecture │  mcp_renderer.py
│ _xml()              │
│  • local generate_xml│  repair → layout → bands → XML
│  • MCP styles       │  search_shapes overrides
│  • MCP libavoid     │  only if create_diagram returns XML
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Optional VLM loop   │  If enabled / image attached
└─────────────────────┘
```

`generate_xml()` (in `draftsman.py`) runs: sanitize → blueprint → topology repair → scaffold → normalize → `build_layout` → `layout_bands` → vertices/edges → quality gates.

Edit and Mermaid paths also live in the same pipeline module (ID-based XML ops; single-shot Mermaid).

### Step 4: LLM Integration (`app/adapters/outbound/llm/`)

Shim: `app/llm/`. Provider registry (`providers.py`) supports OpenAI, OpenRouter, Gemini, Claude, Cursor SDK, and OpenCode.

| Provider | Client Class | Default Model | Key Env Var |
|----------|-------------|---------------|-------------|
| openai | `ChatOpenAI` | `gpt-4o` | `OPENAI_API_KEY` |
| openrouter | `ChatOpenAI` | `openai/gpt-4o-mini` | `OPENROUTER_API_KEY` |
| gemini | `ChatGoogleGenerativeAI` | `gemini-2.5-flash` | `GOOGLE_API_KEY` |
| claude | `ChatAnthropic` | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| cursor | SDK (direct) | `composer-2.5` | `CURSOR_API_KEY` |
| opencode | `ChatOpenAI` | `big-pickle` | `OPENCODE_API_KEY` |

Each `ProviderSpec` also defines:
- `kind` — `"langchain"` or `"sdk"` (Cursor uses SDK, not LangChain)
- `base_url_env` / `base_url_default` — for OpenAI-compatible gateways (OpenRouter, OpenCode)
- `structured_output` — `"json_mode"`, `"function_calling"`, or `"none"`

Structured output method resolution prioritizes:
1. Provider-specific `structured_output` field in the registry
2. `STRUCTURED_OUTPUT_METHOD` env var (default: `"function_calling"`)
3. OpenRouter defaults to `"none"` (free/cheap routes lack tool calling)
4. OpenCode always uses `"none"` (text JSON extraction only)

#### Agent / client / prompts

Canonical modules under `app/adapters/outbound/llm/` (shim: `app/llm/`):

- `agent.py` — `generate_architecture_json`, `generate_architecture_from_chat`, `generate_edit_operations`, `generate_mermaid_diagram`
- `llm_client.py` — `call_llm()` with structured-output method resolution and text-JSON fallback
- `response_parse.py` — `extract_json`, `message_text`, gateway JSON helpers
- `prompt_builders.py` — chat/edit human prompts (history trim, conversion block, validation feedback)
- `prompts/system.py` — system prompts (schema, shape catalogue, hierarchy rules; slim Cursor variant)

### Step 5: Architecture Models (`app/domain/entities/`)

Shim: `app/models/`. Core types: `Node`, `Edge`, `FlowStep`, `ArchitectureDiagram`, plus Mermaid/edit/validation models.

All domain models are Pydantic v2 `BaseModel` subclasses:

```
ArchitectureDiagram
├── nodes: list[Node]        # id, label, shape_type, is_container, parent_id, step_number
├── edges: list[Edge]        # source_id, target_id, label, style, color
├── suggestions: list[str]   # Best-practice recommendations
├── flow_steps: list[FlowStep]  # number, title, summary
├── pattern_id: str | None
└── title: str | None

EditResponse
├── mode: "edit" | "replace"
├── operations: list[DiagramOperation]  # operation, cell_id, new_xml
├── xml: str                  # Full XML replacement (for replace mode)
├── assistant_message: str
└── suggestions: list[str]

MermaidDiagram
├── mermaid: str
├── assistant_message: str
└── suggestions: list[str]

ValidationResult
├── valid: bool
├── issues: list[ValidationIssue]  # type, severity, description
└── suggestions: list[str]
```

### Step 6: Diagram Engine (`app/adapters/outbound/diagram/`)

Canonical path: `app/adapters/outbound/diagram/` (shim: `app/diagram/`).

#### Pipeline (`draftsman.py` + `mcp_renderer.py`)

```text
sanitize → pattern blueprint → topology_repair → scaffold → normalize
  → build_layout → layout_bands → XML
  → (MCP) search_shapes styles → optional create_diagram(libavoid) if XML returned
  → quality / layout_validate
```

#### Topology repair (`topology_repair.py`)

- `reparent_entry_actors` — Users/clients always `parent_id=null` (outside cloud)
- `ensure_az_hierarchy` — insert AZ between VPC and subnets
- `ensure_igw` / `reparent_edge_services` / `mirror_multi_az`

#### Layout (`layout.py`)

Deterministic placement (LLM coordinates stripped):

1. Entry actors left of cloud on the canvas
2. AZs side-by-side; inside each AZ, Public → App → Data **vertical** stack (content-sized heights)
3. Workload containers shrink-wrap with padding; dense leaves use a 2-column grid
4. IGW/boundary icons clamped to `y >= -iconHeight/2`
5. Cross-cutting bands docked beside Region/VPC inside the cloud ([`service_bands.py`](app/adapters/outbound/diagram/service_bands.py))

Leaf default size: **48×48** (`LEAF_NODE_W/H`).

#### Styles + MCP

- Local fallback: `styles.py` catalogue for offline/`local` mode
- MCP: `drawio_mcp_client.search_shapes` → style overrides; sizes stay at 48×48
- Edges: `edge_router.py` only sets fixed mid-face anchors; path geometry is Draw.io / MCP libavoid

#### XML Tools (`xml_tools.py`)

- `wrap_with_mxfile()`, `validate_and_fix_xml()`, `apply_diagram_operations()`

### Step 7: Pattern Library (`app/adapters/outbound/patterns/`)

Shim: `app/patterns/`. Six curated reference architectures:

| Pattern ID | Name | Platform |
|-----------|------|----------|
| `three_tier_web` | 3-Tier Web Application | AWS, GCP, Azure |
| `serverless_api` | Serverless API Backend | AWS, GCP, Azure |
| `event_driven` | Event-Driven Data Pipeline | AWS, GCP |
| `data_analytics` | Data Analytics Platform | AWS, GCP |
| `microservices_k8s` | Microservices on Kubernetes | AWS, GCP, Azure |
| `static_site_cdn` | Static Site with CDN | AWS, GCP, Azure |

**Flow**: When a user prompt matches a pattern (via keyword scoring), the pattern's services, best practices, and flow description are injected into the LLM system prompt. The 3-tier pattern also has a container size scaffold in `template_merge.py`.

### Step 8: Validation (`app/adapters/outbound/validation/`)

Shim: `app/validation/`.

- **Structural** (`structural.py`) — parent refs, nesting depth, IDs, edges; no LLM coordinates
- **Layout** (`layout_validate.py` in diagram/) — hard containment checks when `QUALITY_GATES_BLOCKING=true`
- **Visual** (`visual.py`) — optional VLM on PNG; fail-open if unavailable

### Step 9: Frontend (`static/`)

#### `index.html` (318 lines)

Single-page application with three-column layout:
- **Left sidebar**: Brand header, platform selector (AWS/GCP/Azure/Multi), diagram mode, chat messages, composer (textarea + send/stop + file attach + voice), settings drawer
- **Center**: Draw.io embedded iframe with toolbar (Download/Copy/Open)
- **Right panel**: Validation issues + pipeline step logs (collapsible)

#### `app.js` (1659 lines)

~60 functions organized by responsibility:

| Group | Functions |
|-------|-----------|
| **Chat** | `sendMessage()`, `clearChat()`, `appendChatBubble()`, `autoResizeChatInput()` |
| **Draw.io** | `ensureDrawioFrame()`, `bindDrawioMessages()`, `loadDiagramIntoEditor()`, `exportDiagramXml()`, `exportDiagramPng()`, `applyLayoutToEditor()` |
| **Platform** | `selectPlatform()`, `applyPlatformUi()`, `regenerateForPlatform()` |
| **Validation** | `runVisualValidation()`, `showValidationBanner()`, `autoRetryWithVlm()`, `fixLayoutIssues()` |
| **Settings** | `openSettings()`, `closeSettings()`, `initPromptSettings()`, `savePromptSettings()` |
| **Logging** | `appendVerboseLog()`, `mergeServerLogs()`, `initVerboseLogging()`, `toggleVerbosePanel()` |
| **Voice** | `initVoiceInput()`, `toggleVoiceInput()`, `stopVoiceInput()` |
| **Templates** | `loadTemplates()`, `saveCurrentAsTemplate()`, `deleteTemplate()` |
| **Attachments** | `handleFileSelect()`, `parseUrlContext()` |
| **UI** | `sendMessage()` progress bar, `setDiagramStatus()`, `setComposerRunStatus()`, `initSidebarResizer()` |

Key message flow in `sendMessage()`:
1. Validates input and builds `chatMessages` array with user message
2. Collects state: platform, XML, architecture, image attachments
3. Calls `POST /chat` with full request body
4. On success: loads XML into Draw.io iframe, updates state, appends assistant bubble
5. On error: shows error banner, pops the failed user message from history
6. Runs post-generation tasks: auto-layout (if enabled), VLM validation (if enabled)

#### `styles.css` (1510 lines)

~20 major style sections using CSS custom properties for theming:

| Section | Description |
|---------|-------------|
| `:root` | Color palette, spacing, font stack, sizing variables |
| `.app-shell` | Three-column flexbox layout |
| `.sidebar` | Left panel: fixed width with resize handle |
| `.settings-drawer` | Slide-in settings with backdrop overlay |
| `.chat-panel` | Scrollable message area with bubble styling |
| `.composer` | Input area with textarea, tool buttons, provider select |
| `.diagram-panel` | Center column: full-height Draw.io iframe |
| `.log-panel` | Right panel: validation issues + pipeline logs |
| `.toggle-switch` | Custom toggle switches for settings |
| `@media` | Responsive breakpoints at 1200px and 900px |

### Step 10: Chat History (`app/adapters/outbound/utils/chat.py`)

Shim: `app/utils/chat.py`. Trims history and replaces old architecture/XML payloads with placeholders before the LLM call.

### Step 11: URL Parsing (`app/adapters/outbound/utils/web.py`)

Shim: `app/utils/web.py`. `fetch_url_content()` pulls HTML/JSON/text via `httpx` for prompt context.

### Step 12: Configuration (`app/core/config.py` + `env_config.py`)

Typed settings load from `.env` via `EnvConfigAdapter` (`app/adapters/outbound/config/env_config.py`). Full key list: [docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md).

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Server bind |
| `DIAGRAM_RENDERER` | `auto` | `auto` / `mcp` / `local` |
| `DRAWIO_MCP_URL` | `https://mcp.draw.io/mcp` | MCP endpoint |
| `LEAF_NODE_W` / `H` | `48` | Leaf icon size |
| `QUALITY_GATES_BLOCKING` | `true` | Fail on layout containment errors |
| `LLM_TIMEOUT_SECONDS` | `60` | LLM API timeout |
| `MAX_STRUCTURAL_RETRIES` | `3` | Architecture retry attempts |
| `MAX_VLM_RETRIES` | `2` | VLM validation retry attempts |
| `STRUCTURED_OUTPUT_METHOD` | `function_calling` | LLM structured output mode |

### Step 13: Diagnostics (`scripts/`)

Two diagnostic scripts for troubleshooting provider connectivity:

- **`diagnose_opencode.py`**: Fetches available models from OpenCode API, tests basic and structured requests
- **`diagnose_cursor_sdk.py`**: Tests Cursor SDK bridge discovery, model listing, and simple agent prompt

---

## API Reference

### `POST /chat`

**Request:**
```json
{
  "messages": [{"role": "user", "content": "3-tier web app with ALB, Fargate, RDS"}],
  "provider": "openai",
  "platform": "aws",
  "current_architecture": null,
  "current_xml": null,
  "minimal_style": false,
  "custom_system_message": "",
  "validation_feedback": null,
  "image_data": null,
  "platform_convert": false,
  "diagram_renderer": "auto"
}
```

**Response:**
```json
{
  "status": "success",
  "diagram_type": "cloud",
  "xml": "<mxfile>...</mxfile>",
  "architecture": { /* ArchitectureDiagram */ },
  "flow_steps": [{"number": 1, "title": "User request", "summary": "..."}],
  "pattern_id": "three_tier_web",
  "title": "3-Tier Web Application",
  "suggestions": ["Add WAF for enhanced security"],
  "assistant_message": "Generated diagram with 12 nodes and 8 edges.",
  "edit_mode": false,
  "renderer_used": "mcp",
  "mcp_preview_url": null,
  "validation": {
    "structural": {"valid": true, ...},
    "vlm": null,
    "retries": 0,
    "feedback": null
  },
  "logs": [ /* pipeline steps */ ]
}
```

`renderer_used` is `mcp` only when libavoid XML was applied; otherwise `local` (styles may still come from MCP). `mcp_preview_url` is set only when routed XML was applied.

---

## Testing

Run all tests:
```
python -m pytest tests/ -v
```

Run specific test file:
```
python -m pytest tests/test_hybrid_diagram.py -v
```

Run with warnings:
```
python -m pytest tests/ -v -W default
```

Test coverage (~72 tests):
- **test_structural_layout.py** — AZ wrap, vertical subnet tiers, bands, IGW clamp, workload sizing
- **test_mcp_renderer.py** — `auto`/`local`/`mcp`, libapply only when XML returned, style overrides
- **test_diagram_normalize.py** / **test_layout_sanitize.py** — normalize, sanitize, XML hardening
- **test_aws_reference_lean.py** — lean AWS reference regressions
- **test_hybrid_diagram.py** — icons, edges, intent classification
- **test_multiturn_editing.py** — incremental XML edit lifecycle
- **test_platform_convert.py** / **test_providers.py** / **test_structured_output.py** / **test_suggestions.py**

---

## Key Design Decisions

1. **Split ownership**: Local code owns AWS/GCP/Azure *containment* (users outside cloud, AZ/subnet stacking). MCP/Draw.io owns *icons* and *edge paths* when it returns XML — we do not reimplement libavoid or ELK.
2. **Server-side placement**: LLMs produce unreliable coordinates; layout strips them and places by hierarchy (not a free-form grid of all siblings).
3. **Structural retry loop**: Invalid architecture JSON is retried with validation feedback rather than silently “fixed.”
4. **VLM fail-open**: Visual validation uses a vision model when enabled; if unavailable, the diagram is still returned.
5. **Text JSON extraction fallback**: Providers without structured output fall back to text JSON parsing.
6. **ID-based XML editing**: Edits apply update/add/delete by cell ID when possible, preserving unchanged cells.
7. **Pattern blueprints**: Matched patterns fill missing containers/services (e.g. `three_tier_web` AZs) after the LLM response.
8. **No frontend framework**: Vanilla HTML/CSS/JS + Draw.io iframe — no build step.
