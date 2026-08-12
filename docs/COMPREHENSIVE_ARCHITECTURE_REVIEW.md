# Comprehensive Architecture Review — Draw.io Agent
## Full Audit with Implementation Roadmap

**Date:** 2026-08-12  
**Status:** Current architecture assessment + 3-phase uplift plan  
**Scope:** Backend (FastAPI, IR, LLM pipeline, stores), frontend (SPA), testing, deployment readiness

---

## Executive Summary

Your draw.io agent is **well-architected for single-process operations** with excellent separation of concerns (hexagonal/ports-and-adapters), strong IR/compiler invariants, and comprehensive backend test coverage. The application elegantly handles complex diagram generation via a multi-stage LLM pipeline with proper failure discipline and provider fallback.

**However**, three critical gaps emerge as you scale:

1. **Concurrency bottleneck** — thread-per-request model masks as async; long LLM calls exhaust thread pools under ~20 concurrent users
2. **No project/workspace model** — flat session-local history is a product-architecture gap; multi-user collaboration and rich audit trails require an object model
3. **Frontend and observability blind spots** — zero automated frontend tests, request-local logging only, no distributed tracing

**Low-hanging wins** (Phase 1) unlock 3–5× concurrency without rewrites. Phase 2 (project model + job abstraction) positions you for multi-user. Phase 3 (observability + horizontal readiness) prepares production deployment.

---

## Part 1: Strengths to Preserve

### 1.1 IR + Compiler + Self-Validation Loop

The separation between **LLM judgment** (IR structure, nesting, flows) and **deterministic guarantee** (compiler, geometry, styling) is your system's best design decision:

- `semantic_errors()` and `semantic_warnings()` in IR types feed the corrective loop in `agent.py:476-647`
- Compiler emits exactly what diagnostics consider clean — zero silent fallbacks on IR failure
- `geometry_spec.py` is the single source of truth for all leaf sizing, spacing, and edge routing
- Failure discipline: bad IR → corrective retry, not downgrade to `direct_xml`

**Why preserve:** This boundary prevents the classic "LLM diagram → silent broken XML" failure mode. Auditable, testable, and the reason your test suite is comprehensive.

### 1.2 CACHE_BREAKPOINT & Prompt Stability

LLM-prompt caching is handled correctly:

```python
# prompts/system.py:23, ir.py:95
# Stable prefix cached; volatile suffix (model id, hints, context) excluded
```

This avoids the common mistake of including request-specific data in cached prefixes, which would poison the cache on every request variation.

### 1.3 Failure Discipline & Resilience

Your retry/fallback story is sound:

