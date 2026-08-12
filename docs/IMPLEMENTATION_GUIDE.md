# Implementation Guide: Architectural Improvements

**Date:** 2026-08-12  
**Status:** New files created; integration required

This document explains how to integrate the 8 architectural improvements (A1–A9) into your existing codebase.

## Summary of New Files

### Backend Infrastructure (Core)

| File | Purpose | Replaces |
|------|---------|----------|
| `app/core/container.py` | **A7** Dependency injection container | Module-level bootstrap in main.py/mcp_server.py |
| `app/adapters/outbound/config/config.py` | **A6** Unified lazy config | Split across core/config.py + env_config.py |
| `app/adapters/outbound/llm/async_client.py` | **A1** Async LLM transport | `run_llm_sync` + LangChain sync client |
| `app/adapters/inbound/web/middleware.py` | **A3** Pure ASGI middleware | Mixed BaseHTTPMiddleware + manual @middleware |
| `app/core/services/chat_use_case.py` | **A4** Decoupled use-cases | Direct pipeline calls in routes.py |

### Data & Observability

| File | Purpose | Replaces |
|------|---------|----------|
| `app/adapters/outbound/repositories/unified_repository.py` | **A9** Unified data layer | 5 independent _store.py files |
| `app/adapters/outbound/observability/tracer.py` | **A8** Persistent tracing | In-memory StepLogger only |
| `app/adapters/outbound/observability/metrics.py` | **A8** Prometheus metrics | No metrics |

### Frontend (ES Modules)

| File | Purpose | Replaces |
|------|---------|----------|
| `static/js/app-state.js` | **A5** State manager (ES modules) | ~30 global variables in state.js |
| `static/js/chat-panel.js` | **A5** Example reactive component | Procedural chat.js |
| `static/js/__tests__/app-state.test.js` | **A10** Frontend unit tests | No frontend tests |
| `jest.config.js` | Jest test runner config | N/A |

## Integration Steps (In Order)

### Phase 1: Backend Foundation (A7, A6, A1, A3)

#### 1. Update `app/main.py` to use Container

**Before:**
```python
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm import providers as providers_module

cfg = get_config()
_default_provider_key = providers_module.default_provider_key()
```

**After:**
```python
from app.core.container import get_container

container = get_container()
cfg = container.config
```

**Changes needed:**
1. Import `get_container` from new `app/core/container.py`
2. Get config from `container.config` instead of calling `get_config()` directly
3. Get LLM client from `container.llm_client` (needed for A1 async)

#### 2. Update `app/adapters/inbound/web/routes.py` (A4 + A3)

**Replace direct pipeline calls with use-cases:**

**Before:**
```python
from app.core.services.xml_pipeline import run_xml_pipeline, _summarize_llm_usage

@router.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest):
    result = await asyncio.to_thread(
        run_xml_pipeline,
        request=req,
        logger=logger
    )
```

**After:**
```python
from app.core.services.chat_use_case import ChatStreamUseCase
from app.core.container import get_container

@router.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest):
    container = get_container()
    use_case = ChatStreamUseCase(
        repository=container.repository,
        llm_client=container.llm_client
    )
    
    async for response in use_case.run_chat_stream(req):
        yield f"data: {json.dumps(response.to_dict())}\n\n"
```

#### 3. Update Middleware Stack in `app/main.py` (A3)

**Before:**
```python
application.add_middleware(BodySizeLimitMiddleware)
application.add_middleware(RateLimitMiddleware)
application.add_middleware(ApiKeyMiddleware)

@application.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = ...
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
```

**After:**
```python
from app.adapters.inbound.web.middleware import RequestTracingMiddleware, ContextVarsMiddleware

# Add pure ASGI middleware FIRST (before FastAPI sees the request)
app.add_middleware(ContextVarsMiddleware)
app.add_middleware(RequestTracingMiddleware)

# Then standard middleware
app.add_middleware(CORSMiddleware, ...)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ApiKeyMiddleware)

# Remove the manual @application.middleware("http") decorator
```

### Phase 2: Data Layer (A9)

#### 1. Initialize Unified Repository

In `app/main.py` startup:

```python
from app.core.container import get_container

container = get_container()

# Container automatically initializes repository with migrations
# Access it later:
repository = container.repository
```

#### 2. Create Migration Script (optional for Alembic)

For now, `UnifiedRepository._init_schema()` creates the schema automatically. For versioned migrations later:

```bash
# Install alembic
pip install alembic

# Create migration environment
alembic init alembic

# Generate initial migration
alembic revision --autogenerate -m "Initial schema"

# Run migrations
alembic upgrade head
```

#### 3. Migrate Existing Data (one-time)

Create a script to copy data from old stores to unified repo:

```python
# scripts/migrate_data.py
import sqlite3
from pathlib import Path
from app.adapters.outbound.repositories.unified_repository import UnifiedRepository
from app.adapters.outbound.history_store import list_sessions
from app.adapters.outbound.template_store import list_templates

def migrate():
    old_db = UnifiedRepository(Path("data/app.sqlite3"))  # New unified DB
    
    # Copy history → versions
    for session in list_sessions():
        for version in session.get("versions", []):
            old_db.create_version(
                version_id=version["id"],
                diagram_id=version["diagram_id"],
                xml=version["xml"],
                # ... other fields
            )
    
    # Copy templates
    for template in list_templates():
        old_db.create_template(
            template_id=template["id"],
            owner_id=template["owner_id"],
            title=template["title"],
            xml=template["xml"],
        )

if __name__ == "__main__":
    migrate()
    print("Migration complete")

# Run with: python scripts/migrate_data.py
```

