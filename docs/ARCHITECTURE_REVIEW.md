# Architecture review & uplift — Draw.io AI Architect

Assessment of the current application architecture, its strengths, the highest-
value findings, and a phased uplift plan. Complements `docs/PROMPT_BATTLE_TEST.md`
(prompt-layer audit) and `docs/UX_REVIEW.md` (frontend/product).

> **Status (2026-08-13):** partly superseded. This plan was implemented in
> `bf163dc` as code that nothing imported and that did not run; it was reverted
> in `a6aac95`. Config consolidation and observability landed in a different
> shape, SQLite was hardened rather than rewritten, and the concurrency work
> turned out to be a leaking cap rather than a transport problem. See the
> outcome table at the top of `docs/COMPREHENSIVE_ARCHITECTURE_REVIEW.md` for
> what is done, what was rejected and why, and what is still open. Verify any
> code sketch here against the real signatures before acting on it.

## 1. What this application is, structurally

A single-process **FastAPI + draw.io iframe** monolith, unusually well-dialed-in
for its scope:

- **Hexagonal core** — `app/domain/ir` (Pydantic IR models + `semantic_errors()` /
  `semantic_warnings()` invariants), `app/core/services` (orchestration:
  `agent.py`, `xml_pipeline.py`, `intent.py`), `app/core/ports` (`LlmPort`
  protocol + `get_llm()`).
- **Adapters** — inbound FastAPI web (`routes.py`, `admin_routes.py`, `schemas.py`,
  `rate_limit.py`), outbound LLM (provider registry via `config/providers.json`,
  LangChain client wrapper, fallback chain, retry/backoff), outbound diagram
  (IR compiler, ELK bridge, `geometry_spec.py`, diagnostics/repair, draw.io MCP
  client), stores (history, templates, captures, governance, admin settings).
- **Deterministic diagram compiler** owns geometry (`geometry_spec.py`), shared by
  the diagnostics engine — "compiler emits what diagnostics consider clean" is a
  real guarantee, not a hope.
- **Frontend** is a vanilla-JS SPA (~30 files) talking to `embed.diagrams.net` via
  `postMessage`, no build step, settings persisted to `localStorage`.

Startup guards (default provider/key detection), security posture (rate limiting
inside the API-key gate, `X-Request-ID`, request-timeout wrapper), and 12-factor
env-driven config are all in place.

## 2. Strengths to preserve

| Area | Why it is strong |
|---|---|
| IR + compiler + self-validation | The line between "LLM judgement" and "deterministic guarantee" is the best part of the system: `semantic_errors()` feeds the corrective loop (`agent.py:476-647`), and there is no silent direct-XML fallback on a failed IR. |
| `CACHE_BREAKPOINT` | Stable prompt prefix is cached; volatile suffix (model id, hints, context) is excluded — correct LLM-prompt caching hygiene (`prompts/system.py:23`, `ir.py:95`). |
| Failure discipline | Provider fail-over via `PROVIDER_FALLBACK_ORDER`; transient retries keyed on explicit HTTP status (`llm_client.py:28-72`); attachments never raise (`documents.py`). |
| Single source of truth | `geometry_spec.py`, `providers.json`, `config/xml_gen/**` packs, SQLite `style_cache` — few duplicated constants. |
| Surgical edit path | `DiagramEditPlan` for small edits avoids a full IR re-generation for tiny changes. |

## 3. Findings by priority

### A1 — Thread-per-request pipeline masquerading as async (top scalability risk)
`routes.py` runs `run_xml_pipeline` through `asyncio.to_thread` / `run_llm_sync`;
long LLM calls occupy worker threads, and `_with_request_timeout` cancels the
*awaiting coroutine* but cannot kill the underlying thread (`routes.py:57-73` —
documented in the code itself). Under ~20 concurrent users the thread pool is the
bottleneck long before CPU. Every request is 5–20 sequential LLM calls, so this
amplifies quickly. **Fix direction:** native async LLM transport (or a queue +
worker model) instead of thread-per-request; structured job orchestration.

### A2 — No durable project/workspace model; "session" is browser-local
History, captures, templates, and prefs live in `localStorage` on the client and
flat, independent SQLite stores on the server. There is no concept of a *project*
(a person's architecture = multiple diagrams, versions, linked docs, notes as one
unit). Single-user-local works; multi-user, collaboration, and rich history all
need an object model the app does not have yet. This is the biggest
*product-architecture* gap relative to the system's ambition (docs, governance,
cost, compliance as per-run features).

### A3 — Middleware ordering is hand-rolled and easy to break
`main.py:93-104` stacks CORS → BodySizeLimit → RateLimit → ApiKey on top of a
manual request-id/timing `@application.middleware("http")`. Two custom
`BaseHTTPMiddleware` classes with per-path logic plus the manual one is fragile
and forces every future concern to reason about relative ordering. Consolidate
request-id/timing into one pure ASGI middleware; keep CORS/auth standard.