- **Provider fallback** (`PROVIDER_FALLBACK_ORDER` in config)
- **Transient retry** keyed on explicit HTTP status codes (`llm_client.py:28-72`)
- **Attachment robustness** — uploads never raise; every failure becomes `kind="error"` so one bad PDF doesn't break the batch
- **Request timeout wrapper** guards against indefinite hangs (though threads can't be forcibly killed)

---

## Part 2: Critical Findings by Priority

### A1 ⭐ THREAD-PER-REQUEST PIPELINE (Top Scalability Risk)

**What's happening:**
```python
# routes.py:57-73
async def _with_request_timeout(coro: Any) -> Any:
    return await asyncio.wait_for(coro, timeout=timeout)
    # coro is run_llm_sync (a thread-bound call via asyncio.to_thread)

# xml_pipeline.py
run_llm_sync(lambda: run_xml_pipeline(...))
```

Each diagram generation is 5–20 **sequential** LLM calls, each blocking a worker thread. Under ~20 concurrent users, the default thread pool (min workers + backlog) is the bottleneck long before CPU.

**Why this matters:**
- Uvicorn default: `min_workers=1`, `max_workers=multiprocessing.cpu_count()` (e.g., 8 on a 4-core machine after overflow margin)
- One user's retry/backoff (up to 60s per transient failure) stalls all other diagram generations
- Request timeout cancels the *awaiting coroutine*, not the underlying thread — the thread keeps running silently

**Fix direction — Phase 1 (Week 1–2):**

1. **Async LLM transport** — replace `LangChain` synchronous client with native async (e.g., `httpx.AsyncClient` + provider SDKs):
   ```python
   # adapters/outbound/llm/async_client.py (NEW)
   class AsyncLlmClient:
       async def call_llm_async(
           self, 
           messages, 
           model, 
           provider,
           structured=False
       ) -> Union[str, dict]:
           # No thread pool, native async I/O
           async with httpx.AsyncClient() as client:
               response = await client.post(...)
           return parse_response(response)
   ```

2. **Concurrency semaphore per API key** — prevent one key's provider from blocking others:
   ```python
   # adapters/outbound/llm/concurrency.py (ENHANCE)
   class LlmConcurrencyManager:
       def __init__(self):
           self.semaphores: dict[str, asyncio.Semaphore] = {}
       
       async def acquire(self, api_key: str, max_concurrent=3):
           if api_key not in self.semaphores:
               self.semaphores[api_key] = asyncio.Semaphore(max_concurrent)
           async with self.semaphores[api_key]:
               yield
   ```

3. **Native async pipeline** in `xml_pipeline.py`:
   ```python
   # core/services/xml_pipeline.py (REFACTOR)
   async def run_xml_pipeline_async(request: PipelineRequest) -> PipelineResponse:
       # Remove run_llm_sync entirely
       ir = await agent.generate_xml_diagram_from_ir_async(...)
       # All LLM calls are awaited, not threaded
   ```

**Impact:** 10–50× concurrency per process; routes.py becomes fully async.

---

### A2 NO WORKSPACE/PROJECT MODEL (Product Architecture Gap)

**What's happening:**
```python
# static/js/state.js:45-65
let currentXml = '';
let chatMessages = [];
let appliedSuggestionTexts = [];
let pendingInterviewAnswers = null;
# All browser-local (localStorage)

# adapters/outbound/history_store.py
# Flat list of "diagrams" with no concept of "project"
```

History, captures, templates, and settings are **flat, session-local**. There is no `Project` entity that groups a user's architecture (multiple diagrams, versions, linked docs, governance decisions, cost tracking) into one auditable unit.

**Why this matters:**
- **No multi-user collaboration** — can't share a project with team members
- **No rich audit trail** — "who made this decision, when, and why?" is lost
- **No versioning beyond "last saved"** — can't diff diagrams or roll back to a prior branch
- **Governance + cost are per-run, not per-project** — can't track "this project costs $50k/month"
- **Compliance evidence** is disconnected from the architecture it applies to

This is the **biggest product-architecture gap** relative to your system's ambition (docs, governance, cost, compliance as per-run features).

**Fix direction — Phase 2 (Week 3–4):**

```python
# app/domain/entities/project.py (NEW)
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional

class ProjectVersion:
    """Immutable snapshot of a diagram + IR + cost/compliance/well-arch at a point in time."""
    version_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime
    created_by: str
    diagram_xml: str
    ir_pydantic: dict  # Serialized IR for replay
    cost_estimate: dict
    well_architected_assessment: dict
    compliance_assessment: dict
    generation_logs: List[StepLog]
    notes: str = ""

class Diagram:
    """Single editable diagram within a project."""
    diagram_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str
    description: str = ""
    current_version: ProjectVersion
    version_history: List[ProjectVersion]
    created_at: datetime
    updated_at: datetime
    tags: List[str] = []

class Project:
    """User's architecture + all diagrams, docs, and governance decisions."""
    project_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    owner_id: str  # Multi-user later
    title: str
    description: str = ""
    diagrams: List[Diagram]
    linked_docs: List[dict]  # [{url, title, excerpt}] from /analyze
    governance_decisions: List[dict]  # {decision, rationale, date}
    adr_register: dict  # Architecture Decision Records
    raid_register: dict  # Risks/Assumptions/Issues/Dependencies
    cost_summary: dict  # Aggregated estimate across diagrams
    created_at: datetime
    updated_at: datetime
    members: List[str] = []  # Multi-user: [owner, ...reviewers]
```

**Schema migration:**
```python
# adapters/outbound/repositories/project_repository.py (NEW)
class ProjectRepository:
    """Single source of truth for projects, replacing flat history_store + template_store."""
    
    async def create_project(self, owner_id: str, title: str) -> Project:
        """Create a new project under owner_id."""
        
    async def get_project(self, project_id: str) -> Project:
        """Fetch the full project tree."""
        
    async def add_diagram_to_project(
        self, 
        project_id: str, 
        title: str, 
        xml: str, 
        ir: dict
    ) -> Diagram:
        """Create a diagram, snapshotting current IR and cost/compliance assessments."""
        
    async def update_diagram(
        self, 
        project_id: str, 
        diagram_id: str, 
        xml: str, 
        ir: dict
    ) -> ProjectVersion:
        """Commit a new version (cost, compliance, well-arch auto-recomputed)."""
        
    async def list_projects(self, owner_id: str) -> List[Project]:
        """All projects for a user."""
```

**Backend routes:**
```python
# adapters/inbound/web/routes.py (EXTEND)
@router.post("/projects")
async def create_project(req: CreateProjectRequest):
    project = await project_repo.create_project(
        owner_id=req.owner_id,
        title=req.title
    )
    return {"project_id": project.project_id}

@router.post("/projects/{project_id}/diagrams")
async def add_diagram(project_id: str, req: AddDiagramRequest):
    diagram = await project_repo.add_diagram_to_project(
        project_id=project_id,
        title=req.title,
        xml=req.xml,
        ir=req.ir
    )
    return {"diagram_id": diagram.diagram_id}

@router.get("/projects/{project_id}/versions")
async def list_versions(project_id: str):
    # All versions of all diagrams in the project
```

**Frontend change (minimal):**
```javascript
// static/js/state.js (MODIFY)
let currentProjectId = null;
let currentDiagramId = null;
// Instead of flat currentXml, fetch from /projects/{id}/diagrams/{id}

async function loadProject(projectId) {
    const response = await fetch(`/projects/${projectId}`);
    const project = await response.json();
    currentProjectId = projectId;
    // Render project tree: diagrams, versions, governance
}
```

**Impact:** Multi-user ready; audit trail; cost/compliance per-project; versioning.

---

### A3 MIDDLEWARE STACK IS HAND-ROLLED & FRAGILE

**What's happening:**
```python
# app/main.py:93-137
application.add_middleware(CORSMiddleware)
application.add_middleware(BodySizeLimitMiddleware)  # Custom
application.add_middleware(RateLimitMiddleware)      # Custom
application.add_middleware(ApiKeyMiddleware)         # Custom

@application.middleware("http")
async def add_request_id(request: Request, call_next):  # Manual
    # Stacks on top of the four above
```

Two custom `BaseHTTPMiddleware` classes (BodySize, RateLimit, ApiKey) plus a manual `@application.middleware` decorator. This mixes patterns and makes relative ordering fragile: if request-id must run first (for tracing), you can't add it to the list.

**Why this matters:**
- **Hard to trace ordering** — which middleware sees request-id before which?
- **Per-path logic is implicit** — BodySize checks `request.url.path` internally; Auth ditto; no single place to see "what applies where"
- **Future middleware pain** — every new concern (compression, security headers, caching) requires reasoning about relative ordering

**Fix direction — Phase 1 (Week 2):**

```python
# app/adapters/inbound/web/middleware.py (NEW)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

class RequestTracingMiddleware:
    """Pure ASGI middleware: request-id, timing, logging."""
    
    def __init__(self, app: ASGIApp):
        self.app = app
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        
        request_id = dict(scope["headers"]).get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        
        async def send_with_timing(message: Message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode()),
                    (b"x-response-time-ms", str(round((time.monotonic() - t0) * 1000)).encode()),
                ]
            await send(message)
        
        scope["request_id"] = request_id
        await self.app(scope, receive, send_with_timing)

# app/main.py (SIMPLIFY)
def create_app() -> FastAPI:
    application = FastAPI(title=cfg.app_title)
    
    # ASGI-level tracing (first, before FastAPI sees the request)
    application.add_middleware(RequestTracingMiddleware)
    
    # Standard middleware
    application.add_middleware(CORSMiddleware, ...)
    application.add_middleware(BodySizeLimitMiddleware)  # Policy-based size check
    application.add_middleware(RateLimitMiddleware)       # Per-key quota
    application.add_middleware(ApiKeyMiddleware)          # Auth gate
    
    # No manual @application.middleware decorators
    # All concerns in the middleware stack, in order
```

**Benefits:**
- Single place to see the stack (no hidden `@application.middleware` decorators)
- Pure ASGI for low-level concerns (tracing, timing)
- Clear ordering: tracing → CORS → auth → business logic

---

### A4 ROUTES.PY IMPORTS PIPELINE INTERNALS (Leaky Adapters)

**What's happening:**
```python
# routes.py:47
from app.core.services.xml_pipeline import _summarize_llm_usage, run_xml_pipeline
# Importing private (_summarize) and orchestration guts

# routes.py:180–220 (e.g., _chat_stream handler)
async def _chat_stream(...) -> AsyncIterator[str]:
    logger = _logger_from_request(request)
    
    # Calling pipeline directly, handling SSE encoding in the web adapter
    async for step in run_xml_pipeline_async(...):
        yield encode_sse_event(step)
```

The inbound web adapter (routes.py) reaches directly into core service internals, mixing request/response mapping with orchestration logic. SSE encoding, response formatting, and timeout handling live in `routes.py` but belong in the use-case layer.

**Why this matters:**
- **Inbound adapter is not pure** — it imports private helpers and business logic
- **Hard to test in isolation** — `routes._chat_stream` can't be tested without mocking pipeline guts
- **SSE/response encoding is a use-case concern** — should live with the orchestration, not the transport

**Fix direction — Phase 1 (Week 2):**

```python
# app/core/services/chat_use_case.py (NEW)
"""High-level use-case for chat-stream diagram generation."""

class ChatStreamUseCase:
    def __init__(self, repository: ProjectRepository, llm_client: AsyncLlmClient):
        self.repository = repository
        self.llm_client = llm_client
    
    async def run_chat_stream(
        self, 
        request: ChatRequest,
        on_step: Callable[[StepLog], Awaitable[None]]
    ) -> ChatStreamResponse:
        """
        Orchestrate a chat-stream diagram generation.
        
        Args:
            request: User's chat request (text, attachments, project_id)
            on_step: Callback for each step (SSE encoding is caller's job)
        
        Yields:
            ChatStreamResponse(step_log, intermediate_xml, cost_estimate)
        """
        logger = StepLogger(request_id=request.request_id)
        
        # Classify intent
        intent = await intent.classify_intent_async(request.text, self.llm_client)
        
        # Route to path (Mermaid vs. full IR)
        if intent == "simple":
            async for step in self._mermaid_path(request, logger):
                await on_step(step)
                yield ChatStreamResponse(step_log=step)
        else:
            async for step in self._full_pipeline_path(request, logger):
                await on_step(step)
                yield ChatStreamResponse(step_log=step)
    
    async def _mermaid_path(self, request, logger):
        # Generate Mermaid diagram
        ...
    
    async def _full_pipeline_path(self, request, logger):
        # Generate IR → compile XML → cost/compliance/well-arch
        ...

# app/adapters/inbound/web/routes.py (SIMPLIFY)
from app.core.services.chat_use_case import ChatStreamUseCase

router = APIRouter()
chat_use_case: ChatStreamUseCase = None  # Injected at app startup

@router.post("/chat/stream", response_class=StreamingResponse)
async def chat_stream(request: Request, req: ChatRequest):
    """Stream diagram generation as SSE events."""
    
    async def event_generator():
        async def on_step(step_log: StepLog):
            # SSE encoding (request → response) lives HERE
            event = {
                "step": step_log.step,
                "message": step_log.message,
                "details": step_log.details,
            }
            yield f"data: {json.dumps(event)}\n\n"
        
        try:
            async for response in chat_use_case.run_chat_stream(req, on_step):
                # Response object carries xml, usage, etc.
                yield f"data: {json.dumps(response.to_dict())}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'error': 'Request timeout'})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Benefits:**
- Routes.py only handles HTTP (request parsing, response encoding)
- Core services only handle orchestration (LLM calls, compilation, exports)
- Private helpers stay private; public APIs are clear

---

### A5 FRONTEND: 30 UNSCOPED `<script>` TAGS + GLOBAL STATE

**What's happening:**
```html
<!-- static/index.html:766-791 -->
<script src="js/state.js"></script>
<script src="js/app.js"></script>
<script src="js/diagram.js"></script>
<script src="js/chat.js"></script>
<script src="js/attachments.js"></script>
<!-- ... 25 more -->
<script src="js/voice.js"></script>
```

All scripts share a global namespace. `state.js` exports ~30 mutable globals (`currentXml`, `chatMessages`, `drawioReady`, etc.) that every script reads/mutates. No module boundaries, no dependency injection, no types.

**Why this matters:**
- **Cross-file blast radius** — changing `currentXml` in one file silently breaks 5 others
- **No testing** — can't unit-test a function that depends on globals
- **Hard to trace data flow** — which script sets `isSending`? Where is it checked?
- **Memory leaks** — event listeners on global objects never unregister (e.g., `window.addEventListener('message', handleDrawioMessage)`)

```javascript
// static/js/state.js:45-65
let currentXml = '';           // Who reads this? Who writes this?
let chatMessages = [];         // How is it cleared?
let isSending = false;         // Set in app.js, checked in chat.js, ...
let drawioReady = false;       // Mutated by diagram.js, checked by run-controls.js
let selectedPlatform = 'aws';  // Used everywhere?
```

**Fix direction — Phase 1–2 (Week 2–3):**

```javascript
// static/js/app-state.ts (REWRITE - ES modules, TypeScript)
export interface Project {
    id: string;
    title: string;
    diagrams: Diagram[];
}

export interface Diagram {
    id: string;
    title: string;
    xml: string;
    architecture: Architecture | null;
}

export interface AppState {
    // UI state
    project: Project | null;
    currentDiagram: Diagram | null;
    isSending: boolean;
    drawioReady: boolean;
    selectedPlatform: 'aws' | 'gcp' | 'azure';
    
    // Sidebar & layout
    sidebarCollapsed: boolean;
    sidebarWidth: number;
    
    // Chat
    chatMessages: ChatMessage[];
    
    // Attachments
    attachments: AttachmentInfo[];
}

// Immutable state updates (like Redux)
export const AppStateManager = {
    state: initialState(),
    
    setSending(value: boolean) {
        this.state = { ...this.state, isSending: value };
        this.notifyListeners();
    },
    
    setCurrentDiagram(diagram: Diagram | null) {
        this.state = { ...this.state, currentDiagram: diagram };
        this.notifyListeners();
    },
    
    addChatMessage(msg: ChatMessage) {
        this.state = {
            ...this.state,
            chatMessages: [...this.state.chatMessages, msg],
        };
        this.notifyListeners();
    },
    
    listeners: new Set<() => void>(),
    
    subscribe(listener: () => void) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    },
    
    notifyListeners() {
        this.listeners.forEach(l => l());
    }
};