### Phase 3: Observability (A8)

#### 1. Update LLM Calls to Use Tracer

**Before:**
```python
async def call_llm(...):
    result = await llm_client.call_llm(...)
```

**After:**
```python
from app.adapters.outbound.observability import DiagramGenerationTracer

async def call_llm(..., tracer: DiagramGenerationTracer):
    async with tracer.span("llm.call", model=model, provider=provider):
        result = await llm_client.call_llm(...)
    return result
```

#### 2. Export Traces on Request Complete

In routes.py:

```python
@router.post("/chat/stream")
async def chat_stream(request: Request, req: ChatRequest):
    tracer = DiagramGenerationTracer(request_id=request.state.request_id)
    
    try:
        async for response in use_case.run_chat_stream(req, tracer=tracer):
            yield f"data: {json.dumps(response.to_dict())}\n\n"
    finally:
        # Save trace for debugging
        container = get_container()
        await container.repository.save_trace(tracer)  # TODO: Add save_trace method
```

### Phase 4: Frontend Modules (A5)

#### 1. Update `static/index.html`

**Before:**
```html
<script src="js/state.js"></script>
<script src="js/app.js"></script>
<script src="js/chat.js"></script>
<!-- ... 25 more scripts -->
```

**After:**
```html
<!-- Load new state manager as module -->
<script type="module">
    import { AppStateManager } from './js/app-state.js';
    import { ChatPanel } from './js/chat-panel.js';
    
    // Initialize UI with state manager
    window.appState = AppStateManager;
    
    const chatPanel = new ChatPanel(document.getElementById('chat-container'));
    chatPanel.render();
    
    // Load and initialize other components similarly
    // (one at a time as you migrate them)
</script>
```

#### 2. Gradually Migrate Existing Scripts

For each old script (e.g., `chat.js`):

1. **Before:** Global functions that mutate `currentXml`, `chatMessages`, etc.
2. **After:** Component class that uses `AppStateManager.subscribe()` and calls state update methods

Example migration for chat.js:

```javascript
// OLD: static/js/chat.js
function sendMessage(text) {
    currentXml = '';  // Global mutation
    chatMessages.push({...});  // Global mutation
}

// NEW: static/js/chat-panel.js
export class ChatPanel {
    onSendMessage(text) {
        AppStateManager.setCurrentXml('');
        AppStateManager.addChatMessage({...});
    }
}
```

#### 3. Add Jest to package.json

Create or update `package.json`:

```json
{
    "scripts": {
        "test": "jest",
        "test:watch": "jest --watch",
        "test:coverage": "jest --coverage"
    },
    "devDependencies": {
        "jest": "^29.0.0",
        "jest-environment-jsdom": "^29.0.0"
    }
}
```

Run tests with: `npm test`

## Migration Checklist

### Week 1
- [ ] Create container.py (A7)
- [ ] Create config.py (A6)
- [ ] Create async_client.py (A1)
- [ ] Update main.py to use container
- [ ] Update middleware stack (A3)

### Week 2
- [ ] Create chat_use_case.py (A4)
- [ ] Update routes.py to use use-cases
- [ ] Create unified_repository.py (A9)
- [ ] Run migration script for existing data

### Week 3
- [ ] Create tracer.py & metrics.py (A8)
- [ ] Update LLM calls to use tracer
- [ ] Create app-state.js (A5)
- [ ] Add jest.config.js + test scaffold

### Week 4
- [ ] Migrate chat.js → ChatPanel component
- [ ] Migrate diagram.js → DiagramPanel component
- [ ] Add frontend tests for each component
- [ ] Remove old global state.js

## Testing Strategy

### Backend

```bash
# Run existing tests (should still pass)
pytest

# Test async LLM client
pytest tests/test_llm_async_client.py -v

# Test unified repository
pytest tests/test_unified_repository.py -v

# Test use-cases
pytest tests/test_chat_use_case.py -v
```

### Frontend

```bash
# Run frontend tests
npm test

# Watch mode (auto-rerun on file changes)
npm test -- --watch

# Generate coverage report
npm test -- --coverage
```

### Integration

```bash
# Start dev server
python run.py

# Run E2E tests (Playwright or Cypress)
# TODO: Add E2E tests
```

## Rollback Plan

If issues arise, rollback is straightforward:

1. **Config changes:** Old code can still call `get_config()` (backward compatible)
2. **Async LLM:** Keep old `run_llm_sync` alongside new `async_client.py`; routes can conditionally use either
3. **Use-cases:** Old routes can still call pipeline directly
4. **Frontend:** Keep old `state.js` alive; new code uses `app-state.js`

## Notes for Existing Code

- The new modules are **non-breaking** — old code continues to work
- Run old tests with `pytest` — all should pass unchanged
- Gradually migrate routes to use new container/use-cases
- Gradually migrate frontend scripts to ES modules

## Next Steps

1. **Start with Phase 1** (container + config + middleware) — this is the foundation
2. **Test thoroughly** after each phase
3. **Migrate data** once A9 is integrated
4. **Add observability** (A8) when you need better debugging
5. **Modernize frontend** (A5) on your own pace

---

For questions or issues, refer back to the comprehensive architecture review in `docs/COMPREHENSIVE_ARCHITECTURE_REVIEW.md`.