### A4 — `routes.py` (769 lines) imports pipeline internals and songs directly into adapters
`routes.py:47` imports `_summarize_llm_usage` (private) and the XML pipeline's
guts; handlers reach straight into stores/adapters. Inbound web should only call
core *use-case* functions. The `_mermaid_response` / `_sse` / `_with_request_timeout`
helpers are `xml_pipeline` concerns living in the web adapter.

### A5 — Frontend global-state + 30 unscoped `<script>` tags
`state.js` holds ~30 mutable globals; every script shares namespaces
(`currentXml`, `pendingXmlExport`, `messageHandler`); no modules, bindings,
imports, or tests. It works, but any single change has cross-file blast radius,
and this is where the highest ongoing maintenance cost sits.

### A6 — Config read both at import time and mid-flight
`core/config.py` snapshots `MAX_CUSTOM_MESSAGE_LEN`, `LLM_TIMEOUT_SECONDS`, etc.
at import, while `get_config()` reads `.env` + admin-store overrides per request
(`env_config.py`). Two sources of truth; `apply_all_to_environ` is side-effect-
heavy. Acceptable for one process; a real liability for horizontal scale and test
hermeticity.

### A7 — Two app roots, ad-hoc provider bootstrap
`app/main.py` and `app/mcp_server.py` both wire providers/env; bootstrap logic
lives partly in module init. A single `container.py`/factory that builds all roots
(FastAPI app, MCP server, admin app) from one config snapshot removes the
duplication.

### A8 — No observability beyond request logs
`StepLogger` gives excellent structured per-request steps (incl. token usage) but
it is in-memory and lost unless "captures" save it. No metrics, no tracing, no
span correlation across the 5–20 LLM calls in one generation. This is where
production debugging will hurt first.

### A9 — Five independent, uncoordinated SQLite stores
`history_store`, `template_store`, `capture_store`, `governance_store`,
`admin_settings_store` each own their DB file/lifecycle. Tolerable today; before
any MVP release, unify behind one repository module with a migration path
(ALembic or a small versioned schema).

### A10 — No frontend or end-to-end tests
~90 pytest files thoroughly cover the backend (compilers, IR, prompts, stores).
The SPA has zero automated coverage; the `postMessage`/draw.io boundary, voice,
and edit-save flows will regress silently.

## 4. Uplift plan

### Phase 1 — cheap, high-return, no rewrites
1. **Async LLM path** — concurrency manager (per-key semaphore + worker queue) and
   a native async transport in `llm_client.py` / `concurrency.py`. Biggest
   headline win for concurrency.
2. **Single data layer** — merge the five SQLite stores behind one repository
   module + migration path (ALembic or a tiny version table).
3. **Middleware consolidation** — one ASGI middleware module; pure request-id/timing;
   standard auth.
4. **Decouple `routes.py` from pipeline internals** — expose core use-case
   functions; move SSE/response mapping into the use-case layer's own presenter.
5. **Frontend foundation** — ES modules + a single app state object; decompose
   the top-level sources in `state.js`/`app.js` behind `type="module"`.

### Phase 2 — structure
6. **Workspace/project domain model** — `Project(id, owner, diagrams[], versions,
   linked docs, notes)` as backend source of truth; client stores a `project_id`.
   Enables multi-user later.
7. **Job/stream abstraction** — a persisted `Job(id, status, steps, outputs)`;
   `/chat/stream` and `/generate` become views over one job runner. Enables
   replay, resume, and background execution.
8. **Container/wiring layer** — `create_container()` builds all roots; kill
   import-time config snapshots in favor of lazy per-request resolution.

### Phase 3 — scale/ops
9. **LLM observability** — spans per call (token/cost/latency), Prometheus metrics,
   structured log export.
10. **Horizontal readiness** — decide where mutable state (style cache, session
    store) lives at scale (Redis or documented single-writer SQLite); deliberately
    choose the async-worker story (in-process pool → separate worker process).

## Evidence index

| Claim | File refs |
|---|---|
| A1 thread-per-request + uncancellable thread | `routes.py:57-73`, `adapters/outbound/llm/concurrency.py`, `xml_pipeline.py` run via `run_llm_sync` |
| A2 browser-local session / flat stores | `static/js/state.js:1-60`, `adapters/outbound/{history,template,capture,governance,admin_settings}_store.py` |
| A3 middleware stack | `main.py:88-137` |
| A4 routes imports pipeline private helpers | `routes.py:47` and handler bodies |
| A5 global state + tag soup | `static/js/state.js`, `static/index.html:766-791` |
| A6 dual config timing | `core/config.py` vs `adapters/outbound/config/env_config.py` |
| A7 two roots | `app/main.py`, `app/mcp_server.py` |
| A8 request-local logs | `core/logging.py` StepLogger |
| A9 many SQLite stores | `adapters/outbound/*_store.py` |
| A10 frontend coverage gap | `tests/` (backend only), `static/` (none) |