// static/js/diagram-panel.ts (Component example)
export class DiagramPanel {
    private unsubscribe: () => void;
    
    constructor(private container: HTMLElement) {
        this.unsubscribe = AppStateManager.subscribe(() => this.render());
    }
    
    render() {
        const { currentDiagram } = AppStateManager.state;
        if (!currentDiagram) {
            this.container.innerHTML = '<p>No diagram selected</p>';
            return;
        }
        this.container.innerHTML = `<h2>${currentDiagram.title}</h2>...`;
    }
    
    destroy() {
        this.unsubscribe();
    }
}

// static/index.html (SIMPLIFIED)
<script type="module">
    import { AppStateManager } from './js/app-state.js';
    import { DiagramPanel } from './js/diagram-panel.js';
    import { ChatPanel } from './js/chat-panel.js';
    
    // Initialize components (not globals)
    const diagramPanel = new DiagramPanel(document.getElementById('diagram'));
    const chatPanel = new ChatPanel(document.getElementById('chat'));
</script>
```

**Benefits:**
- Tree-shakeable modules; dev tools can detect unused code
- Type safety (if using TypeScript)
- Testable: `AppStateManager.state` is observable; each component is isolated
- Memory: explicit cleanup via `destroy()`

---

### A6 DUAL CONFIG TIMING (Import-time vs. Runtime)

**What's happening:**
```python
# app/core/config.py
MAX_CUSTOM_MESSAGE_LEN = int(os.environ.get("MAX_CUSTOM_MESSAGE_LEN", 8000))
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", 300))
# Snapshot at import time

