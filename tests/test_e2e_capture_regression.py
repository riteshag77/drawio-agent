"""Live end-to-end regression suite — replays a real multi-turn session through
the actual chat pipeline (``_run_chat``, the same function ``POST /chat``
calls) with a real LLM, no mocking.

Makes real provider calls, so it is skipped by default and does NOT run as
part of a plain ``pytest`` invocation. Opt in with:

    RUN_LIVE_E2E=1 pytest tests/test_e2e_capture_regression.py -v -s

This exists to catch the class of bug that unit tests structurally can't:
regressions that only show up across a real multi-turn conversation with a
real model. Three such bugs were found by manual testing in one session
(sequential edits silently undoing each other, the Overview Panel going
stale after a surgical edit, suggestions failing with zero visible signal)
before any of them had a test — this suite is the automated replacement for
that manual discovery process. See tools/e2e_capture_compare.py for the
adjacent manual/CLI variant of the same idea (score a capture's polish
against next-ai-draw-io conventions) — this module instead asserts hard
pass/fail invariants suitable for a gate, not a score.

The single test below drives the full lifecycle a real user exercises:
create -> apply a suggestion -> apply a WAF finding -> apply a compliance
finding -> a manual free-form edit typed by the user -> verify nothing from
an earlier step got silently undone by a later one.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import xml.etree.ElementTree as ET

from app.adapters.inbound.web.routes import _run_chat
from app.adapters.inbound.web.schemas import ChatMessage, ChatRequest
from app.adapters.outbound.diagram.xml_diagnostics import (
    diagnose_xml,
    ensure_overview_panel_position,
    validate_layout,
)
from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document, extract_page_by_name
from app.core.logging import StepLogger

_RUN_LIVE = os.environ.get("RUN_LIVE_E2E") == "1"
_SKIP_REASON = "set RUN_LIVE_E2E=1 to run the live-LLM regression suite (makes real provider calls)"

_PROVIDER = "gemini"  # cheap/fast default — override via LIVE_E2E_PROVIDER if needed
_PLATFORM = "aws"

# Used only if the live WAF/Compliance assessment on turn 1 doesn't surface at
# least two real findings on its own (e.g. the generated diagram happens to
# already satisfy everything checked) — keeps the test deterministic either way.
_FALLBACK_FINDINGS = [
    {"message": "No key management or encryption service detected", "missing_services": ["aws_kms"]},
    {"message": "No WAF detected in front of the load balancer", "missing_services": ["aws_waf"]},
]


#: Multi-view by default — this is the realistic case (a real live capture
#: with all four views selected is exactly what exposed the Overview Panel
#: bug: the fixer only ever looked at page 1 of the merged mxfile, and
#: Network — the page a surgical edit actually grows — is page 2, not page 1,
#: per VIEW_ORDER (Logical, Network, Data flow, Security). A single-view
#: request never exercises the merged-multi-page code path at all.
_DEFAULT_VIEWS = ["logical", "network", "security", "data_flow"]


def _send(messages: list[dict[str, str]], **overrides) -> dict:
    body = ChatRequest(
        messages=[ChatMessage(**m) for m in messages],
        provider=os.environ.get("LIVE_E2E_PROVIDER", _PROVIDER),
        platform=_PLATFORM,
        diagram_views=overrides.pop("diagram_views", _DEFAULT_VIEWS),
        **overrides,
    )
    logger = StepLogger()
    return asyncio.run(_run_chat(body, logger))


def _extract_findings(waf: dict | None, compliance: dict | None, limit: int = 2) -> list[dict]:
    """Flatten real WAF pillar findings + compliance control findings into the
    same {message, missing_services} shape insights-panels.js works with."""
    out: list[dict] = []
    for pillar in (waf or {}).get("pillars", []) or []:
        for f in pillar.get("findings", []) or []:
            if f.get("message"):
                out.append({"message": f["message"], "missing_services": f.get("missing_services") or []})
    for fw in ((compliance or {}).get("frameworks") or {}).values():
        for ctrl in fw.get("controls", []) or []:
            for f in ctrl.get("findings", []) or []:
                if f.get("message"):
                    out.append({"message": f["message"], "missing_services": f.get("missing_services") or []})
    seen: set[str] = set()
    deduped = []
    for f in out:
        if f["message"] in seen:
            continue
        seen.add(f["message"])
        deduped.append(f)
    if len(deduped) < limit:
        for fallback in _FALLBACK_FINDINGS:
            if len(deduped) >= limit:
                break
            if fallback["message"] not in seen:
                deduped.append(fallback)
                seen.add(fallback["message"])
    return deduped[:limit]


def _finding_apply_prompt(finding: dict) -> str:
    """Mirrors static/js/insights-panels.js applyInsightFixes()'s exact request shape."""
    line = f"1. {finding['message']}"
    svcs = finding.get("missing_services") or []
    if svcs:
        line += " — Add one of: " + ", ".join(svcs) + "."
    return "Please update the diagram to address the following:\n" + line


