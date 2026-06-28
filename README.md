# Draw.io AI Architect

An AI-powered cloud architecture diagram generator. Describe your infrastructure in natural language and get a live, editable Draw.io diagram with numbered flows, multi-cloud support, and visual validation.

---

## Architecture Overview

```
┌──────────────┐     POST /chat      ┌──────────────────┐     LLM API     ┌──────────┐
│  Frontend    │ ──────────────────> │  FastAPI Server   │ ─────────────> │ OpenAI   │
│  (vanilla    │                     │  (app/)           │                │ Gemini   │
│   HTML/CSS)  │ <────────────────── │                   │ <───────────── │ Claude   │
│              │     JSON response   │                   │                │ OpenCode │
│  draw.io     │                     │  Pipeline         │                │ Cursor   │
│  iframe      │                     │  ┌─────────────┐  │                └──────────┘
└──────────────┘                     │  │ Intent      │  │
                                     │  │ Classify    │  │
                                     │  ├─────────────┤  │
                                     │  │ Retry Loop  │  │
                                     │  │ (structural │  │
                                     │  │  validation)│  │
                                     │  ├─────────────┤  │
                                     │  │ Layout      │  │
                                     │  │ Engine      │  │
                                     │  ├─────────────┤  │
                                     │  │ VLM Visual  │  │
                                     │  │ Validation  │  │
                                     │  └─────────────┘  │
                                     └──────────────────┘
```

**Data flow for a typical request:**

1. User types a message in the chat UI and clicks Send
2. Frontend collects state (platform, XML, architecture, image attachments) and sends `POST /chat`
3. FastAPI route validates the request body via Pydantic schemas
4. Pipeline orchestrator runs the generation chain:
   - **Retry loop**: Calls the LLM with structural validation; retries up to 3 times if the JSON is invalid
   - **Layout engine**: Strips LLM-supplied coordinates, applies deterministic auto-layout, generates Draw.io XML
   - **VLM validation** (optional): Renders the diagram to PNG, sends to a vision model to check for overlaps
5. Response is returned as JSON with `xml`, `architecture`, `flow_steps`, `validation`, and `logs`
6. Frontend loads the XML into the embedded Draw.io iframe

---

## Directory Structure