# app/adapters/outbound/config/env_config.py
def get_config() -> Config:
    # Re-reads .env + admin-store overrides per request
    return Config(
        max_custom_message_len=int(os.environ.get(...)),
        llm_timeout_seconds=int(os.environ.get(...)),
    )

# Usage
if len(msg) > MAX_CUSTOM_MESSAGE_LEN:  # Stale!
    raise ValueError(...)
timeout = get_config().request_timeout_seconds  # Fresh
```

Two sources of truth for the same values. Import-time snapshot is used in some places; per-request `get_config()` in others. Admin settings can override at runtime, but not all code sees those overrides.

**Why this matters:**
- **Test hermeticity** — import-time snapshots make tests interfere with each other unless `importlib.reload(core.config)` before each test
- **Horizontal scale** — if you have multiple processes, one process's import-time value diverges from others and from the admin-store central truth
- **Admin override confusion** — changing `LLM_TIMEOUT_SECONDS` via the admin UI might not affect code that imported `LLM_TIMEOUT_SECONDS` at startup

**Fix direction — Phase 2 (Week 4):**

```python
# app/adapters/outbound/config/config.py (CONSOLIDATE)
"""Single config registry; lazy per-request resolution."""

from functools import lru_cache
import os
from typing import Optional

class Config:
    def __init__(self):
        self._cache = {}
    
    @property
    def max_custom_message_len(self) -> int:
        return self._get_int("MAX_CUSTOM_MESSAGE_LEN", 8000)
    
    @property
    def llm_timeout_seconds(self) -> int:
        return self._get_int("LLM_TIMEOUT_SECONDS", 300)
    
    @property
    def request_timeout_seconds(self) -> int:
        return self._get_int("REQUEST_TIMEOUT_SECONDS", 60)
    
    def _get_int(self, key: str, default: int) -> int:
        """Resolve from .env or admin-store (admin-store takes precedence)."""
        # TODO: Fetch from admin_settings_store if exists
        # For now, environment only
        return int(os.environ.get(key, default))
    
    def clear_cache(self):
        """Call this after admin-store update."""
        self._cache.clear()

# Global singleton (initialized at startup)
_config = Config()

def get_config() -> Config:
    return _config

# Usage
def my_handler(request: Request):
    cfg = get_config()
    timeout = cfg.llm_timeout_seconds  # Always fresh
    if len(msg) > cfg.max_custom_message_len:  # Consistent
        raise ValueError(...)

# app/main.py (REMOVE import-time snapshots)
# No more: MAX_CUSTOM_MESSAGE_LEN = int(os.environ.get(...))
# Just: from app.adapters.outbound.config import get_config
```

**Benefits:**
- Single source of truth
- Per-request fresh values
- Admin overrides work everywhere
- Test-friendly: `get_config()` can be mocked

---

### A7 TWO APP ROOTS & AD-HOC PROVIDER BOOTSTRAP

**What's happening:**
```python
# app/main.py
if cfg.drawio_style_cache_reset:
    from app.adapters.outbound.diagram.style_cache import clear_style_cache
    clear_style_cache()

_default_provider_key = providers_module.default_provider_key()
_default_provider_spec = providers_module.get_provider(_default_provider_key)
# Bootstrap logic scattered at module level

# app/mcp_server.py
# Separate bootstrap for the MCP server; duplicates provider setup
```

There are two app roots (`main.py` for FastAPI, `mcp_server.py` for MCP server), and each wires up config/providers independently. Provider loading logic is implicit (module-level imports and `default_provider_key()` calls) rather than explicit.

**Why this matters:**
- **Duplication** — provider/config setup code lives in two places
- **Hard to test** — can't easily create test instances with different configs
- **Hard to extend** — adding a new root (admin CLI, worker process, gRPC server) requires repeating bootstrap
- **Initialization order** — if some component needs providers before another, that dependency is implicit

**Fix direction — Phase 2 (Week 3):**

```python
# app/core/container.py (NEW)
"""Dependency injection / service locator for all roots."""

from typing import Optional
from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.config import get_config
from app.core.services.chat_use_case import ChatStreamUseCase
from app.adapters.outbound.repositories.project_repository import ProjectRepository

