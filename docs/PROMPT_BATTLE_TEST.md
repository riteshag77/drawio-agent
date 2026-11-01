# Prompt battle-test report — `config/xml_gen/**` + `llm/prompts/*.py`

Audit of the LLM prompt surface against the IR schemas, compilers, and the IR
correction loop that consume it. Focus: internal contradictions, prompt rules
without a code backstop, legacy vocabulary leaking into active packs, and
coverage gaps.

> **Status 2026-08-14: every finding below is fixed.** Two more turned up in a
> fresh third pass — kimi/moonshot-v1-8k's `max_output_tokens` (undersized
> against its own 8K context window) and a residual "Virtual Network (sibling
> of Zones)" line the A2 cleanup missed in the classify packs — both closed in
> the same commit. All 18 are covered by regression tests; see
> `tests/test_prompt_assembly.py`, `tests/test_mermaid_generation.py`,
> `tests/test_response_parse.py`, `tests/test_providers.py`.
>
> The findings below are kept as a record of what was wrong and why, not as an
> open list — re-verify against current code before trusting a claim here, the
> same way this document itself was re-verified twice before being trusted.

> **Re-verified 2026-08-13 against HEAD** (superseded by the note above). This
> merged the original report with the second-pass follow-ups (previously
> `PROMPT_BATTLE_TEST_FOLLOWUPS.md`, now removed) into one severity-ordered
> list, grouped by root cause rather than by which pass found them.

## Scope & method

Every file was read in full and cross-checked against the code that reads it:

- Pack content: `config/xml_gen/ir/*.txt` (network, logical, security, data_flow,
  corridor_core, generic, archimate), `config/xml_gen/classify/*.txt`
  (aws/gcp/azure/multi), `config/xml_gen/icons/saas.txt`, `prompt_3d.txt`,
  `gap_analysis.txt`, `docs/adr_raid.txt`, `config/compliance_llm_prompt.txt`.
- Catalog/reference content: `config/references/*.txt` (aws_stencils, aws-icons-3d,
  gcp/azure/multi icons).
- Assembly code: `app/adapters/outbound/llm/prompts/{ir,system,simple,vlm_qa}.py`,
  `prompt_builders.py`.
- Enforcement: `app/domain/ir/*.py` `semantic_errors()` / `semantic_warnings()`,
  `app/core/services/agent.py` (correction loop), `geometry_spec.py`.
- Tests: `tests/test_prompt_assembly.py`, `test_logical_quality.py`,
  `test_security_edge_cases.py`, and the IR/edge-case suites.

## What is already strong (no change needed)

- **Prompt↔schema↔compiler contracts are enforced, not just requested.** Logical's
  ≤14 leaves / ≤5-per-domain is a *hard* IR error (`logical.py` `semantic_errors`).
  `flows`-required, dangling-ref, self-loop, group-zero-flow, data-tier-not-directly-
  outside, orphan-actor rules (`network.py`, `corridor.py`) are mechanically
  corrected by `_generate_ir_with_corrections` (`agent.py`), which passes the exact
  error strings back to the model. The prompt *requests* a shape; the IR layer
  *guarantees* it.
- **`CACHE_BREAKPOINT` split** (`system.py`, `ir.py`) keeps the stable pack prefix
  cacheable and the volatile per-request suffix (custom instructions, filtered
  catalog, model id) out of the cache key. Correct and in place for every IR view
  plus the 3D mark.
- **Legacy XML-era fields are gone.** `fallback_status`, `page_xml_crc`,
  `full_path_used`, `FIRST_RESPONSE_GUIDE` — none exist anywhere in the repo. The
  only remaining direct-XML packs are 3D (`prompt_3d.txt`) and surgical edit (no
  pack), both documented as such in `system.py`.
- **View isolation is clean.** ArchiMate gets its own single-pack entry point with
  no cloud classify/catalog/SaaS icons; Generic explicitly forbids `shape_type`
  while the cloud IR path expects it; corridor views share one `corridor_core.txt`
  plus a per-view judgment pack. Mini examples in each pack are shaped exactly like
  the real schema.