def _suggestion_apply_prompt(suggestion: dict) -> str:
    """Mirrors static/js/chat.js implementSuggestions()'s exact request shape."""
    cat = suggestion.get("category") or "general"
    tag = f" [{cat}]" if cat != "general" else ""
    return "Implement these suggestions:\n1. " + suggestion.get("text", "") + tag


def _vertex_ids(xml_str: str) -> set[str]:
    """Every vertex id on the Network page — used to detect a node silently
    vanishing across a later turn (the core sequential-edit regression)."""
    page = extract_page_by_name(xml_str or "", "Network") or xml_str or ""
    try:
        parsed = ET.fromstring(ensure_diagram_document(page, "Network"))
    except ET.ParseError:
        return set()
    return {
        c.get("id") for c in parsed.iter("mxCell")
        if c.get("vertex") == "1" and c.get("id")
    }


@unittest.skipUnless(_RUN_LIVE, _SKIP_REASON)
class FullLifecycleRegressionTests(unittest.TestCase):
    """One real session: create -> apply suggestion -> apply WAF finding ->
    apply compliance finding -> manual free-form edit. Every step's additions
    must still be present after every later step."""

    def test_full_lifecycle_preserves_every_earlier_change(self) -> None:
        gen_prompt = (
            "Design a simple highly available AWS web app: an application load "
            "balancer, an app server tier, and a relational database. Keep it small."
        )
        conversation: list[dict[str, str]] = [{"role": "user", "content": gen_prompt}]

        # ── Step 1: create ──────────────────────────────────────────────
        resp1 = _send(
            conversation,
            suggestions=True, cost_estimate=True, well_architected=True, compliance=True,
        )
        self.assertEqual(resp1.get("status"), "success", resp1.get("detail"))
        xml1 = resp1.get("xml") or ""
        self.assertTrue(xml1, "fresh generation must return xml")
        conversation.append({"role": "assistant", "content": resp1.get("assistant_message") or "Diagram updated."})

        # Bug 3 regression: suggestions must either be populated, or the
        # system must honestly report why not (degraded_features) — never
        # silently empty with zero signal, which was the actual bug.
        suggestions = resp1.get("suggestions") or []
        degraded1 = resp1.get("degraded_features") or []
        suggestions_degraded = any(d.get("feature") == "suggestions" for d in degraded1)
        self.assertTrue(
            suggestions or suggestions_degraded,
            f"suggestions empty with no degraded signal — the exact silent-failure bug. "
            f"logs={[l for l in resp1.get('logs') or [] if l.get('step') == 'suggestions']}",
        )

        findings = _extract_findings(resp1.get("well_architected"), resp1.get("compliance"))
        self.assertGreaterEqual(len(findings), 2, "need at least 2 findings (real or fallback) to test sequencing")

        applied_suggestions: list[str] = []
        step_ids: list[tuple[str, set[str]]] = []  # (step label, ids newly introduced at that step)
        prev_xml = xml1
        prev_ids = _vertex_ids(xml1)
        current_xml = xml1

        def _apply_step(label: str, user_text: str) -> str:
            nonlocal current_xml, prev_ids
            conversation.append({"role": "user", "content": user_text})
            resp = _send(
                conversation, current_xml=current_xml, applied_suggestions=applied_suggestions,
            )
            self.assertEqual(resp.get("status"), "success", f"{label}: {resp.get('detail')}")
            new_xml = resp.get("xml") or ""
            self.assertTrue(new_xml, f"{label}: response had no xml")
            conversation.append({"role": "assistant", "content": resp.get("assistant_message") or "Diagram updated."})
            new_ids = _vertex_ids(new_xml)
            added = new_ids - prev_ids
            step_ids.append((label, added))
            current_xml = new_xml
            prev_ids = new_ids
            return new_xml

        # ── Step 2: apply a suggestion (implementSuggestions()'s exact shape) ──
        if suggestions:
            picked = suggestions[0]
            xml2 = _apply_step("apply-suggestion", _suggestion_apply_prompt(picked))
            applied_suggestions.append(picked.get("text") or picked.get("suggestion") or "")
        else:
            xml2 = current_xml  # suggestions degraded this run — skip, don't fail the chain over it

        # ── Step 3 & 4: apply two separate WAF/compliance findings — the
        # exact reported bug: applying the 2nd must not undo the 1st. ──
        _apply_step("apply-finding-1", _finding_apply_prompt(findings[0]))
        _apply_step("apply-finding-2", _finding_apply_prompt(findings[1]))

        # ── Step 5: a manual, free-form edit typed by the user (not from any
        # insights panel) — the most common real-world edit shape. ──
        final_xml = _apply_step(
            "manual-edit",
            "Rename the app server to Orders API and add a Redis cache between "
            "the app tier and the database.",
        )

        # ── Core regression: every id introduced at an earlier step must
        # still exist in the final diagram. ──
        final_ids = _vertex_ids(final_xml)
        for label, added in step_ids:
            if not added:
                continue
            missing = added - final_ids
            self.assertFalse(
                missing,
                f"node(s) added at step '{label}' are gone from the final diagram "
                f"after later edits: {missing} — a later edit silently undid this step",
            )

        # ── Bug 2 regression + general structural sanity — checked on EVERY
        # page, not just the primary one. This is the exact shape of the real
        # bug: the fixer that re-positions the Overview Panel only ever looked
        # at page 1 of the merged mxfile (Logical, per VIEW_ORDER), so a
        # Network-only check here would have passed even when Network (page 2)
        # was still broken — which is what actually happened in a live capture. ──
        for page_name in _DEFAULT_VIEWS:
            title = {
                "logical": "Logical", "network": "Network",
                "security": "Security", "data_flow": "Data flow",
            }[page_name]
            page_xml = extract_page_by_name(final_xml, title)
            if page_xml is None:
                continue  # view wasn't rendered as a separate page this run
            if 'id="overview_panel"' in page_xml:
                refixed = ensure_overview_panel_position(page_xml)
                self.assertEqual(
                    refixed, page_xml,
                    f"[{title}] Overview Panel needed repositioning after the response "
                    "already left the pipeline — it went stale relative to final diagram "
                    "bounds (only page 1 of the merged mxfile getting fixed is the exact "
                    "bug this multi-page check exists to catch)",
                )

            issues = validate_layout(page_xml)
            orphan_issues = [i for i in issues if "disconnected" in i]
            overlap_issues = [i for i in issues if "overlap" in i.lower()]
            self.assertEqual(orphan_issues, [], f"[{title}] orphaned node(s) after the edit chain: {orphan_issues}")
            self.assertEqual(overlap_issues, [], f"[{title}] overlapping sibling(s) after the edit chain: {overlap_issues}")

            diagnostics = diagnose_xml(page_xml)
            self.assertEqual(diagnostics.errors, [], f"[{title}] structural errors in final diagram: {diagnostics.errors}")


if __name__ == "__main__":
    unittest.main()