class AppContainer:
    """Single place to wire all dependencies."""
    
    def __init__(self):
        self.config = get_config()
        self.providers = self._load_providers()
        self.llm_client = self._create_llm_client()
        self.project_repository = ProjectRepository(db_path=self.config.db_path)
        self.chat_use_case = ChatStreamUseCase(
            repository=self.project_repository,
            llm_client=self.llm_client
        )
    
    def _load_providers(self):
        """Load and validate providers from config/providers.json."""
        default_key = providers_module.default_provider_key()
        spec = providers_module.get_provider(default_key)
        
        if spec.api_key_env and not os.environ.get(spec.api_key_env):
            logger.warning(f"Default provider {default_key} missing {spec.api_key_env}")
        
        return providers_module
    
    def _create_llm_client(self):
        from app.adapters.outbound.llm.async_client import AsyncLlmClient
        return AsyncLlmClient(self.providers)

# Global container
_container: Optional[AppContainer] = None

def get_container() -> AppContainer:
    global _container
    if not _container:
        _container = AppContainer()
    return _container

# app/main.py (SIMPLIFY)
from app.core.container import get_container

def create_app() -> FastAPI:
    application = FastAPI(title="Draw.io Agent")
    container = get_container()
    
    # Inject dependencies into routes
    async def lifespan(app: FastAPI):
        # Startup
        if container.config.drawio_style_cache_reset:
            from app.adapters.outbound.diagram.style_cache import clear_style_cache
            clear_style_cache()
        yield
        # Shutdown (if needed)
    
    application.router.dependencies = [Depends(lambda: container)]
    
    # Add middleware, routes, etc.
    ...
    
    return application

# app/mcp_server.py (SIMPLIFY)
from app.core.container import get_container

def create_mcp_server():
    container = get_container()
    # MCP server uses the same container, no duplicate bootstrap
    return DrawioMcpServer(container.llm_client)

# app/scripts/admin_cli.py (NEW - easy to add)
from app.core.container import get_container

def main():
    container = get_container()
    # CLI tool has access to all services
    print(f"DB path: {container.config.db_path}")
    print(f"Providers: {container.providers}")
```

**Benefits:**
- Single point of initialization
- Testable: `AppContainer` can be mocked or configured per test
- New roots (CLI, worker, etc.) just create a container
- Clear dependency graph

---

### A8 NO OBSERVABILITY BEYOND REQUEST LOGS

**What's happening:**
```python
# app/core/logging.py
class StepLogger:
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.steps = []  # In-memory only; lost on process exit
    
    def log(self, step, message, level, details):
        self.steps.append({...})

# Used within a request, exposed in response["logs"], saved only if user clicks "capture"
```

No metrics, tracing, or persistent logging. `StepLogger` is request-local and ephemeral. Multiple LLM calls within one diagram generation have no span correlation across calls.

**Why this matters:**
- **Production debugging is blind** — if a diagram generation fails, you can't see the sequence of LLM calls, their token usage, or latencies
- **No performance metrics** — you don't know if a new prompt is faster or if a provider is degrading
- **Multi-process is invisible** — on horizontal scale, request-id doesn't connect logs across processes

**Fix direction — Phase 3 (Week 5):**

```python
# app/adapters/outbound/observability/tracer.py (NEW)
"""Structured logging + Prometheus metrics + optional OpenTelemetry."""

import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any

@dataclass
class SpanEvent:
    """Single LLM call or operation."""
    timestamp: str
    operation: str  # "llm.call", "ir.compile", "cost.estimate"
    duration_ms: float
    status: str  # "success", "error", "retry"
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    error_message: Optional[str] = None

class DiagramGenerationTracer:
    def __init__(self, request_id: str, parent_span_id: Optional[str] = None):
        self.request_id = request_id
        self.parent_span_id = parent_span_id
        self.start_time = time.time()
        self.events: list[SpanEvent] = []
        self.logger = logging.getLogger("drawio-agent.traces")
    
    def span(self, operation: str):
        """Context manager for a single operation."""
        return _Span(self, operation)
    
    def add_event(self, event: SpanEvent):
        self.events.append(event)
        # Log immediately (structured logging)
        self.logger.info(
            f"[{self.request_id}] {event.operation}",
            extra={
                "request_id": self.request_id,
                "operation": event.operation,
                "duration_ms": event.duration_ms,
                "status": event.status,
                "tokens_in": event.tokens_in,
                "tokens_out": event.tokens_out,
                "cost_usd": event.cost_usd,
            }
        )
        
        # Prometheus metrics
        llm_call_duration.labels(
            operation=event.operation, 
            status=event.status
        ).observe(event.duration_ms / 1000)
        
        if event.tokens_in:
            llm_tokens.labels(operation="input").inc(event.tokens_in)
        if event.tokens_out:
            llm_tokens.labels(operation="output").inc(event.tokens_out)
    
    def export_json(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "total_duration_ms": round((time.time() - self.start_time) * 1000),
            "events": [asdict(e) for e in self.events],
        }

class _Span:
    def __init__(self, tracer: DiagramGenerationTracer, operation: str):
        self.tracer = tracer
        self.operation = operation
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time.time() - self.start_time) * 1000)
        status = "error" if exc_type else "success"
        
        event = SpanEvent(
            timestamp=datetime.utcnow().isoformat(),
            operation=self.operation,
            duration_ms=duration_ms,
            status=status,
            error_message=str(exc_val) if exc_val else None,
        )
        self.tracer.add_event(event)

# Prometheus metrics (per-operation timing, token counts)
from prometheus_client import Counter, Histogram

