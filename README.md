# Draw.io AI Architect

Describe a system in plain English; get back a **live, editable draw.io diagram** — not a
picture of one. Cloud architecture across AWS / GCP / Azure / multi-cloud, plus flowcharts,
sequence diagrams, org charts, state machines and data charts.

Every diagram lands in an embedded draw.io canvas you can drag, edit, extend and export as
`.drawio` / PNG / XML. Cloud requests additionally fan out into **multiple viewpoint pages**
(Network, Security, Logical, Data flow, ArchiMate) and optional cost, Well-Architected and
compliance insights.

FastAPI backend (`app/`) + vanilla HTML/JS frontend (`static/`) embedding a draw.io iframe.

**[What it's for](#what-its-for)** · **[The application](#the-application)** ·
**[Example output](#example-output)** · **[Run locally](#run-locally)** ·
**[What makes it different](#what-makes-it-different)** ·
**[Architecture](#architecture-overview)** · **[Runtime](#runtime-architecture)** ·
**[API](#api)** · **[Known gaps](#known-gaps)**

---

## What it's for

| You need to… | Ask for | You get |
| --- | --- | --- |
| Design or document cloud infrastructure | *"Highly available web app: ALB, app servers in private subnets, multi-AZ RDS"* | Multi-AZ VPC with official cloud icons, numbered request path, plus Security / Logical / ArchiMate pages |
| Explain a system to a non-technical audience | *"…and show the business impact"* | An overview panel translating the topology into availability/risk language |
| Estimate before you build | any cloud prompt | Monthly cost breakdown, Well-Architected findings, compliance mapping |
| Document a process end-to-end | *"FNOL claims process, end to end, in detail"* | Sequence diagram with phase bands, activation bars and derived call/return pairing |
| Map a decision flow | *"draw the claim approval flow"* | Flowchart with decision branches, retry loops and datastores |
| Put numbers in front of people | *"claims cost by line of business"* | Bar / line / pie / gantt chart with a validated palette |
| Turn a whiteboard photo into an editable file | upload an image | A traced draw.io diagram that reproduces the layout you photographed |
| Modify what's on screen | *"rename checkout to orders"*, *"add a Redis cache"* | A surgical patch — the rest of the canvas is left exactly as it was |

Everything is driven from one chat box. Attachments (PDF / image / text) become context for
cloud designs; follow-up turns edit the diagram already on the canvas.

---

## The application

Chat on the left, a live draw.io editor on the right. Pick a cloud platform, start from an
architecture pattern or just describe what you want, and choose which model answers.

![Application start screen with architecture patterns](docs/images/app-start.png)

After a generation the diagram is in a real draw.io canvas — pan, zoom, drag boxes, edit
labels. Viewpoint pages appear as tabs along the bottom (**Network · Security · ArchiMate**),
the version chip (`v1`) tracks history, and the diagram exports as `.drawio`, PNG or raw XML.
Follow-up messages edit what's already on the canvas.

![Application with a generated multi-page cloud diagram](docs/images/app-generated.png)

Every successful generation is snapshotted. Past sessions reopen with their full conversation
and diagram, and each session keeps its own version list.

![Sessions dialog listing past diagram sessions](docs/images/app-sessions.png)

---

## Example output

### Cloud architecture — multi-view, real icons

> *"Highly available web app with load balancer, app servers in private subnets, multi-AZ database"*

![Network view of a highly available AWS web application](docs/images/example-cloud-network.png)

Official AWS stencils resolved through the draw.io MCP server, a numbered primary request
path, trust-coloured subnet tiers, and an overview panel carrying the business impact and key
facts. The same request produces a **Security & trust** page from the same model:

![Security and trust view of the same application](docs/images/example-cloud-security.png)

Trust zones ramp by sensitivity (public → DMZ → private → restricted), edge colour encodes
data classification, and the legend explains both — so a CISO and a network engineer can read
the same generation from their own viewpoint.

### Sequence diagram — process across many systems

> *"Insurance claims process, FNOL to settlement, end to end"*

![Insurance claims sequence diagram with phase bands](docs/images/example-sequence.png)

Participant cards coloured by role (people vs systems), the emoji in each label lifted out as
its icon, phase bands with title tabs grouping the conversation into stages, and activation
bars **derived** from sync/reply pairing rather than declared. The vertical axis is time, so
nothing is allowed to reflow it.

### Flowchart

> *"draw the claim approval flow"*

![Claim approval flowchart](docs/images/example-flowchart.png)

Shapes carry meaning (decision, datastore, terminal, person) and colour is assigned by role
from one shared palette, so a flowchart, a sequence diagram and a chart from this app look
like one product.

### Charts

> *"claims cost by line of business, 2025 vs 2026"*

![Bar chart of claims cost by line of business](docs/images/example-chart.png)

Bar / line / pie / gantt, plotted rather than laid out — direct value labels, a legend row,
and a categorical palette validated for colour-vision deficiency and contrast.

---

## Run locally

Two runtimes are required: **Python 3.11+** for the app and **Node 20+** for ELK layout. A
Python-only environment installs fine and then fails at request time on every corridor and
network view.

1. `cp .env.example .env`, then fill in at least one LLM API key (`ANTHROPIC_API_KEY`
   recommended for IR quality; `OPENAI_API_KEY` / `GOOGLE_API_KEY` also work). `.env` is
   gitignored — keep it that way. Every key is documented in
   **[docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md)**.
2. Install and start:

```powershell
pip install -r requirements.txt      # or requirements.lock for exact pins
python run.py
```

Open `http://localhost:8000/`. Use **Render** for `Auto (MCP)` / `MCP` / `Local`.

> Without an MCP server reachable, cloud diagrams still generate — leaves render as neutral
> placeholder boxes instead of official vendor icons. Structure, layout and every other view
> are unaffected.

### Working on it

```bash
pip install -r requirements-dev.txt

pytest                              # full suite (~1,277 tests, 84 files)
pytest tests/test_ir_pipeline.py -q # focused
ruff check .                        # lint
lint-imports                        # architecture boundaries
pytest --cov                        # suite + coverage floor
```

All four run in CI on every push and pull request
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), which also builds the container and
boots it against `/health`.

### Dependencies

| File | Role |
| --- | --- |
| `requirements.txt` | What the app **declares** — open ranges |
| `requirements.lock` | What actually installs — exact pins, cross-platform |
| `requirements-dev.txt` / `.lock` | Test and lint tooling; never in the image |

The locks are generated with `uv pip compile --universal` so a file produced on Windows
installs on the Linux runner, and resolved against a constraints file from a verified
environment so they pin versions the suite has actually passed against. Regeneration commands
are in each lock's header.

### Container

```bash
docker build -t drawio-agent .
docker run -p 8000:8000 --env-file .env -v "$PWD/data:/app/data" drawio-agent
```

Serves uvicorn directly rather than `run.py` (which enables the reloader), runs as a non-root
user, and uses **one worker deliberately** — the LLM concurrency pool, the in-memory style
cache and the rate-limit counters are all per-process, so a second worker would silently
double the effective `MAX_CONCURRENT_LLM`. Scale with replicas, after moving the stores off
SQLite.

### Deploying (Render)

[`render.yaml`](render.yaml) is a Render Blueprint that builds and runs the same `Dockerfile`
— connect the repo as a Blueprint in the Render dashboard, then set the `sync: false` secrets
it lists (an LLM key, `CORS_ORIGINS`, `API_KEY`, …) in the service's Environment tab.

Every store here degrades to a no-op rather than crashing when it can't write, so the free
plan's ephemeral disk is fine to run on — history, templates and the icon-style cache just
reset on restart instead of the app failing.

`autoDeploy` is off. The only deploy trigger is the `deploy` job in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml), which runs after `test` and `docker`
both pass on a push to `main` — Render's default is to deploy the instant it sees a push,
racing ahead of CI regardless of whether it's green. Wire it up once: Render service →
Settings → Deploy Hook → Create Deploy Hook → copy the URL → GitHub repo → Settings → Secrets
and variables → Actions → New repository secret → `RENDER_DEPLOY_HOOK_URL`. Until that secret
exists the step logs a warning and exits cleanly rather than failing CI.

Setting `API_KEY` gates every route except `/`, `/health` and `/static/*` —
`static/js/api-key.js` is what lets the browser itself satisfy that gate (prompts once, stores
the key in `localStorage`, retries transparently).

---

## What makes it different

**The LLM never writes coordinates.** It emits a validated intermediate representation (IR) —
structure only: what nests inside what, what talks to what, which service each leaf is. A
deterministic compiler owns every pixel. This is the central design decision and most of the
advantages below fall out of it:

| | Why it matters |
| --- | --- |
| **Layout is reproducible** | The same IR always compiles to the same geometry. Spacing lives in one file (`geometry_spec.py`) shared by the compiler *and* the diagnostics that check it. |
| **Failures are loud, not crooked** | A structurally invalid diagram fails `semantic_errors()` and gets one corrective pass. It cannot reach the canvas as overlapping boxes and dangling arrows. |
| **Icons are real** | Leaf stencils are resolved from the draw.io shape library via MCP, not invented by the model — so an S3 bucket looks like an S3 bucket. |
| **One system, many viewpoints** | Network, Security, Logical, Data-flow and ArchiMate pages are generated from the same design, so they describe the *same* architecture rather than five unrelated drawings. |
| **Edits are surgical** | A rename patches one cell and keeps `layout="ir"`, so band alignment survives. Only genuinely large changes regenerate. |
| **The output is a file, not an image** | Everything is mxGraph XML in an editable canvas. Nothing is a screenshot you have to redraw by hand to change. |
| **Nothing is silently truncated** | Diagram size is a note in the log, never a cap. Ask for fifty services and you get fifty. |

---

## Architecture overview

Hexagonal / ports-and-adapters layout:

```text
app/
  domain/
    entities/          # XmlDiagram, DiagramEditPlan — no I/O
    ir/                # NetworkIR + CorridorIR (Logical/Security/Data flow),
                       # GenericDiagramIR (image tracing), SimpleDiagramIR (non-cloud)
  core/
    config.py          # one-time .env load + static paths (no snapshots)
    logging.py         # StepLogger: step entries, timings, rotating files
    ports/             # LlmPort — the core↔adapter boundary
    services/          # agent.py, xml_pipeline.py, intent.py
  adapters/
    inbound/web/       # FastAPI routes, schemas, rate limits
    outbound/
      config/          # env_config.py — the single config source
      llm/             # providers.json registry, LangChain clients, fallback
        concurrency.py # bounded private thread pool for provider calls
      diagram/         # compile, repair, MCP icons, edge_routing
        compiler/      # IR → draw.io XML (geometry + styles)
          chart.py     # plots bar/line/pie/gantt
          sequence.py  # lifelines, phase bands, derived activations
          simple.py    # dispatches the non-cloud kinds
          palette.py   # the one categorical palette every non-cloud view draws from
        geometry_spec  # single source of spacing constants
        edge_routing   # exit/entry anchors + obstacle routes (not layout)
        elk_bridge.py  # ELK layout: long-lived sidecar + per-call fallback
      sqlite_util.py   # tuned connections (WAL + busy timeout) for every store
      utils/           # documents, web, chat
      cost / WAF / compliance / history_store / capture_store
static/                # chat UI + draw.io iframe
tools/elk_layout/      # layout.js (one-shot) + serve.js (sidecar), vendored elkjs
config/
  xml_gen/ir/          # IR prompt packs (judgment, not pixels)
  references/          # shape_type catalogs — IR-native, no stencil syntax
  providers.json       # LLM provider registry
```

### Ownership split

| Piece | Owns | Does **not** own |
| --- | --- | --- |
| **LLM** | IR structure: nesting, flows, leaf `shape_type` keys | Pixel geometry, official icon stencils |
| **IR types** (`domain/ir/`) | Structural invariants (`semantic_errors`, nesting) | Rendering |
| **Compiler** | Geometry, styles, parent wiring, ELK corridor layout | Icon stencils |
| **Prompt packs** (`config/xml_gen/ir/`) | Classification / viewpoint judgment | Coordinates |
| **Draw.io MCP** | `search_shapes` → official leaf icons; optional libavoid | Nesting meaning |
| **`edge_routing`** | Edge exit/entry + local orthogonal detours | Moving boxes |
| **UI** | Chat, iframe, history restore, render mode | Generation logic |

### Three IR families

| IR | Used for | Compiler |
| --- | --- | --- |
| `NetworkIR` / `CorridorIR` | Cloud architecture — the multi-view path | `compiler/network.py`, `compiler/corridor.py` (ELK) |
| `SimpleDiagramIR` | Non-cloud: `flow`, `sequence`, `bar`, `line`, `pie`, `gantt` | `compiler/simple.py` → generic / sequence / chart |
| `GenericDiagramIR` | Tracing an uploaded image as-is | `compiler/generic.py` |

`SimpleDiagramIR` is one discriminated schema so the non-cloud path stays a **single** LLM
call — the model picks the representation that fits the request without a separate
classification round-trip.

---

## Runtime architecture

The pieces below are invisible in a single request and decide how the app behaves under load,
on failure and on shutdown. Each carries a measurement or a failure mode that motivated it.

### ELK layout runs on a long-lived sidecar

Layout is computed by `elkjs` under Node. Starting Node and loading elkjs costs **~300–400 ms
regardless of graph size**, while the layout itself is 31 ms (6 nodes) to 101 ms (120 nodes) —
so spawning per call spent several times more starting the engine than using it, once per
view.

`tools/elk_layout/serve.js` now stays up and answers newline-delimited JSON requests in FIFO
order. Measured across 5 views of 40 nodes:

| strategy | wall clock |
| --- | --- |
| per-call cold spawns (concurrent) | 932 ms |
| **one sidecar, serialized** | **233 ms** |
| sidecar pool, 5 processes | 170 ms |

Serialized still wins fourfold, because concurrent process starts contend worse than linearly
— so this is one process, not a pool. A single-view request goes 385 ms → 46 ms. Replayed
against the 32 ELK graphs the compiler actually builds during the test suite: **12.4× faster,
byte-identical geometry on 32 of 32**.

Any failure — death, wedge, or a response-id mismatch — falls back to the per-call `layout.js`
spawn, so the fast path degrades to the one that always worked rather than failing a request.
A timeout or id mismatch *kills* the process instead of reusing it: the pipe's framing is no
longer trustworthy, and returning a late reply as the next request's response would be silent
layout corruption. `ELK_SIDECAR_ENABLED=false` forces the old path.

### LLM calls run on a bounded private pool

`MAX_CONCURRENT_LLM` is enforced by a dedicated `ThreadPoolExecutor`, never by a semaphore
around `asyncio.to_thread`. A semaphore guards the *coroutine*, not the thread — so when a
request hits `REQUEST_TIMEOUT_SECONDS` the coroutine is cancelled, the permit is released, and
the provider call keeps running uncounted. Measured at a cap of 2, four calls ran
simultaneously.

A pool worker stays occupied until the call genuinely finishes, so the cap holds under
cancellation; excess work queues as backpressure; and LLM calls never touch the default
executor that the ~40 store and document `to_thread` calls share. Cancelling before the work
is dequeued withdraws it outright, so a request that times out while queued never reaches the
provider.

The pipeline stays thread-offloaded rather than natively async on purpose: the functions
behind `run_llm_sync` interleave provider calls with CPU-bound IR compilation, ELK layout and
XML rewriting. Making them `async def` and awaiting only the LLM would move that CPU work onto
the event loop.

### SQLite stores are WAL, with a busy timeout

Every store is reached from threadpool workers, so two requests genuinely touch the same file
at once. `sqlite_util.connect()` applies WAL (readers never block on the writer), a 5 s busy
timeout (a contended writer waits instead of raising `database is locked`),
`synchronous=NORMAL` and `foreign_keys=ON`, degrading to defaults if the filesystem refuses.
Use it instead of `sqlite3.connect`.

### Config has exactly one source

`env_config.EnvConfigAdapter`, read via `get_config().<setting>` at the point of use. Never
capture a setting as an import-time constant: a snapshot taken before `init_config()` cannot
be refreshed in modules that already imported it. `core/config.py` owns only the one-time
`.env` + admin-override load and the static `BASE_DIR`/`STATIC_DIR` paths.

### Observability

`StepLogger` entries carry timestamps *and* durations. `logger.span(...)` (usable as `with` or
`async with`) records `duration_ms` per stage and accumulates a per-step total;
`_build_response` emits the breakdown as a final `step="timings"` entry, so every response
carries a stage profile through the existing `logs`/SSE/file-log paths. Every provider call is
timed inside `call_llm`, covering the corrective re-ask and each backoff retry rather than just
the first try.

### Shutdown

A FastAPI `lifespan` drains the LLM pool (letting in-flight calls finish) and then stops the
ELK sidecar — in that order, since layouts run on pool threads. It also warns once at startup
when the app is bound off-loopback with development defaults (CORS `*`, no API key, no rate
limit, a stock admin password).

---

## Quality gates

| Gate | Enforces |
| --- | --- |
| `ruff check .` | A narrow, high-signal ruleset — see the rationale in `pyproject.toml` |
| `lint-imports` | `domain` imports nothing outward; `core → adapters` is frozen at its current set |
| `pytest --cov` | ~1,277 tests plus a coverage floor |
| CI Docker job | The image builds and the container answers `/health` |

Two of these are ratchets rather than aspirations, and both exist because of a specific
failure. The `core → adapters` allowlist freezes 24 pre-existing violations so a **new** one
fails review — it is meant to shrink, never grow. The coverage floor sits just under current
coverage so it fails on regression; it is the one check that would have caught a merged commit
of ~6,000 lines that could not run, since lint and tests never execute unimported code.

The prompt packs get the same treatment: `tests/test_prompt_assembly.py` asserts that no IR
prompt ever *presents* draw.io XML syntax (`resIcon=`, `mxgraph.`, `parent="1"`) while still
allowing a pack to *forbid* it, and budget-checks all nine IR assemblies. Prompt packs are
program logic with no other safety net.

---

## End-to-end request flow

```mermaid
flowchart TD
  UI[Frontend chat + draw.io iframe] -->|POST /chat or /chat/stream| API[FastAPI routes]
  API --> Intent{intent.classify_intent}

  Intent -->|non-cloud, no attachments| Simple[run_simple_pipeline]
  Intent -->|cloud or attachments present| Pipe[xml_pipeline.run_xml_pipeline]

  Simple --> SimpleIR[LLM -> SimpleDiagramIR]
  SimpleIR --> SimpleComp[flow / sequence / chart compiler]

  Pipe --> Gap[Optional gap analysis]
  Gap --> Primary{Primary generation}

  Primary -->|fresh cloud| IR[LLM -> Pydantic IR]
  Primary -->|small edit + current_xml| Surg[DiagramEditPlan surgical ops]
  Primary -->|large redesign| IRregen[IR regenerate seeded from canvas inventory]
  Primary -->|uploaded image| GenIR[GenericDiagramIR trace]
  Primary -->|3D mode| XML3D[Direct XML via prompt_3d.txt]

  IR --> Comp[Deterministic compiler]
  IRregen --> Comp
  GenIR --> Comp
  Comp --> Stamp["Stamp layout=ir on layer"]
  Surg --> Direct[Direct XML patch]
  XML3D --> Direct

  Stamp --> Repair[repair_xml: structural + edge_routing]
  Direct --> Repair
  SimpleComp --> Repair

  Repair --> Fanout[Parallel fan-out]
  Fanout --> Views[Extra IR views Logical / Security / Data flow]
  Fanout --> MCP[MCP icon resolve + optional libavoid]
  Fanout --> Enrich[Cost / WAF / Compliance]
  Fanout --> Docs[ADR / RAID pages]

  Views --> Merge[Merge mxfile pages]
  MCP --> Merge
  Merge --> Hist[Optional history snapshot]
  Hist --> Resp[JSON: xml, generation_path, logs, insights]
  Resp --> UI
```

**Every path returns draw.io XML.** The non-cloud path used to return Mermaid source for the
frontend to hand the embed as `{action: "import", format: "mermaid"}` — draw.io has no
`import` action, so it replied `{error: "unknownMessage"}` and drew nothing, for every simple
diagram this app ever produced. The embed accepts mxGraph XML and nothing else.

Orchestrator: [`app/core/services/xml_pipeline.py`](app/core/services/xml_pipeline.py)
IR prompts: [`config/xml_gen/ir/`](config/xml_gen/ir/)
Env / MCP detail: [`docs/ENV_AND_MCP.md`](docs/ENV_AND_MCP.md)

### Response fields that matter

| Field | Meaning |
| --- | --- |
| `generation_path` | `ir` \| `simple_ir` \| `generic_ir` (compiled from a validated IR) or `direct_xml` (surgical edit or 3D) |
| `renderer_used` | `mcp` or `local` — icon resolution only, not how structure was built |
| `degraded_features` | Soft-failed branches (view quality, quota, missing secondary tab) |
| `llm_usage` | Token/cost to *generate* the diagram (distinct from depicted cloud spend) |
| `logs` | Structured step log (also streamed as SSE on `/chat/stream`) |

---

## Diagram lifecycle (create → update → restore)

```mermaid
stateDiagram-v2
  [*] --> Empty: open app
  Empty --> FreshIR: "describe architecture"
  FreshIR --> MultiView: compile primary + fan-out views
  MultiView --> OnCanvas: draw.io iframe shows pages

  OnCanvas --> Surgical: "rename X / add Y / delete Z"
  Surgical --> OnCanvas: patch XML, regen secondary views

  OnCanvas --> Redesign: "redesign" or NonSurgicalEditError
  Redesign --> FreshIR: IR regen with current inventory

  OnCanvas --> History: session version chips
  History --> OnCanvas: restore prior XML

  OnCanvas --> Export: download .drawio / PNG / copy XML
```

**1. Fresh generation (IR path — default)**
Prompt → optional gap analysis → LLM emits a view-specific IR → Pydantic + `semantic_errors()`
validate → compiler lays out XML (`geometry_spec` spacing; ELK for corridor views) → stamp
`layout="ir"` → thin `repair_xml` (structure + `edge_routing`, **no** leaf resize / pitch
nudges) → MCP merges official icons → parallel extra views / ADR / RAID / cost / WAF.

**2. Surgical edit (keep most of the canvas)**
When `current_xml` is present and the ask looks small, the agent returns a `DiagramEditPlan`
(add/delete/move/connect/relabel). Relabel keeps `layout="ir"` so band alignment survives;
geometry-changing ops clear it so fit logic can run. Extra IR view tabs regenerate so they stay
aligned; ADR/RAID are skipped to save cost.

**3. Non-surgical redesign**
If the plan is "too big" (`NonSurgicalEditError`), the pipeline regenerates via IR, seeded with
an inventory of what's already on the canvas plus the same enriched context the primary page
saw — so Logical/Security/Data flow describe the *same* system.

**4. Non-cloud follow-ups always regenerate**
A chart's geometry is *derived* from its data (a bar's height is its value), so patching cells
would relabel a bar without re-plotting it. Simple diagrams re-emit the whole IR, with the
current label inventory fed back in so anything you didn't ask to change survives.

**5. Multi-view quality**
Secondary views score against viewpoint rules (`VIEW_QUALITY_MIN_SCORE`). Below the gate: one
IR corrective retry, then keep the best attempt and mark `degraded_features` rather than
failing the whole request.

**6. History / restore**
Successful responses can be snapshotted (`DIAGRAM_HISTORY_*`). UI version chips call history
APIs to restore prior XML without regenerating.

---

## Generation paths at a glance

| Mode | When | Path | `generation_path` |
| --- | --- | --- | --- |
| Fresh cloud | Default multi-view | IR → compiler | `ir` |
| Non-cloud | Flowchart, sequence, chart, org chart, ER, mindmap | `SimpleDiagramIR` → compiler | `simple_ir` |
| Image trace | An image was uploaded in as-is mode | `GenericDiagramIR` → compiler | `generic_ir` |
| Surgical edit | Small change + `current_xml` | Direct XML ops | `direct_xml` |
| Redesign | Large edit / NonSurgical | IR regen | `ir` |
| 3D / isometric | `three_d=true` | Direct XML (`prompt_3d.txt`) | `direct_xml` |

`DIAGRAM_IR_ENABLED` / `DIAGRAM_IR_VIEWS` gate which views use IR. Fresh IR failure fails the
request — there is no silent direct-XML fallback.

---

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /chat` | Multi-turn diagram generation |
| `POST /chat/stream` | Same with SSE progress events |
| `POST /generate` | Single-turn generate |
| `POST /download` | Return `.drawio` XML attachment |
| `POST /parse-url` | Fetch URL text for context |
| `GET /health` | Health check |
| `GET /captures` | List saved request/response captures |
| `GET /history/sessions` | Diagram version sessions |
| `GET /history/versions/{id}` | Restore one version (full XML) |

---

## UI notes

- Toast notifications for errors / degraded features
- Insights tabs: Cost | WAF | Compliance | Logs
- Export: Download `.drawio`, PNG, Copy XML
- Session version chips restore prior successful XML
- Attachments: PDF / images (vision) and text/markdown/JSON/YAML (inlined context)
- Render select: Auto (MCP) / MCP / Local

---

## Known gaps

Honest list of what is weak, in the order I'd fix it. Several are deliberate scope decisions
rather than bugs — those are marked.

### Diagram quality

- **Groups and swimlanes are unstyled.** A `SimpleGroup` renders as a plain grey dashed box —
  no palette, no title bar — while every other element in the same diagram is themed. A
  phased flowchart looks unfinished because of it.
- **Self-message labels collide with their node.** Visible on any flowchart retry loop; the
  label box sits beside the loop but is not offset far enough.
- **Sequence diagrams cannot express `alt` / `opt` branches.** A conditional block gets
  flattened into consecutive messages, so "if fraud score is high" and "if it is low" read as
  though both happen. This is a schema addition, not a theme fix, and it is the single biggest
  gap in what a sequence diagram here can *say*.
- **Very large pages crowd their edge labels.** Nothing is capped any more (by design), but at
  ~24 leaves on a Logical page the labels of adjacent flows start overlapping. The layout is
  correct; the labelling needs collision avoidance.
- **Charts are capped at 6 series / 6 pie slices.** Deliberate: the categorical palette is
  assigned in fixed slot order and never cycles, so a 7th could only reuse a colour already in
  use. Raising it means adding validated hues first.
- **Heatmap, Venn, wireframe and similar are not supported.** Deliberate — the model says so
  in its reply rather than substituting a confident lookalike.

### Operational

- **Single worker only.** The LLM concurrency pool, style cache and rate-limit counters are
  per-process. Horizontal scaling needs the stores moved off SQLite first.
- **Icons depend on an MCP server.** In `local` mode leaves are neutral placeholders. Nothing
  breaks, but a cloud diagram loses much of its value.
- **Auth is a single shared `API_KEY`.** There is no per-user identity, tenancy or
  authorization model.
- **Voice input depends on the browser's speech service.** Chrome/Edge stream audio to a
  remote recogniser; a blocked endpoint or a Chromium build without Google API keys produces a
  `network` error every time, with no server-side fallback.

### Codebase

- **24 frozen `core → adapters` import violations.** `lint-imports` stops new ones; the
  existing list is meant to shrink and hasn't yet.
- **`flow_spine` synthesis is a heuristic.** When a model returns no flows at all, the request
  path is invented in code. It connects every leaf, but the ordering it implies is a guess.

---

## Related docs

| Doc | Contents |
| --- | --- |
| [docs/ENV_AND_MCP.md](docs/ENV_AND_MCP.md) | Env keys, MCP, IR flags, history, rate limits |
| [docs/CLOUD_DIAGRAM_SKILLS.md](docs/CLOUD_DIAGRAM_SKILLS.md) | Cursor skills for offline `.drawio` authoring |
| [CLAUDE.md](CLAUDE.md) | Contributor guidance for agents working in this repo |
| [docs/PROMPT_BATTLE_TEST.md](docs/PROMPT_BATTLE_TEST.md) | Prompt-surface audit — open findings and their remediation order |
| [docs/COMPREHENSIVE_ARCHITECTURE_REVIEW.md](docs/COMPREHENSIVE_ARCHITECTURE_REVIEW.md) | Architecture audit. **Read the outcome table at the top first** — parts of the roadmap below it were implemented and reverted |
| [config/providers.json](config/providers.json) | LLM provider registry |

Optional Cursor skills under [`.cursor/skills/`](.cursor/skills/) produce publish-quality
diagrams inside Cursor chat; they are separate from the FastAPI runtime MCP
(`DRAWIO_MCP_URL`).
