# Prompt battle-test report — `config/xml_gen/**` + `llm/prompts/*.py`

Audit of the LLM prompt surface against the IR schemas, compilers, and the IR
correction loop that consume it. Focus: internal contradictions, prompt rules
without a code backstop, legacy vocabulary leaking into active packs, and
coverage gaps.

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

- **Prompt↔schema↔compiler contracts are enforced, not just requests.** Logical's
  ≤14 leaves / ≤5-per-domain is a *hard* IR error (`logical.py` `semantic_errors`).
  `flows`-required, dangling-ref, self-loop, group-zero-flow, data-tier-not-directly-
  outside, orphan-actor rules (`network.py`, `corridor.py`) are mechanically
  corrected by `_generate_ir_with_corrections` (`agent.py:476-647`), which passes
  the exact error strings back to the model. The prompt *requests* a shape; the IR
  layer *guarantees* it.
- **`CACHE_BREAKPOINT` split** (`system.py:23`, `ir.py:95`) keeps the stable pack
  prefix cacheable and the volatile per-request suffix (custom instructions,
  filtered catalog, model id) out of the cache key. Correct and in place for every
  IR view plus the 3D mark.
- **Legacy XML-era fields are gone.** `fallback_status`, `page_xml_crc`,
  `full_path_used`, `FIRST_RESPONSE_GUIDE` — none exist anywhere in the repo. The
  only remaining direct-XML packs are 3D (`prompt_3d.txt`) and surgical edit (no
  pack), both documented as such in `system.py`.
- **View isolation is clean.** ArchiMate gets its own single-pack entry point with
  no cloud classify/catalog/SaaS icons (`ir.py:127-158`); Generic explicitly
  forbids `shape_type` while the cloud IR path expects it; corridor views share one
  `corridor_core.txt` plus a per-view judgment pack. Mini examples in each pack are
  shaped exactly like the real schema.

## Findings by severity

### HIGH — the model is shown legacy XML-era vocabulary it is forbidden to emit

1. **`references/aws_stencils.txt` is an XML-mode stencil table, injected into
   Network/Security/Data-flow IR prompts.** Its first lines are literally
   `style="shape=mxgraph.aws4.resourceIcon;resIcon=..."` (`aws_stencils.txt:2-4`),
   and it is titled "draw.io stencil IDs (XML mode)". But the view packs say the
   opposite: `ir/network.txt:252-253` — "Never a draw.io style, stencil path, or
   `resIcon=` string". Worse, `ir.py`'s `_SHAPE_TYPE_GUIDE` tells the model to
   "convert a catalog entry to a semantic key" with the example `'RDS' -> aws_rds` —
   an example the catalog itself never contains (it holds already-lowercased keys
   like `lambda`, `ecs`, and maps colours into `style=` strings). So the guide and
   the AWS catalog disagree with each other, and the catalog teaches exactly the
   string format the view pack forbids. GCP/Azure/multi catalogs
   (`gcp-icons-common.txt`, `azure-icons-common.txt`, `multi-icons-top.txt`) use
   the newer `shape_type` column format and are fine — the AWS reference is the
   outlier.

2. **The classify packs are legacy placement maps injected into IR prompts whose
   schema they don't match.** `ir.py:64-72` appends `classify/<platform>.txt` to
   Network/Security/Data-flow. But:
   - `classify/aws.txt:8` — "Cloud level beside Edge (edges to VPCs — never inside
     one VPC row)";
   - `classify/aws.txt:9` — `parent="1"` wording;
   - `classify/aws.txt:16` — "Virtual Network (sibling of Zones)";
   - `classify/gcp.txt:3` — "NEVER use gcp2020" (unreachable: IR never emits
     stencil paths at all).
   None of that maps to NetworkIR's `edge_layer` / `groups` / subnet-tier
   vocabulary. Real costs: wasted tokens in the stable prefix, and a genuine risk
   of the model leaking `parent=`/stencil language into IR JSON.

### HIGH — contradictory instructions inside one prompt

3. **`simple.py` (Mermaid fast path) self-contradicts on the output format.**
   Line 4: "Output ONLY valid Mermaid.js source — no markdown fences, no XML, no
   commentary outside the structured response" vs line 24: "Return structured JSON
   with `mermaid`: ...". One line says raw Mermaid, the other says a JSON envelope.
   The LLM port's structured-output schema is what actually rescues this; the
   prompt text is fighting it. If a provider runs in a looser mode this becomes a
   parse-everything failure.

### MEDIUM — density guidance is inconsistent/absent where it matters most

4. **`ir/network.txt` has NO density cap.** Only these packs do:
   - `logical.txt:248` — hard cap ≤14 leaves, ≤5 per domain/shelf, **code-enforced**
     (`logical.py`);
   - `security.txt:230` — soft 8–12 / 5–7 when thin, **prompt-only**;
   - `data_flow.txt:186` — soft 8–12 / 5–7 when thin, **prompt-only**;
   - `generic.txt:119` — ≤40 (replication path, appropriate);
   - `archimate.txt:422` — 9–15 elements / 3–6 per band;
   - `prompt_3d.txt` — ≤15 leaf nodes.
   Network has the deepest nesting (region → VPC → zone → subnet → leaf, plus
   edge_layer, groups, connectivity hubs) and therefore the widest possible
   fan-out, yet carries no ceiling and no enforcement. Recommend a leaf-count cap
   in the pack plus a `network.py` `semantic_errors` rule reusing the existing
   tiny-fixture exemption (`network.py:490`: 1–3 leaves stay allowed).