```
drawio-agent/
│
├── run.py                        # Dev entry point: `python run.py`
├── requirements.txt              # Python dependencies
│
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app factory (CORS, middleware, static mount)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py             # HTTP route handlers (6 endpoints)
│   │   └── schemas.py            # Request/response Pydantic models
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py             # Env-driven configuration (12-factor)
│   │   ├── cursor_bridge.py      # Windows compatibility patch for Cursor SDK
│   │   ├── logging.py            # Structured step-log collection for pipeline tracing
│   │   └── ssl_trust.py          # HTTPS certificate verification setup
│   │
│   ├── diagram/
│   │   ├── __init__.py
│   │   ├── draftsman.py          # Top-level XML generation orchestrator
│   │   ├── edge_router.py        # Simple edge exit/entry point calculation
│   │   ├── layout.py             # Deterministic auto-layout: tiers, containers, grids
│   │   ├── styles.py             # Draw.io style builder for AWS/GCP/Azure shapes
│   │   ├── xml_render.py         # Low-level mxCell/edge XML fragment builders
│   │   └── xml_tools.py          # XML validation, auto-fix, and edit operations
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── agent.py              # Public LLM orchestration API
│   │   ├── llm_client.py         # LangChain invocation with structured-output fallback
│   │   ├── prompt_builders.py    # Human-readable prompt assembly
│   │   ├── providers.py          # Provider registry + LangChain client construction
│   │   ├── response_parse.py     # JSON/mermaid/text extraction from model responses
│   │   ├── sdk_client.py         # SDK-backed providers (Cursor SDK)
│   │   └── prompts/
│   │       ├── __init__.py
│   │       ├── schema.txt        # Architecture schema + shape catalogue for prompts
│   │       ├── simple.py         # System prompt for Mermaid (simple) diagrams
│   │       ├── system.py         # System prompt builder (architecture, edit, cursor)
│   │       └── validation.py     # VLM validation system prompt
│   │
│   ├── models/
│   │   ├── __init__.py           # Re-exports all domain models
│   │   ├── architecture.py       # Node, Edge, FlowStep, ArchitectureDiagram
│   │   ├── mermaid.py            # MermaidDiagram
│   │   ├── operations.py         # DiagramOperation, EditResponse
│   │   └── validation.py         # ValidationIssue, ValidationResult
│   │
│   ├── patterns/
│   │   ├── __init__.py
│   │   ├── library.py            # 6 curated architecture patterns with keyword matching
│   │   └── template_merge.py     # Container size scaffolds (e.g. 3-tier web)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   └── pipeline.py           # End-to-end generation/edit pipeline orchestration
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── chat.py               # Chat history trimming and placeholder replacement
│   │   └── web.py                # URL content fetching and HTML-to-text extraction
│   │
│   └── validation/
│       ├── __init__.py
│       ├── structural.py         # Architecture JSON structural validation
│       └── visual.py             # VLM-based PNG image validation (fail-open)
│
├── static/
│   ├── index.html                # Single-page app skeleton (318 lines)
│   ├── app.js                    # Frontend logic (1659 lines, 60+ functions)
│   └── styles.css                # All styles (1510 lines, ~20 major sections)
│
├── tests/
│   ├── __init__.py
│   ├── test_cursor_sdk.py        # Cursor SDK error formatting and prompt size
│   ├── test_hybrid_diagram.py    # Architecture generation, edge routing, XML output
│   ├── test_layout_sanitize.py   # Coordinate stripping, depth validation, XML hardening
│   ├── test_multiturn_editing.py # 3-turn incremental XML editing lifecycle
│   ├── test_platform_convert.py  # Platform conversion prompt and request format
│   ├── test_providers.py         # Provider registration and model normalization
│   └── test_structured_output.py # Structured output method resolution and text extraction
│
├── scripts/
│   ├── diagnose_opencode.py      # OpenCode Zen API connectivity diagnostic
│   └── diagnose_cursor_sdk.py    # Cursor SDK local runtime diagnostic
│
└── docs/                         # Documentation files
```

---

## Step-by-Step Code Walkthrough

### Step 1: Application Entry Point (`run.py` / `app/main.py`)

The server starts via `run.py:main()` which calls `uvicorn.run()` on the `app` object created by `app/main.py:create_app()`.

`create_app()` does four things:
1. **CORS middleware** — allows the frontend origin (configured via `CORS_ORIGINS` env var)
2. **Request ID middleware** — attaches a `request_id` to every request state for log correlation
3. **Static file mount** — serves `/static/index.html`, `app.js`, and `styles.css` at the `/static/` URL prefix
4. **Router inclusion** — registers all routes from `app/api/routes.py`

### Step 2: API Layer (`app/api/`)

**Schemas** (`app/api/schemas.py`) define the Pydantic request models:

- `ChatRequest` — the main endpoint body with `messages` (list of `ChatMessage`), provider, platform, optional current/previous architecture and XML, validation feedback, image data, and platform conversion flags
- `ValidateDiagramRequest` — image data and validation provider
- `ParseUrlRequest` — URL to fetch

**Routes** (`app/api/routes.py`) handle 6 endpoints:

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

### Step 3: Pipeline Orchestration (`app/services/pipeline.py`)

The pipeline is the central orchestrator. There are three pipeline functions:

#### `run_generation_pipeline()` (line 243)
The main path for cloud architecture diagrams:

```
user prompt / messages
        │
        ▼
  ┌─────────────────────┐
  │ _retry_loop_sync()  │  Up to 3 attempts
  │                     │
  │ 1. Call LLM →       │
  │    ArchitectureJSON │
  │ 2. validate_structure│
  │ 3. If invalid,      │
  │    retry with       │
  │    feedback         │
  └─────────┬───────────┘
            │ architecture_data
            ▼
  ┌─────────────────────┐
  │ generate_xml()      │  Layout engine + XML builder
  │                     │
  │ 1. sanitize_layout  │  Strip LLM coordinates
  │ 2. apply_scaffold   │  Container sizing
  │ 3. build_layout     │  Tiered auto-layout
  │ 4. render XML       │  mxCell fragments
  └─────────┬───────────┘
            │ xml_output
            ▼
  ┌─────────────────────┐
  │ VLM retry loop      │  If image attached
  │ (optional)          │
  │                     │
  │ 1. Validate PNG     │
  │ 2. If issues,       │
  │    re-generate      │
  └─────────┬───────────┘
            ▼
  ┌─────────────────────┐
  │ _build_response()   │  Standardized JSON response
  └─────────────────────┘
```

#### `run_edit_pipeline()` (line 169)
For incremental XML editing:
1. Calls `generate_edit_operations()` which returns `EditResponse` with ID-based operations
2. Applies operations via `apply_diagram_operations()` in `xml_tools.py`
3. Runs VLM validation loop (same as generation pipeline)

#### `run_mermaid_pipeline()` (line 150)
Simple path for Mermaid diagrams — one LLM call, no retry loop.

### Step 4: LLM Integration (`app/llm/`)

#### Provider Registry (`app/llm/providers.py`)

Supports 6 providers:

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

#### Agent (`app/llm/agent.py`)

Public API functions:
- `generate_architecture_json()` — calls LLM with system prompt + architecture schema, returns `ArchitectureDiagram`
- `generate_architecture_from_chat()` — wraps above with chat message assembly, returns `(ArchitectureDiagram, summary_text)`
- `generate_edit_operations()` — calls LLM with edit system prompt + current XML, returns `EditResponse`
- `generate_mermaid_diagram()` — simple Mermaid generation

#### LLM Client (`app/llm/llm_client.py`)

`call_llm()` is the generic LangChain invocation function:
1. Resolves structured output method for the provider
2. If `"none"`: calls `llm.invoke()` directly, extracts JSON from text response
3. If `"function_calling"`: uses `with_structured_output()` with tool calling
4. If `"json_mode"`: uses `with_structured_output(method="json_mode")`
5. **Fallback**: if structured output fails, logs the warning and falls back to text JSON extraction
6. Uses `_message_text()` to safely get text from either `AIMessage` objects or raw strings

#### Response Parsing (`app/llm/response_parse.py`)

- `extract_json()` — finds JSON object in LLM response (handles fenced code blocks, raw braces)
- `message_text()` — normalizes LangChain AIMessage or string to plain text
- `invoke_gateway_json()` — calls OpenAI-compatible gateway directly with `response_format`
- `invoke_json()` — calls LLM and validates output as Pydantic model

#### Prompt Builders (`app/llm/prompt_builders.py`)

- `build_chat_prompt()` — assembles the human prompt for chat generation:
  - Platform conversion block (if converting)
  - Previous architecture JSON (for comparison)
  - Current architecture JSON (authoritative, to modify)
  - Conversation history (trimmed, with placeholders for large payloads)
  - Latest user request
  - Validation feedback (if retrying)
- `build_edit_prompt()` — similar but for XML editing mode

#### System Prompts (`app/llm/prompts/system.py`)

- `get_system_prompt()` — builds the full system prompt:
  - Role definition ("expert cloud architect")
  - Style instructions (normal or minimal)
  - Architecture schema reference (loaded from `schema.txt`)
  - Platform shape catalogue
  - Container hierarchy rules
  - Edge rules
  - Self-check checklist
- `get_cursor_system_prompt()` — slimmed-down version for Cursor SDK (128k context budget)
- `get_edit_system_prompt()` — for XML edit mode with operation instructions

### Step 5: Architecture Models (`app/models/`)

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

### Step 6: Diagram Engine (`app/diagram/`)