- **Attachment routing is consistent.** Images/PDFs take the vision path and only
  text digs down to `context_text`, matching the classifier in
  `utils/documents.py`. No local text-extraction drift.
- **`animated_connectors` is applied at compile time for IR views** (`agent.py`),
  so the `_ANIMATED_HINT` in `_build_human_parts` is legacy-orphaned on the XML
  path (3D / surgical edit only). A duplicated mechanism, not a conflict.
- **Security's 6-hop trust journey is cross-checked by a test.**
  `tests/test_security_edge_cases.py` enforces `story_step_cap("Security") >= 6`,
  keeping the pack's worked example and the storybox cap in sync.

---

## A. Legacy XML-era vocabulary injected into IR prompts — HIGH

Both findings share one root cause: content written for the retired direct-XML
path is still concatenated into prompts whose schema forbids exactly that
vocabulary. The model is shown the thing it is told never to emit.

**A1. `references/aws_stencils.txt` is an XML-mode stencil table, injected into
Network/Security/Data-flow IR prompts.** Its first lines are literally
`style="shape=mxgraph.aws4.resourceIcon;resIcon=..."` and it is titled "draw.io
stencil IDs (XML mode)", while `ir/network.txt` says "Never a draw.io style,
stencil path, or `resIcon=` string". Compounding it, `ir.py`'s
`_SHAPE_TYPE_GUIDE` tells the model to "convert a catalog entry to a semantic
key" with the example `'RDS' -> aws_rds` — a form the catalog never contains
(it holds lowercased keys like `lambda`, `ecs`, and maps colours into `style=`
strings). The guide and the AWS catalog disagree with each other, and the
catalog teaches the exact string format the view pack forbids. The GCP/Azure/multi
catalogs use the newer `shape_type` column format and are fine — AWS is the
outlier.

**A2. The classify packs are legacy placement maps injected into IR prompts
whose schema they don't match.** `ir.py` appends `classify/<platform>.txt` to
Network/Security/Data-flow, but that content still says `parent="1"`,
"Virtual Network (sibling of Zones)", and "NEVER use mxgraph.gcp2 / gcp2020"
(unreachable — IR never emits stencil paths). None of it maps to NetworkIR's
`edge_layer` / `groups` / subnet-tier vocabulary. Real costs: wasted tokens in
the *stable, cached* prefix, and a genuine risk of `parent=`/stencil language
leaking into IR JSON.

## B. Instructions that contradict each other — HIGH / MEDIUM

**B1. `simple.py` (Mermaid fast path) self-contradicts on output format — HIGH.**
Line 4: "Output ONLY valid Mermaid.js source — no markdown fences, no XML, no
commentary" versus line 24: "Return structured JSON with `mermaid`: …". One says
raw Mermaid, the other a JSON envelope. The LLM port's structured-output schema
is what rescues this today; the prompt text is fighting it. A provider running in
a looser structured-output mode turns this into a parse-everything failure.

**B2. Flow-label guidance contradicts within the corridor family — MEDIUM.**
`corridor_core.txt:111` — "`label` is optional; when used, …"; `logical.txt:161` —
"Allowed edges (every flow labeled)"; `network.txt:157` — "**Every flow needs a
short `label`**". Two corridor views contradict their own shared core. Pick one
baseline in `corridor_core` and let a pack only *tighten* it — Network's
"required on the primary path" is a sensible tightening, Logical's blanket
"every flow" is a different rule sitting under the shared core's "optional".

**B3. The shared corridor pack implies a contract only Logical enforces — MEDIUM.**
`corridor_core.txt:103` states "`flows` is REQUIRED whenever 2+ leaves or any
external actor exist", which `corridor.py` does enforce for all three corridor
views. But `_orphan_errors` — "every leaf/actor must touch one flow" — is
**called only from `logical.py:180`**, deliberately not on Security/Data-flow,
because visible zone/phase boxes give a passive leaf legitimate context. The code
reasoning is sound; the shared pack never discloses it, so a model reading the
shared core on a data-flow request may expect a contract it is never nudged to
satisfy. One clarifying sentence removes the ambiguity.

## C. Rules with no code backstop — MEDIUM / LOW

