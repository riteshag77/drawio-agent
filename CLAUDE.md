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

# Frontend, browser-driven — CI's `frontend` job, and the only automated
# coverage static/js has. Skips (never fails) locally without a browser;
# install it with `playwright install chromium`. CI sets
# REQUIRE_BROWSER_TESTS=1, which turns that skip back into a failure, because
# a browser job that skips is a green check proving nothing.
pytest tests/test_voice_mode_browser.py tests/test_chat_flow_browser.py
```

One check cannot run in CI and has to be run deliberately:

```bash
# Layout/geometry regression net — replays every stored capture's IR through
# the current compiler. Record a baseline BEFORE starting layout work; doing
# it after bakes in whatever you just changed and the check can only pass.
python tools/replay_captures.py --update-baseline   # once, before
python tools/replay_captures.py --strict            # after
```

It is local-only because `data/` is gitignored in full: on a runner it would
find zero captures and exit 0 having verified nothing. `--strict` turns "no
corpus" and "no baseline" into failures so a wiped `data/` can't imitate a pass.

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
  domain/entities/        # plain dataclasses/pydantic models (XmlDiagram, DiagramEditPlan,
                          # GovernanceRule/Finding, VisualQaResult) — no I/O
  domain/ir/              # diagram IR — NetworkIR + shared CorridorIR (Logical/Security/Data flow),
                          # ArchimateIR, GenericDiagramIR (as-is image trace),
                          # SimpleDiagramIR (non-cloud), self-validating
    flow_spine.py         # synthesizes a request path when a model returns leaves but no flows;
                          # sets `flows_are_synthesized` — see "Correction ladder" below
  core/services/          # use cases: agent.py (LLM calls + the corrective passes),
                          # xml_pipeline.py (orchestration), intent.py,
                          # governance.py (deterministic org-rule evaluator)
  adapters/inbound/web/   # FastAPI routes.py + admin_routes.py + schemas.py + rate_limit.py
  adapters/outbound/      # everything that talks to the outside world:
    llm/                  # provider registry, langchain client wrapper, prompt builders,
                          # fallback.py, concurrency.py (bounded pool), caching.py, pricing.py
    diagram/              # draw.io XML validation/repair/MCP client
      compiler/           # IR → draw.io XML (deterministic; owns geometry + styles)
                          # chart.py plots bar/line/pie/gantt; sequence.py draws lifelines;
                          # simple.py dispatches the non-cloud kinds; archimate.py the ArchiMate page
      geometry_spec.py    # canonical spacing constants — shared by compiler AND diagnostics
      elk_bridge.py       # ELK layout: long-lived sidecar (tools/elk_layout/serve.js),
                          # falling back to a per-call layout.js spawn
      view_conformance.py # scores a compiled page against its viewpoint rules (cloud views)
      simple_conformance.py  # the same for the non-cloud kinds
      diagram_vlm_qa.py   # the always-on geometry linter (DIAGRAM_VLM_QA), distinct from the
                          # client-triggered screenshot QA on POST /diagram/validate-visual
    utils/                # documents.py (attachment extraction), web.py, chat.py
    cost_estimator.py, well_architected.py, waf_topology.py, compliance.py, saas_catalog.py
    history_store.py, capture_store.py, template_store.py, governance_store.py,
    admin_settings_store.py
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
3. Response includes `xml`, `renderer_used` (`mcp` or `local`), `generation_path`
   (`ir` | `direct_xml` | `generic_ir` | `simple_ir`), `llm_usage`, `logs`,
   `degraded_features` and `inferred_connections`.

The last two are the pipeline admitting what it did not deliver, and both are
load-bearing for the UI rather than diagnostics:

- `degraded_features` entries carry the **view key** of a page that failed, not
  the name of the branch that produced it. "Additional view generation" names
  internal machinery; the user needs to hear that the *Logical tab* is missing
  from the diagram in front of them. A missing page also outranks the run's own
  success toast — a run that came back short of a page did not simply succeed.
- `inferred_connections` names each page whose arrows `flow_spine` invented
  because the model returned components and no flows (measured at 34 of 383
  stored views). The IR always knew this (`flows_are_synthesized`) and the fact
  went no further than the layout code, so invented relationships were
  indistinguishable from described ones. Anything that hides it again is a
  regression, not a simplification.

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

### Correction ladder: errors, warnings, governance, the validation loop

Four distinct mechanisms check a generated diagram. They are not
interchangeable, and the cheapest one that can do the job is the right one.

1. **`semantic_errors()`** (IR types) — hard structural rules. Gets one
   corrective re-ask; if the IR still breaks them, the compiler rejects the
   view and the request fails. There is no direct-XML fallback.
2. **`semantic_warnings()`** (IR types) + **governance findings** — soft. Both
   feed the *same* single, best-effort `agent._apply_semantic_warning_pass`,
   which never blocks and never runs a second round-trip.
3. **Conformance scoring** — `view_conformance.score_view` for cloud pages,
   `simple_conformance.score_simple_ir` for the non-cloud kinds. Scores what the
   *model* produced (viewpoint vocabulary, density, labels, icons) against
   `VIEW_QUALITY_MIN_SCORE`.
4. **The validation loop** (`DIAGRAM_VALIDATION_*`, off by default) —
   regenerates a view while its score keeps measurably improving.

**Do not ask the model in prose for something the schema can require.** 82% of
views entered the warning pass solely because an optional narrative field was
blank — and the field was still blank afterwards. The fix was marking those
fields required on the IR model, which is where the asking actually binds.
`agent._NARRATIVE_GAP_MARKERS` now skips that call entirely. Structural
warnings ("no DLQ", "no cache companion") still get their ask, because they
name something the model can add, and it does. A correction pass may **add**,
never delete.

Density is logged and never corrected (`IRModel.density_notes`): "fixing" a
dense page means dropping components the request named.

**Governance rules** (`domain/entities/governance.py`, evaluated by
`core/services/governance.py`, stored in `governance_store.py`, authored in the
admin panel) are org house-style policy — "the ledger lives in the restricted
zone", "nothing in public talks straight to data". Every kind is a bounded
structural fact-check over the IR, never free-form prose and never an LLM
judgment call: the same IR must always produce the same finding, or a
conformance report is worth less than none. v1 is soft-only by design.
`_all_leaves` is the one place that knows all three IR shapes (Network nests,
corridor views keep a flat list, ArchiMate keeps `elements`).

**The validation loop** covers the primary page, the extra views and the simple
path — the primary was left out at first, which meant turning validation on
left the page the user is actually looking at untouched. Its knobs:

- `DIAGRAM_VALIDATION_ENABLED` — off by default; each iteration is a full view
  regeneration.
- `DIAGRAM_VALIDATION_MAX_ITERATIONS` — corrective passes on top of the first
  generation.
- `DIAGRAM_VALIDATION_MIN_IMPROVEMENT` — **0 means "any measurable
  improvement"**, and that is the point of the setting: iterate because the
  diagram is getting better, not because a budget allows it.
- `DIAGRAM_VALIDATION_PROVIDER` — judging and drawing are different jobs.
  Accepts `"<provider>::<model>"` and is passed through whole.

With validation off the loop reduces to exactly the previous behaviour: one
retry, keep the better attempt. Fresh IR only — a surgical edit or a 3D page is
never regenerated wholesale to chase a score, since that throws away the canvas
the user has been editing.

Edge crossings are a **tiebreak only** (`_pick_tidier`), deliberately not folded
into `score_view`. The two measure different things: the score judges what the
model produced, crossings judge what the compiler drew from it. Merging them
would ask the model to fix geometry it does not control — the same category
error as asking it in prose for a field the schema had marked optional.

### Admin panel and stores

`admin_routes.py` (mounted separately, password-gated via
`admin_settings_store.py`) owns provider credentials/defaults, the validation
settings above, and governance-rule CRUD. Admin overrides are loaded once by
`core/config.py` alongside `.env`.

Every store uses `sqlite_util.connect()` and **degrades to a no-op rather than
raising** when it cannot write, so an ephemeral disk resets history instead of
failing requests: `history_store` (session/version snapshots),
`capture_store` (request/response captures — the corpus
`tools/replay_captures.py` replays), `template_store`, `governance_store`,
`admin_settings_store`, `diagram/style_cache`.

### Architecture interview (`architecture_interview`)

When enabled and the brief is thin, `/chat` can return a question set instead of
a diagram (`config/xml_gen/interview.txt`). Rounds are counted from the answers
themselves (`_rounds` marker) rather than held server-side — the chat endpoint
is stateless per request, and a session store for a two-round form would be the
only stateful thing in the request path. Capped at two rounds: a user who has
answered twice has earned their diagram.

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

### Frontend (`static/js/`)

No module system: every file declares plain globals, and `index.html`'s ordered
`<script>` tags **are** the dependency graph. A new file needs a tag added
there and nothing else. `app.js` must stay **last** — it is the only file that
executes at load time (the boot sequence); every sibling only declares
functions. `index.html` also carries ~86 inline `onclick=` handlers that
resolve against `window`, which is why converting to ES modules would be a
large change rather than a mechanical one.

One chat turn spans four files, split by *why they change* rather than by size:
`chat-api.js` (transport: `/chat/stream` SSE + the plain-POST fallback),
`chat-request.js` (assembling the request), `chat-response.js` (applying a
result + the six insight panels), `chat-send.js` (`sendMessage()`, which should
stay readable as `begin -> build -> post -> apply -> end`). Anything that grows
a body of its own belongs in a sibling, not inline in `sendMessage`.
`provider-options.js` is shared with `admin.html`, which loads a different
script set — that is why it is its own file.

**Toasts queue; they do not overwrite.** There was one toast element, so
whichever message wrote last won — a warning raised mid-turn was erased by the
"Done" that followed it, every time, which is a fair explanation for how
sessions lost a tab without anyone noticing. `toast.js` now queues, floats
warnings ahead of routine news, suppresses repeats, and holds a missing-page
warning longer (12s vs 6.5s). Do not go back to a single element.

Exports (`exports.js`) are `.drawio`, PNG, **SVG** and copy-XML. The SVG is
exported with its XML embedded so the file stays editable — an SVG that is only
a picture defeats the point of the app.

### Voice mode

The mic button runs a conversational loop, not dictation: ~1.2s of silence ends
a turn (there is no "send"), the app speaks back, and the user can interrupt it
by talking. Dictation survives behind a settings toggle and is the only thing
that works for mixing typing and speech.

- `voice.js` owns the state machine (`off | listening | capturing | thinking |
  speaking`), endpointing, the TTS queue, and the echo filter. Tunables live in
  one `VOICE_TUNING` block.
- **Routing is entirely client-side and synchronous — no model, no network.**
  `voice-commands.js` claims exact phrases first, then `voice-router.js` does
  the rest locally: a substitution table fixes cloud jargon browser STT gets
  wrong ("easy two" → EC2, "are d s" → RDS), UI commands are matched by
  requiring *every* token of a command phrase to be present, and anything else
  becomes a diagram instruction. Measured **0.2-1.2 ms**.
  This replaced a `POST /voice/route` LLM call that ran 1.5-3s typically but
  12.3s and 13.9s in one batch of eight. Latency is the feature here, and the
  timeout it needed had no good value: too short silently dropped the turn or
  sent the *raw* transcript uncorrected, too long left the user waiting. The
  job was always mechanical — do not reintroduce a model on this path.
- Matching is deliberately strict, because a loose match silently flips a
  setting the user never mentioned. Single-word phrases are excluded from fuzzy
  matching (the registry already handles `"stop"` exactly), and `"easy to"`
  only becomes EC2 before a compute noun — it is ordinary English otherwise.
- **Self-echo degrades automatically.** Barge-in needs the mic open during
  playback, and the Web Speech API opens its own capture stream that accepts no
  `echoCancellation` constraint, so the filter compares transcripts against what
  is being spoken — a guess about hardware no test can check. Rather than trust
  it, the filter's own hit rate is the signal: it only fires if playback really
  is reaching the mic, so three hits in a session drop voice mode to
  **half-duplex** (mic ignored during playback, except the stop phrases in
  `_INTERRUPT_PHRASES`). Announced via toast, reflected in the HUD, and relearnt
  each session so headphones win barge-in back. Tune with
  `VOICE_TUNING.halfDuplexAfterEchoes`.
- Covered by `tests/test_voice_mode_browser.py` (see Commands), because this is
  the one place a frontend regression is completely silent. Its sibling
  `tests/test_chat_flow_browser.py` covers the ordinary chat turn — transcript,
  `currentXml`, the insight panels, the send/stop lifecycle and the failure
  paths — by stubbing `fetch`, not `postChatStream`, so SSE parsing stays under
  test. Neither touches the draw.io iframe: it loads from a remote origin, and
  asserting on it would trade a unit test for a network test.

### Logging

`core/logging.StepLogger` accumulates structured step entries (`step`, `message`, `level`, `details`) surfaced both in the JSON response (`logs`) and via SSE progress events on `/chat/stream`. LLM token usage/cost is logged as `step="usage"` entries and summarized separately from the diagram's own estimated cloud cost.

Time a stage with `with logger.span("step", "message", **details):` (also works as `async with`) — it logs one entry carrying `duration_ms` and accumulates a per-step total. `xml_pipeline._build_response` emits the breakdown as a final `step="timings"` entry, so every response carries a stage profile without any call site threading a new field through. Every provider call is already timed inside `llm_client.call_llm` (`step="llm_call"`), covering the corrective re-ask and each backoff retry.

LLM calls run on a bounded private thread pool (`llm/concurrency.py`, sized by `MAX_CONCURRENT_LLM`), never on the default executor. This is deliberate: a semaphore around `asyncio.to_thread` releases its permit when a timed-out request is cancelled while the provider call keeps running, so the cap leaks (measured: 4 concurrent calls at a cap of 2). Keep LLM work on that pool — the store/document `asyncio.to_thread` calls rely on the default executor staying free.

## Cursor skills (not part of the FastAPI runtime)

`.cursor/skills/` contains standalone Cursor agent skills (`aws-architecture-diagram`, `azure-diagrams`, `drawio-skill`) for producing publish-quality `.drawio` files directly in Cursor chat, independent of this app's MCP config (`.cursor/mcp.json` is separate from `DRAWIO_MCP_URL`). See `docs/CLOUD_DIAGRAM_SKILLS.md`.