### MEDIUM — the shared corridor pack implies a contract only Logical enforces

5. **Orphan-leaf policy is Logical-only but `corridor_core.txt` states a universal
   "every node connected" ideal.** `corridor_core.txt:103` — "`flows` is REQUIRED
   whenever 2+ leaves or any external actor exist". The hard validation in
   `corridor.py:376-406` enforces *that* for all three corridor views (valid). But
   `_orphan_errors` (`corridor.py:408+`) — "every leaf/actor must touch one flow" —
   is **opt-in and only enabled on Logical** (`logical.py`), deliberately NOT on
   Security/Data-flow (rationale: visible zone/phase boxes give context, so a
   passive leaf without an edge is legitimate there). The code reasoning is sound;
   the shared pack doesn't disclose it. On a data-flow request a model that read
   the shared pack may expect the same strict contract and be puzzled by a
   disconnected leaf in the output it was never nudged to fix. One clarifying
   sentence in `corridor_core.txt` (or in each view pack) removes the ambiguity.

6. **Flow-label guidance contradicts within the corridor family.**
   - `corridor_core.txt:112` — `label` optional, 1–6 words when used;
   - `logical.txt:162` — "Allowed edges (every flow labeled)";
   - `network.txt:157` — "**Every flow needs a short `label`** ... never blank on
     the primary path".
   Corridor views contradict their own shared core. Pick one baseline in
   `corridor_core` and let a pack only tighten it (e.g. Network's "required on
   primary path" is a sensible tightening; Logical's blanket "every flow" is not
   the same rule and sits under the shared core's "optional").

### LOW — hygiene / quality of life

7. **`_filter_catalog` header detection is format-specific.** `system.py:87-89`
   recognizes `━` / `Service` / `Product` / `Standalone` / `##` / `BROKEN` —
   tuned for `aws_stencils.txt`. GCP/Azure/multi catalogs are markdown tables whose
   column headers live in *body* lines, so a token-filtered slice can drop the
   table's header row while keeping its data rows (rows then lack column context).
   It fails safe (returns the full catalog when nothing matches), so this is
   cosmetic — but it is exactly where a future catalog reorganization breaks.
8. **Mermaid's "max 8 nodes"** (`simple.py:16`) has no server-side counterpart.
   Acceptable for a fast path, but inconsistent with the suite's otherwise
   enforced caps.
9. **Prompt budget test covers only the 3D path.** `test_prompt_assembly.py:14-17`
   asserts the 3D prompt stays under `MAX_SYSTEM_PROMPT_TOKENS`. The IR assemblies
   (incl. Network, which is the largest) are never budget-tested — cheap to add.

## Recommended remediations (ordered)

1. Replace `references/aws_stencils.txt` with a semantic-key catalog matching the
   GCP/Azure form (`shape_type | Display Name`), and strip the `style=`/`resIcon=`
   lines — or, minimally, fix `_SHAPE_TYPE_GUIDE`'s example to match the actual
   file format. Blocks finding 1.
2. Slim the classify packs to the tier judgement that still earns tokens
   (app-tier vs data-tier vs cross-cutting concern) and drop `parent=` /
   "Virtual Network (sibling of Zones)" / "NEVER use mxgraph.<alt>" wording; align
   the placement column with the IR schema it now feeds. Blocks finding 2.
3. Fix `simple.py` line 1 to say "Return structured JSON matching the schema" and
   drop "Output ONLY valid Mermaid source". Blocks finding 3.
4. Add a Network density cap: pack text + a `network.py` `semantic_errors` rule
   (reusing the 1–3 leaf fixture exemption), or a `semantic_warnings` nudge if a
   hard block feels too aggressive. Blocks finding 4.
5. Harmonize the flow-label rule and the orphan-leaf policy across
   `corridor_core.txt` and the view packs (findings 5–6).
6. Add tests: extend `test_prompt_assembly.py` to budget-check every IR assembly;
   assert the absence of `parent=` / `resIcon=` / `mxgraph.` in generated IR-schema
   prompts so findings 1–2 can never regress. Finding 7+9.

## Evidence appendix (verified chains)

| # | Claim | File refs |
|---|-------|-----------|
| 1 | AWS catalog is XML style, IR forbids it | `references/aws_stencils.txt:2-4` ↔ `ir/network.txt:252-253` ↔ `llm/prompts/ir.py:36-43` |
| 2 | Classify pack injected into IR, legacy placement | `llm/prompts/ir.py:64-72` ↔ `classify/aws.txt:8-9,16` ↔ `ir/network.txt:11-27,74-116` |
| 3 | Mermaid prompt format conflict | `llm/prompts/simple.py:4` vs `:24-27` |
| 4 | Density caps: only Logical enforced | `ir/logical.txt:250` + `domain/ir/logical.py:182-192`; `ir/security.txt:230`, `ir/data_flow.txt:188` prompt-only; `ir/network.txt` none |
| 5 | Orphan-leaf Logical-only | `corridor_core.txt:103` ↔ `domain/ir/corridor.py:376-406,408+` ↔ `domain/ir/logical.py:180` |
| 6 | Flow-label optional vs required | `corridor_core.txt:112` vs `logical.txt:162`, `network.txt:157-163` |
| 7 | Filter header heuristic | `llm/prompts/system.py:69-102` vs `references/gcp-icons-common.txt`, `azure-icons-common.txt`, `multi-icons-top.txt` |
| 8 | Mermaid node cap prompt-only | `llm/prompts/simple.py:16` |
| 9 | Budget test 3D-only | `tests/test_prompt_assembly.py:14-17` |