> **Superseded — do not act on C1.** The Network cap this section asked for
> was added (`MAX_LEAVES = 26`) and has since been removed again, along with
> Logical's ≤14/≤5. Both were hard `semantic_errors`, so an estate or a
> journey larger than a readability preference failed the whole request. A
> 54-leaf Network page and a 24-leaf Logical page were then compiled and
> rendered: both lay out correctly — containers size to their contents,
> subnets stay aligned, flows route — so the numbers were never describing a
> limit the compiler had. They are `density_notes()` now: logged, never
> blocking, and deliberately not `semantic_warnings()` either, since that
> channel buys a corrective LLM pass and "correcting" density means dropping
> components the user asked for. The table below is kept as the historical
> record of how the caps got there.

**C1. `ir/network.txt` has no density cap at all — MEDIUM.** Compare:

| pack | cap | enforced? |
|---|---|---|
| `logical.txt` | ≤14 leaves, ≤5 per domain | **yes** — `logical.py` |
| `security.txt` | soft 8–12 / 5–7 when thin | prompt only |
| `data_flow.txt` | soft 8–12 / 5–7 when thin | prompt only |
| `archimate.txt` | 9–15 elements, 3–6 per band | prompt only |
| `generic.txt` | ≤40 (replication path) | prompt only |
| `prompt_3d.txt` | ≤15 leaf nodes | prompt only |
| **`ir/network.txt`** | **none** | **none** |

Network has the deepest nesting (region → VPC → zone → subnet → leaf, plus
`edge_layer`, `groups`, connectivity hubs) and therefore the widest possible
fan-out, yet carries no ceiling and no enforcement — verified again on HEAD:
`network.txt` has no `## Density` section and `network.py` has no leaf-count
rule. Recommend a pack cap plus a `network.py` `semantic_errors` rule reusing the
existing tiny-fixture exemption (1–3 leaves stay allowed), or a
`semantic_warnings` nudge if a hard block feels too aggressive.

**C2. Mermaid's "max 8 nodes" has no server-side counterpart — LOW.**
Acceptable for a fast path, but inconsistent with the suite's otherwise enforced
caps.

## D. Dead scaffolding — LOW

**D1. `{{MODEL_NAME}}` replacement is dead code.** `ir.py:92-93` and
`system.py:133-134` call `.replace("{{MODEL_NAME}}", model_name)`, but **zero**
files under `config/` contain that token (re-verified: 0 matches). The intended
per-model tailoring never happens and has no hook to build on.

**D2. `primary_view` is accepted but never read.**
`get_xml_gen_system_prompt` / `..._marked` (`system.py:146,171`) declare
`primary_view: str = "network"` and never reference it in the body. Dead API
surface.

**D3. The Mermaid fast path ignores the injected LLM port.**
`generate_mermaid_diagram` hardcodes `get_llm()` (`agent.py:1311`) instead of the
`llm` injection every other generator accepts, leaving that path un-fakeable in
isolation.

## E. Resilience & hygiene — MEDIUM / LOW

**E1. A missing view-pack file 500s the request; a missing classify/SaaS pack
degrades silently — MEDIUM.** `ir.py:63` calls `_load_prompt(name)` unguarded for
`view_prompt_files`, while the classify and SaaS loads are wrapped in
`try/except FileNotFoundError`. A typo in the pack list is a hard failure; a
missing classify pack is an invisible downgrade. Align them — a config error
should be loud, or both should degrade. This is the only item in the
lower-severity group with user-visible consequences.

**E2. `_filter_catalog` header detection is format-specific — LOW.**
`system.py` recognizes `━` / `Service` / `Product` / `Standalone` / `##` /
`BROKEN`, tuned for `aws_stencils.txt`. The GCP/Azure/multi catalogs are markdown
tables whose column headers live in *body* lines, so a token-filtered slice can
drop the header row while keeping data rows. It fails safe (returns the full
catalog when nothing matches), so this is cosmetic — but it is exactly where a
future catalog reorganization breaks.