#### Layout Engine (`app/diagram/layout.py`)

Deterministic auto-layout that replaces LLM-supplied coordinates:

1. **Sanitize** (`draftsman.py:27`): strips all `x`, `y`, `width`, `height` from architecture nodes
2. **Build children map**: parent→children relationships from `parent_id` fields
3. **Tier classification** (`node_tier()`, line 45): classifies nodes into tiers — `"container"`, `"resource"`, `"step"`, `"other"`
4. **Layout siblings** (`layout_siblings()`, line 98): positions sibling nodes in a grid (up to 6 columns, left-to-right, top-to-bottom)
5. **Fit containers** (`fit_container_bounds()`, line 76): adjusts container width/height to enclose children with padding
6. **Root layout** (`build_layout()`, line 138): lays out root-level containers vertically with spacing

Constants:
- Leaf nodes: 48×48px
- Containers: auto-sized with 40px padding, 30px top padding for header
- Max 6 columns per row, 20px gap between nodes

#### Style Builder (`app/diagram/styles.py`)

`build_style(shape_type)` returns the complete Draw.io style string for any architecture shape:

1. Normalizes legacy keys (e.g., `aws_application_load_balancer` → `aws_alb`)
2. Matches platform prefix: `aws_*`, `gcp_*`, `azure_*`
3. Looks up platform-specific icon SVG paths and style overrides
4. Returns a Draw.io `style=` string with shape, stroke, fill, opacity, verticalLabelPosition, and platform accent color

Supports 200+ AWS services, 100+ GCP services, and 100+ Azure services.

#### Edge Router (`app/diagram/edge_router.py`)

`route_edge()` calculates exit/entry points for edges based on relative position of source and target:
- Used in `xml_render.py:append_edge()` to set `exitX`/`exitY`/`entryX`/`entryY`
- Draw.io handles the actual orthogonal pathfinding natively

#### XML Tools (`app/diagram/xml_tools.py`)

- `wrap_with_mxfile()` — wraps bare `<mxGraphModel>` in a full `<mxfile>` document with compression support
- `validate_and_fix_xml()` — validates XML structure and applies safe fixes:
  - Adds missing `mxGeometry` elements
  - Reorders parents before children
  - Removes duplicate cell IDs
- `apply_diagram_operations()` — applies ID-based update/add/delete operations to existing XML
- `auto_fix_xml()` — applies structural fixes without regeneration

### Step 7: Pattern Library (`app/patterns/`)

Six curated reference architectures:

| Pattern ID | Name | Platform |
|-----------|------|----------|
| `three_tier_web` | 3-Tier Web Application | AWS, GCP, Azure |
| `serverless_api` | Serverless API Backend | AWS, GCP, Azure |
| `event_driven` | Event-Driven Data Pipeline | AWS, GCP |
| `data_analytics` | Data Analytics Platform | AWS, GCP |
| `microservices_k8s` | Microservices on Kubernetes | AWS, GCP, Azure |
| `static_site_cdn` | Static Site with CDN | AWS, GCP, Azure |

**Flow**: When a user prompt matches a pattern (via keyword scoring), the pattern's services, best practices, and flow description are injected into the LLM system prompt. The 3-tier pattern also has a container size scaffold in `template_merge.py`.

### Step 8: Validation (`app/validation/`)

#### Structural Validation (`app/validation/structural.py`)

`validate_architecture_structure()` checks:
- At least one node exists
- All `parent_id` references point to existing `is_container` nodes
- Container nesting depth ≤ 5 (critical issue at >5)
- No duplicate IDs
- Edge source/target IDs exist in node list
- No LLM-supplied coordinates (warns if present)
- Container has at least one child if it exists

Returns `ValidationResult` with issues categorized as `"critical"` or `"warning"`.

#### Visual Validation (`app/validation/visual.py`)

