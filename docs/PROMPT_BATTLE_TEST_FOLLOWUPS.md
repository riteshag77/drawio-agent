# Prompt battle-test — follow-up observations

Supplementary findings to `docs/PROMPT_BATTLE_TEST.md`, gathered in a second pass
over the same surface. These are lower-signal than the headline findings there —
mostly dead scaffolding, one genuine resilience asymmetry, and a couple of
path-consistency confirmations.

## Dead / inert scaffolding

1. **`{{MODEL_NAME}}` replacement is dead code.**
   `app/adapters/outbound/llm/prompts/ir.py:92-93` and
   `app/adapters/outbound/llm/prompts/system.py:133` call
   `.replace("{{MODEL_NAME}}", ...)`, but **zero** prompt files actually contain
   that token (verified across every `*.txt` under `config/`). The intended
   model-name injection never happens, and a future intent to tailor prompts per
   model has no hook to build on.

2. **`primary_view` is an accepted-but-unread parameter.**
   `get_xml_gen_system_prompt` / `get_xml_gen_system_prompt_marked`
   (`system.py:146,171`) declare `primary_view: str = "network"` but never read it
   in the body. Dead API surface.

3. **Mermaid fast path ignores the injected LLM port.**
   `generate_mermaid_diagram` hardcodes `get_llm()` (`agent.py:1311`) instead of
   the `llm` injection pattern every other generator uses. Not a prompt bug, but
   it makes that path un-testable / un-swappable in isolation.

## Resilience asymmetry

4. **A missing view-pack file 500s the request; a missing catalog/classify file
   degrades gracefully.**
   `ir.py:63` calls `_load_prompt(name)` unguarded for `view_prompt_files`, while
   the classify/SaaS loads (`ir.py:71`, `:78`) are wrapped in
   `try/FileNotFoundError`. A typo in the pack list = hard failure; a missing
   classify pack = silent fallback. Recommended: align them — either both fail
   hard (a config error should be loud) or both degrade smoothly.

## Path consistency (confirmations, no action needed)

5. **Images/PDFs take the vision path; only text digs down to `context_text`** —
   consistent with the attachment classifier in
   `app/adapters/outbound/utils/documents.py`; no local text-extraction drift.

6. **`animated_connectors` is correctly applied at compile time for IR views**
   (`agent.py:1230,1236`) — so the `_ANIMATED_HINT` in `_build_human_parts`
   (`agent.py:121-124`) is legacy-orphaned UI on the XML path (which only 3D /
   surgical edit use now). No behavioral conflict today, just a duplicated
   mechanism.

7. **Security's 6-hop trust journey is cross-checked by a test.**
   `tests/test_security_edge_cases.py:483-485` enforces
   `story_step_cap("Security") >= 6` — the pack's worked example and the
   storybox cap are deliberately kept in sync.

## Suggested handling

- Fix item 4 (resilience asymmetry) if prompt-pack content review happens — it is
  the only one of these with user-visible consequences.
- Items 1–3 are hygiene; address opportunistically (e.g. when the IR assembly is
  next touched) rather than as a dedicated change.