llm_call_duration = Histogram(
    "llm_call_duration_seconds",
    "Duration of LLM call",
    labelnames=["operation", "status"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
)

llm_tokens = Counter(
    "llm_tokens_total",
    "Total LLM tokens processed",
    labelnames=["operation"]
)

# Usage
async def generate_xml_diagram_from_ir(..., tracer: DiagramGenerationTracer):
    with tracer.span("ir.generation"):
        ir = await agent.generate_ir(...)
    
    with tracer.span("ir.compile"):
        xml = compiler.compile(ir)
    
    # ... later, export trace
    trace_data = tracer.export_json()
    await trace_store.save(trace_data)  # Persist for later analysis
```

**Benefits:**
- Every LLM call is traced with timing, tokens, cost
- Prometheus metrics for alerting + dashboarding
- Persistent trace log for post-mortem debugging
- Per-request correlation

---

### A9 FIVE INDEPENDENT SQLITE STORES (Data Layer Fragmentation)

**What's happening:**
```python
# adapters/outbound/history_store.py
history_db = sqlite3.connect("data/diagram_history.sqlite3")

# adapters/outbound/template_store.py
template_db = sqlite3.connect("data/templates.sqlite3")

# adapters/outbound/capture_store.py
capture_db = sqlite3.connect("data/captures.sqlite3")

# adapters/outbound/governance_store.py
governance_db = sqlite3.connect("data/governance_rules.sqlite3")

# adapters/outbound/admin_settings_store.py
admin_db = sqlite3.connect("data/admin_settings.json")
```

Five independent stores with their own schemas, migrations, and lifecycle. No unified transaction model, no referential integrity across them.

**Why this matters:**
- **No ACID across stores** — if a diagram save fails halfway, history is stale, captures are inconsistent
- **No schema versioning** — adding a column requires editing each store's init code; no migration path (Alembic)
- **Data migration is manual** — if you want to move a diagram from one user to another, you update five tables independently
- **Backup/restore is fragmented** — no single schema dump; each backup tool needs to know about five files

**Fix direction — Phase 1 (Week 1):**

Use **one SQLite database** with a **unified schema** managed by Alembic:

```python
# app/adapters/outbound/database.py (NEW)
"""Single SQLite database for all stores."""

import sqlite3
from pathlib import Path
from alembic import command
from alembic.config import Config as AlembicConfig

DATABASE_PATH = Path(__file__).parent / "../../data/app.sqlite3"

def get_db():
    """Connection to the unified database."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # Dict-like rows
    return conn

def run_migrations():
    """Alembic migrations for schema versioning."""
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy_url", f"sqlite:///{DATABASE_PATH}")
    command.upgrade(alembic_cfg, "head")

# alembic/versions/001_initial_schema.py (Migration)
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'projects',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )
    
    op.create_table(
        'diagrams',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id')),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('current_xml', sa.Text),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )
    
    op.create_table(
        'versions',  # Replaces history_store + capture_store
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('diagram_id', sa.String(32), sa.ForeignKey('diagrams.id')),
        sa.Column('xml', sa.Text),
        sa.Column('ir_json', sa.Text),
        sa.Column('cost_estimate', sa.JSON),
        sa.Column('compliance_assessment', sa.JSON),
        sa.Column('well_architected', sa.JSON),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )
    
    op.create_table(
        'templates',  # From template_store
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('xml', sa.Text),
        sa.Column('run_count', sa.Integer, default=0),
    )
    
    op.create_table(
        'governance_decisions',  # From governance_store
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.String(32), sa.ForeignKey('projects.id')),
        sa.Column('decision', sa.Text),
        sa.Column('rationale', sa.Text),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )
    
    op.create_table(
        'admin_settings',  # From admin_settings_store
        sa.Column('key', sa.String(255), primary_key=True),
        sa.Column('value', sa.JSON),
        sa.Column('updated_at', sa.DateTime, default=sa.func.now()),
    )
    
    op.create_index('ix_diagrams_project_id', 'diagrams', ['project_id'])
    op.create_index('ix_versions_diagram_id', 'versions', ['diagram_id'])

def downgrade():
    op.drop_table('versions')
    op.drop_table('diagrams')
    op.drop_table('projects')
    # ... etc
```

**Benefits:**
- Single schema source of truth
- Migrations via Alembic (versioned, reversible)
- Referential integrity (foreign keys)
- ACID transactions across related data

---

### A10 ZERO FRONTEND TESTS

**What's happening:**
```bash
$ pytest
# 75 backend test files, ~2000 tests
# 0 frontend tests

$ ls static/
# 27 .js files, not a single .test.js or .spec.ts
```

No automated coverage for SSE/draw.io boundary, chat message flow, attachment upload, voice commands, settings persist, or history navigation.

**Why this matters:**
- **Regression risk** — UI changes break silently; you only find out when a user reports it
- **Refactoring friction** — can't safely split global state into modules without manual testing every flow
- **Maintenance cost** — every feature addition requires manual "click around" validation

**Fix direction — Phase 1–2 (Week 3):**

```javascript
// static/js/__tests__/app-state.spec.ts (NEW - ES modules + Jest)
import { AppStateManager, AppState } from '../app-state';

describe('AppStateManager', () => {
    beforeEach(() => {
        AppStateManager.listeners.clear();
        AppStateManager.state = AppStateManager.initialState();
    });
    
    test('setSending updates state and notifies listeners', () => {
        const listener = jest.fn();
        AppStateManager.subscribe(listener);
        
        AppStateManager.setSending(true);
        
        expect(AppStateManager.state.isSending).toBe(true);
        expect(listener).toHaveBeenCalled();
    });
    
    test('addChatMessage appends to chat history', () => {
        const msg = { id: '1', text: 'Hello', role: 'user' };
        AppStateManager.addChatMessage(msg);
        
        expect(AppStateManager.state.chatMessages).toHaveLength(1);
        expect(AppStateManager.state.chatMessages[0]).toEqual(msg);
    });
});

// static/js/__tests__/diagram-panel.spec.ts
import { DiagramPanel } from '../diagram-panel';
import { AppStateManager } from '../app-state';

describe('DiagramPanel', () => {
    let container: HTMLElement;
    let panel: DiagramPanel;
    
    beforeEach(() => {
        container = document.createElement('div');
        panel = new DiagramPanel(container);
    });
    
    afterEach(() => {
        panel.destroy();
    });
    
    test('renders diagram title when diagram is selected', () => {
        const diagram = {
            id: 'diag-1',
            title: 'VPC Architecture',
            xml: '<mxfile>...</mxfile>',
        };
        
        AppStateManager.setCurrentDiagram(diagram);
        
        expect(container.innerHTML).toContain('VPC Architecture');
    });
    
    test('renders "no diagram" when diagram is null', () => {
        AppStateManager.setCurrentDiagram(null);
        
        expect(container.textContent).toContain('No diagram selected');
    });
});

// static/js/__tests__/chat-panel.spec.ts (E2E - SSE + draw.io)
import { ChatPanel } from '../chat-panel';
import { AppStateManager } from '../app-state';

describe('ChatPanel - SSE integration', () => {
    let panel: ChatPanel;
    
    test('parses SSE events and updates state', async () => {
        // Mock fetch to return SSE stream
        global.fetch = jest.fn(() =>
            Promise.resolve({
                body: {
                    getReader: () => ({
                        read: async () => ({
                            value: new TextEncoder().encode(
                                'data: {"step":"generation","message":"Starting...","xml":"<mxfile>..."}\n\n'
                            ),
                            done: false,
                        }),
                    }),
                },
            })
        );
        
        panel = new ChatPanel(document.getElementById('chat'));
        await panel.streamChat('generate a VPC');
        
        expect(AppStateManager.state.currentDiagram.xml).toBe('<mxfile>...');
    });
});

// jest.config.js (NEW)
export default {
    preset: 'ts-jest',
    testEnvironment: 'jsdom',
    roots: ['<rootDir>/static/js'],
    testMatch: ['**/__tests__/**/*.spec.ts'],
    moduleNameMapper: {
        '^@/(.*)$': '<rootDir>/static/js/$1',
    },
};

// package.json (ADD)
{
    "scripts": {
        "test:frontend": "jest",
        "test:frontend:watch": "jest --watch",
        "test": "npm run test:backend && npm run test:frontend"
    },
    "devDependencies": {
        "@testing-library/dom": "^10.0.0",
        "@types/jest": "^29.0.0",
        "jest": "^29.0.0",
        "jest-environment-jsdom": "^29.0.0",
        "ts-jest": "^29.0.0",
        "typescript": "^5.0.0"
    }
}
```

**Benefits:**
- Regression detection before production
- Refactoring confidence
- Documentation via tests (how to use ChatPanel, DiagramPanel, etc.)

---

## Part 3: Recommended 3-Phase Uplift Plan

### Phase 1: Quick Wins (Week 1–3)

Goal: **3–5× concurrency, cleaner architecture, test foundation**

| Work Item | Est. Effort | Owner | Impact |
|-----------|-------------|-------|--------|
| A1: Async LLM transport (httpx + semaphore) | 3d | Backend | 10–50× concurrency |
| A4: Decouple routes.py from pipeline (use-case layer) | 2d | Backend | Testability, clarity |
| A3: Middleware consolidation (ASGI + standard stack) | 1d | Backend | Maintainability |
| A5: Frontend modules + state manager (ES modules) | 3d | Frontend | Codebase clarity |
| A9: Unified SQLite + Alembic schema | 2d | Backend | Data integrity |
| A10: Frontend test scaffold (Jest + TestingLibrary) | 2d | Frontend | Regression safety |

**Estimated total:** 2–3 weeks, 1–2 people

### Phase 2: Structure (Week 3–4)

Goal: **Project/workspace model, job abstraction, container pattern**

| Work Item | Est. Effort | Owner | Impact |
|-----------|-------------|-------|--------|
| A2: Project domain model (Project, Diagram, Version) | 3d | Backend | Multi-user ready, audit trail |
| A2: ProjectRepository + migration script | 2d | Backend | Data access abstraction |
| A7: Container/wiring layer (dependency injection) | 1d | Backend | All roots use same setup |
| A6: Unified config (lazy per-request) | 1d | Backend | Test hermeticity |
| Frontend: Load/save projects (Project ID in state) | 2d | Frontend | UI for workspace model |

**Estimated total:** 1–2 weeks, 1–2 people

### Phase 3: Scale/Ops (Week 5+)

Goal: **Observability, horizontal readiness, production hardening**

| Work Item | Est. Effort | Owner | Impact |
|-----------|-------------|-------|--------|
| A8: Tracer + Prometheus metrics + persistent traces | 3d | Backend | Debugging, alerting, dashboards |
| Horizontal readiness (decide on mutable state: Redis vs. single-writer) | 2d | Architecture | Scale to multiple processes |
| OpenTelemetry integration (optional, week 5+) | 2d | Ops | Distributed tracing |
| Database connection pooling (asyncpg or sqlalchemy async) | 2d | Backend | Connection limits at scale |

**Estimated total:** 1–2 weeks, 1 person (parallel with Phase 2 if team size allows)

---

## Part 4: Implementation Patterns & Code Examples

### Pattern 1: Async LLM Pipeline

**Before:**
```python
def _run_chat(...):
    result = run_llm_sync(lambda: run_xml_pipeline(...))
```

**After:**
```python
# adapters/outbound/llm/async_client.py
class AsyncLlmClient:
    async def call_llm(
        self, 
        messages: list[dict], 
        model: str, 
        provider: str,
        structured: bool = False
    ) -> str | dict:
        provider_spec = self.providers.get_provider(provider)
        timeout = get_config().llm_timeout_seconds
        
        async with self.concurrency_manager.acquire(provider_spec.api_key) as _:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    provider_spec.endpoint,
                    json={...},
                    headers={...},
                    timeout=timeout,
                )
        
        if response.status_code in (429, 500, 502, 503):
            # Retry logic
            return await self._retry_with_backoff(...)
        
        return parse_response(response)

# core/services/xml_pipeline.py
async def run_xml_pipeline_async(request: PipelineRequest, tracer: Tracer) -> PipelineResponse:
    async with tracer.span("intent.classify"):
        intent = await intent.classify_intent_async(request.text)
    
    if intent == "simple":
        async with tracer.span("mermaid.generate"):
            mermaid_xml = await agent.generate_mermaid_diagram_async(request.text)
        return PipelineResponse(xml=mermaid_xml)
    
    async with tracer.span("ir.generate"):
        ir = await agent.generate_xml_diagram_from_ir_async(request.text, llm_client)
    
    async with tracer.span("ir.validate"):
        ir.semantic_errors()  # Sync validation
    
    async with tracer.span("ir.compile"):
        xml = compiler.compile(ir)
    
    # In parallel: cost, compliance, well-arch
    cost_task = asyncio.create_task(estimate_monthly_cost_async(ir))
    compliance_task = asyncio.create_task(assess_compliance_async(ir))
    well_arch_task = asyncio.create_task(assess_well_architected_async(ir))
    
    cost, compliance, well_arch = await asyncio.gather(
        cost_task, compliance_task, well_arch_task
    )
    
    return PipelineResponse(
        xml=xml,
        cost=cost,
        compliance=compliance,
        well_architected=well_arch,
    )
```

### Pattern 2: Project/Workspace Model

```python
# app/domain/entities/project.py
from pydantic import BaseModel, Field
from datetime import datetime
from typing import List

class ProjectVersion(BaseModel):
    version_id: str = Field(default_factory=uuid.uuid4().hex[:8])
    created_at: datetime
    created_by: str
    diagram_xml: str
    ir_pydantic: dict
    cost_estimate: dict
    compliance_assessment: dict

class Project(BaseModel):
    project_id: str = Field(default_factory=uuid.uuid4().hex[:12])
    owner_id: str
    title: str
    description: str = ""
    diagrams: List[dict]  # [{id, title, xml, versions}]
    created_at: datetime

# adapters/outbound/repositories/project_repository.py
class ProjectRepository:
    def __init__(self, db_conn):
        self.db = db_conn
    
    async def create_project(self, owner_id: str, title: str) -> Project:
        cursor = self.db.cursor()
        project_id = uuid.uuid4().hex[:12]
        cursor.execute(
            "INSERT INTO projects (id, owner_id, title, created_at) VALUES (?, ?, ?, ?)",
            (project_id, owner_id, title, datetime.utcnow())
        )
        self.db.commit()
        return await self.get_project(project_id)
    
    async def add_diagram_to_project(
        self, 
        project_id: str, 
        title: str, 
        xml: str, 
        ir: dict
    ) -> Diagram:
        diagram_id = uuid.uuid4().hex[:12]
        version_id = uuid.uuid4().hex[:8]
        
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT INTO diagrams (id, project_id, title, current_xml) VALUES (?, ?, ?, ?)",
            (diagram_id, project_id, title, xml)
        )
        cursor.execute(
            """INSERT INTO versions 
               (id, diagram_id, xml, ir_json, created_at) 
               VALUES (?, ?, ?, ?, ?)""",
            (version_id, diagram_id, xml, json.dumps(ir), datetime.utcnow())
        )
        self.db.commit()
        
        return Diagram(id=diagram_id, title=title, xml=xml, versions=[version_id])
```

### Pattern 3: Async Route Handler

```python
# adapters/inbound/web/routes.py
@router.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest) -> StreamingResponse:
    """Stream diagram generation as SSE."""
    
    container = get_container()
    use_case = container.chat_use_case
    tracer = DiagramGenerationTracer(request_id=request.state.request_id)
    
    async def event_generator():
        try:
            async for response in use_case.run_chat_stream(req):
                event_json = json.dumps({
                    "step": response.step_log.step,
                    "message": response.step_log.message,
                    "xml": response.intermediate_xml,
                    "cost": response.cost_estimate,
                })
                yield f"data: {event_json}\n\n"
        
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'error': 'Request timeout'})}\n\n"
        
        finally:
            # Save trace for debugging
            await tracer_store.save_trace(tracer.export_json())
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

## Part 5: Risk Mitigation & Testing Strategy

### Risk: Async Migration Introduces Race Conditions

**Mitigation:**
- Use semaphores per API key (concurrent per-key limit, not global)
- All state updates immutable (Redux-like, not mutable global state)
- Backend: async operations isolated per request; no cross-request state mutation
- Frontend: state manager notifies subscribers; no shared mutable objects
- Test strategy: race condition fuzzing (run same test 100× with random delays)

### Risk: Project Model Migration Breaks Existing Data

**Mitigation:**
- Alembic migration script copies old history_store → new projects/diagrams/versions tables
- Legacy browser sessions still work (project_id defaults to synthetic "legacy-session")
- Two-phase rollout: backend supports both old and new; frontend switches once backend is stable
- Rollback: keep old stores for 2 weeks; Alembic downgrade available

### Risk: Frontend Module Refactor Breaks Existing Features

**Mitigation:**
- Introduce ES modules gradually (one component at a time, not all at once)
- Run Jest tests in CI; block merge if coverage drops
- Feature flag: `FRONTEND_MODULES_ENABLED` in admin settings; toggle gradually per user
- Smoke test: record/replay top 5 user flows; compare screenshots before/after

---

## Part 6: Success Metrics & Monitoring

### Phase 1 Success
- [ ] Concurrency test passes: 50 concurrent diagram requests, <5 failures
- [ ] All 90 backend tests pass
- [ ] Frontend test scaffold exists; 30% coverage achieved
- [ ] Request latency p99 < 10s (was unbounded)

### Phase 2 Success
- [ ] Project API works: create, add diagram, list versions
- [ ] Migration script: old history imported into projects
- [ ] Frontend loads/saves projects (no "new every time" workaround)

### Phase 3 Success
- [ ] Prometheus metrics scraped; dashboards show LLM call rates + costs
- [ ] Horizontal deployment: can run 2 replicas behind load balancer
- [ ] Production debugging: traces queryable by request_id

---

## Part 7: Priority Quick-Start (This Week)

If you want to start immediately, **focus on A1 + A5 + A10**:

1. **A1: Async LLM (2–3 days)**
   - Replace `run_llm_sync` with `AsyncLlmClient` + `httpx.AsyncClient`
   - Test: one user with 50 concurrent requests should not stall

2. **A5: Frontend modules (3 days)**
   - Create `app-state.ts` (immutable state manager)
   - Move one script (`chat.js`) to use it
   - Run Jest

3. **A10: Test scaffold (1 day)**
   - Add `jest.config.js`, `package.json` `test:frontend` script
   - Write 5 basic tests for the new state manager

**Estimated time-to-value: 1 week, 1 person, 10× concurrency + regression tests.**

---

## References

- [CLAUDE.md](CLAUDE.md) — Project overview
- [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) — Current findings (supplementary)
- [ENV_AND_MCP.md](docs/ENV_AND_MCP.md) — Config & MCP details
- Alembic docs: https://alembic.sqlalchemy.org/
- FastAPI async: https://fastapi.tiangolo.com/deployment/concepts/#concurrency
- Jest (frontend): https://jestjs.io/