**E3. The prompt-budget test covers only the 3D path — LOW.**
`tests/test_prompt_assembly.py` asserts the 3D prompt stays under
`MAX_SYSTEM_PROMPT_TOKENS`. The IR assemblies — including Network, the largest —
are never budget-tested.

---

## Remediation order

**All steps below are done** (commits `e7c48a9`, `6d68c39`) — kept as the
original plan, not a to-do list. Ordered by value per unit of risk; the first
two were the only ones that changed what the model sees on every cloud
request.

1. **Fix the AWS catalog (A1).** Replace `references/aws_stencils.txt` with a
   semantic-key catalog matching the GCP/Azure form (`shape_type | Display Name`)
   and strip the `style=`/`resIcon=` lines — or, minimally, correct
   `_SHAPE_TYPE_GUIDE`'s example to match the file's actual format.
2. **Slim the classify packs (A2).** Keep the tier judgement that still earns its
   tokens (app-tier vs data-tier vs cross-cutting); drop `parent=`,
   "Virtual Network (sibling of Zones)", and "NEVER use mxgraph.<alt>"; align the
   placement column with the IR schema it now feeds.
3. **Resolve the Mermaid format contradiction (B1).** Make line 4 say "Return
   structured JSON matching the schema" and drop "Output ONLY valid Mermaid
   source".
4. **Give Network a density cap (C1)** — pack text plus a `network.py` rule,
   reusing the 1–3 leaf fixture exemption.
5. **Harmonize the corridor family (B2, B3).** One flow-label baseline in
   `corridor_core`; one sentence disclosing that the orphan check is Logical-only.
6. **Make pack loading symmetric (E1).**
7. **Add the regression tests (E3, and a guard for A1/A2).** Extend
   `test_prompt_assembly.py` to budget-check *every* IR assembly, and assert that
   generated IR-schema prompts contain no `parent=`, `resIcon=`, or `mxgraph.`
   so A1 and A2 can never come back.
8. **Clear the dead scaffolding (D1–D3)** opportunistically, next time the IR
   assembly is touched.

Step 7 is worth pulling forward if any of steps 1–6 are done: the prompt packs
are program logic with no automated safety net, so the only thing that stops a
fixed finding from regressing is a test that fails when it does.

## Evidence appendix (verified chains)

| # | Claim | File refs |
|---|-------|-----------|
| A1 | AWS catalog is XML style, IR forbids it | `references/aws_stencils.txt:2-4` ↔ `ir/network.txt:252-253` ↔ `llm/prompts/ir.py:36,86` ↔ `system.py:66` |
| A2 | Classify pack injected into IR, legacy placement | `llm/prompts/ir.py:64-72` ↔ `classify/aws.txt:9,16,21` ↔ `ir/network.txt:11-27,74-116` |
| B1 | Mermaid prompt format conflict | `llm/prompts/simple.py:4` vs `:24` |
| B2 | Flow-label optional vs required | `corridor_core.txt:111` vs `logical.txt:161`, `network.txt:157` |
| B3 | Orphan check Logical-only | `corridor_core.txt:103` ↔ `domain/ir/corridor.py:408` ↔ `domain/ir/logical.py:167,180` |
| C1 | Density caps: only Logical enforced | `ir/logical.txt:15` + `domain/ir/logical.py`; `security.txt:230`, `data_flow.txt:186`, `archimate.txt:422` prompt-only; `ir/network.txt` none |
| C2 | Mermaid node cap prompt-only | `llm/prompts/simple.py:16` |
| D1 | `{{MODEL_NAME}}` never present in any pack | `llm/prompts/ir.py:92-93`, `system.py:133-134` ↔ 0 matches under `config/` |
| D2 | `primary_view` declared, never read | `llm/prompts/system.py:146,171` |
| D3 | Mermaid path hardcodes the port | `core/services/agent.py:1311` |
| E1 | View packs fail hard, classify degrades | `llm/prompts/ir.py:63` vs `:70-78` |
| E2 | Filter header heuristic | `llm/prompts/system.py:87-89` vs `references/gcp-icons-common.txt`, `azure-icons-common.txt`, `multi-icons-top.txt` |
| E3 | Budget test 3D-only | `tests/test_prompt_assembly.py:18-19` |