VLM-based diagram validation:
1. Captures a PNG of the rendered diagram from Draw.io
2. Sends the image to a vision-capable model (Gemini 2.5 Flash or GPT-4o)
3. The VLM analyzes for: overlapping elements, misaligned containers, clipped labels
4. **Fail-open**: if VLM is unavailable or disabled, returns a default valid result

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

### Step 10: Chat History Processing (`app/utils/chat.py`)

Two key processing steps before sending messages to the LLM:

1. **`replace_historical_tool_inputs()`** (line 45): replaces old architecture/XML payloads in assistant messages with placeholders to save tokens:
   - Architecture JSON → `[Previous architecture object omitted from prompt to save tokens]`
   - Large XML → `[Previous XML diagram omitted...]`

2. **`trim_chat_messages_for_prompt()`** (line 65): keeps the last `MAX_CHAT_TURNS_WITH_FULL_HISTORY` (12) messages with full content; older messages retain only their assistant_message text

### Step 11: URL Parsing (`app/utils/web.py`)

`fetch_url_content()`:
1. Fetches URL content with `httpx`
2. Detects content type: HTML, JSON, XML, or plain text
3. For HTML: extracts `<title>` and `<body>` text via regex
4. Returns `{title, content, char_count}` for LLM context injection

### Step 12: Configuration (`app/core/config.py`)

All configuration is environment-driven:

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `LLM_TIMEOUT_SECONDS` | `60` | LLM API timeout |
| `MAX_STRUCTURAL_RETRIES` | `3` | Architecture retry attempts |
| `MAX_VLM_RETRIES` | `2` | VLM validation retry attempts |
| `MAX_CUSTOM_MESSAGE_LEN` | `2000` | Max custom system message length |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `STRUCTURED_OUTPUT_METHOD` | `function_calling` | LLM structured output mode |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/v1` | OpenCode API endpoint |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API endpoint |

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
  "platform_convert": false
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
  "validation": {
    "structural": {"valid": true, ...},
    "vlm": null,
    "retries": 0,
    "feedback": null
  },
  "logs": [
    {"step": "request", "message": "Generation request", "level": "info", ...},
    {"step": "prompt", "message": "Prepared LLM request", "level": "info", ...},
    {"step": "llm", "message": "Calling openai (gpt-4o)", "level": "info", ...}
  ]
}
```

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

Test coverage:
- **test_hybrid_diagram.py** (8 tests) — Node/edge generation, icon resolution, edge routing, intent classification
- **test_layout_sanitize.py** (9 tests) — Coordinate stripping, container depth, XML hardening, topology
- **test_multiturn_editing.py** (1 test) — 3-turn incremental XML edit lifecycle
- **test_platform_convert.py** (4 tests) — Request schema, conversion prompt, XML inclusion
- **test_providers.py** (3 tests) — Provider registration, model normalization, URL normalization
- **test_structured_output.py** (6 tests) — Method resolution per provider, message text extraction, gateway invocation
- **test_cursor_sdk.py** (4 tests, 2 skipped) — Error formatting, prompt size, SDK integration

---

## Key Design Decisions

1. **Server-side layout**: LLMs produce unreliable coordinates; the layout engine strips them and applies deterministic auto-layout based on tier ordering and container nesting
2. **Structural retry loop**: The LLM often produces invalid architecture JSON; rather than fixing it, we retry with specific validation feedback (up to 3 attempts)
3. **VLM fail-open**: Visual validation uses a vision model; if unavailable, the diagram is accepted as-is rather than blocking the user
4. **Text JSON extraction fallback**: Not all LLM providers support structured output (tool calling); the system falls back to text-based JSON extraction for providers like OpenRouter and OpenCode
5. **ID-based XML editing**: Instead of regenerating the full XML on every edit, the system applies targeted operations (update/add/delete) by cell ID, preserving unchanged elements
6. **Pattern injection**: Matching the user's prompt against curated architecture patterns improves output quality by injecting relevant constraints and component lists into the system prompt
7. **No frontend framework**: The UI is vanilla HTML/CSS/JS with a Draw.io iframe — no build step, no framework lock-in
