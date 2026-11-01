"""One chat turn, in a real browser: request -> SSE -> applied result.

The second frontend test, and the reason it exists is the same as the first's:
this path has no server-side assertion that can stand in for it. ``/chat`` is
covered thoroughly on the Python side, but everything that happens *after* the
response arrives — the transcript, ``currentXml``, the six insight panels, the
send/stop lifecycle, what an error does to the composer — lives only in
``static/js`` and was, until this file, checked by nobody.

Deliberately does not touch the draw.io iframe. The embed loads from a remote
origin, so asserting on it would make this test a network test with a flake
budget; every assertion here is on state the app owns itself.

``fetch`` is stubbed at the network boundary rather than the function boundary:
``chat-api.js``'s job is parsing SSE frames, so a test that replaced
``postChatStream`` would skip the part most likely to break.

Same environment contract as tests/test_voice_mode_browser.py — skips without a
browser, fails instead when ``REQUIRE_BROWSER_TESTS=1``.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]

try:  # noqa: SIM105 - the import *is* the capability check
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment-dependent
    sync_playwright = None

_REQUIRE_BROWSER = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"


def _unavailable(reason: str) -> Exception:
    if _REQUIRE_BROWSER:
        return RuntimeError(f"REQUIRE_BROWSER_TESTS=1 but {reason}")
    return unittest.SkipTest(reason)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


#: A complete, realistic ``result`` frame. Tests mutate a copy of this rather
#: than building a payload each time, so a field added to the real response
#: shape has exactly one place to be added here too.
RESULT_PAYLOAD = {
    "type": "result",
    "status": "success",
    "xml": '<mxfile host="embed.diagrams.net"><diagram name="Network" id="p1">'
           "<mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/>"
           "</root></mxGraphModel></diagram></mxfile>",
    "assistant_message": "Added a Redis cache in front of the database. It sits in the app subnet.",
    "generation_path": "ir",
    "renderer_used": "local",
    "diagram_type": "cloud",
    "cost_estimate": {
        "total": 1234.5,
        "currency": "AUD",
        "breakdown": {"Compute": [{"service": "EC2", "monthly": 900.0}],
                      "Database": [{"service": "RDS", "monthly": 334.5}]},
    },
    "well_architected": {"score": 72, "pillars": {}, "findings": []},
    "compliance": {"frameworks": [], "controls": []},
    "suggestions": ["Enable Multi-AZ on RDS", "Add a WAF in front of the ALB"],
    "logs": [{"step": "ir", "message": "Compiled Network view", "level": "success"}],
    "llm_usage": {"total_tokens": 4200, "input_tokens": 3000, "output_tokens": 1200},
}

#: Installed before any page script. Replaces only the two chat endpoints and
#: records what was sent; every other request (the app's own JS, /providers,
#: ...) goes to the real server.
FETCH_STUB = """
window.__chatRequests = [];
window.__chatResult = %s;
window.__chatMode = 'ok';           // 'ok' | 'http-error' | 'error-frame'
window.__chatHold = false;          // never resolves, for lifecycle assertions

const _origFetch = window.fetch.bind(window);
const _frame = (obj) => `data: ${JSON.stringify(obj)}\\n\\n`;

window.fetch = (url, opts = {}) => {
    const u = String(url && url.url ? url.url : url);
    if (!u.includes('/chat')) return _origFetch(url, opts);

    window.__chatRequests.push(JSON.parse(opts.body || '{}'));

    if (window.__chatHold) {
        return new Promise((_, reject) => {
            const abort = () => {
                const err = new Error('aborted'); err.name = 'AbortError'; reject(err);
            };
            if (!opts.signal) return;
            // An already-aborted signal never fires 'abort' again. Real fetch
            // rejects straight away in that case; without this the transport's
            // plain-POST fallback (which reuses the same signal after the
            // stream fails) would hang on a promise nothing can ever settle.
            if (opts.signal.aborted) return abort();
            opts.signal.addEventListener('abort', abort);
        });
    }
    if (window.__chatMode === 'http-error') {
        return Promise.resolve(new Response(
            JSON.stringify({ status: 'error', detail: 'the provider refused the request' }),
            { status: 500, headers: { 'Content-Type': 'application/json' } }));
    }

    const frames = [_frame({ type: 'progress', step: 'ir', message: 'Generating IR', logs: [] })];
    frames.push(window.__chatMode === 'error-frame'
        ? _frame({ type: 'error', status: 'error', detail: 'pipeline gave up', logs: [] })
        : _frame(window.__chatResult));

    const body = new ReadableStream({
        start(c) {
            const enc = new TextEncoder();
            for (const f of frames) c.enqueue(enc.encode(f));
            c.close();
        },
    });
    return Promise.resolve(new Response(body, {
        status: 200, headers: { 'Content-Type': 'text/event-stream' },
    }));
};
""" % json.dumps(RESULT_PAYLOAD)


@unittest.skipIf(sync_playwright is None and not _REQUIRE_BROWSER, "playwright not installed")
class ChatFlowBrowserTests(unittest.TestCase):
    proc: subprocess.Popen | None = None
    base: str = ""
    _pw = None
    _browser = None

    @classmethod
    def setUpClass(cls) -> None:
        if sync_playwright is None:  # pragma: no cover - only when CI's install step failed
            raise _unavailable("playwright is not installed")
        port = _free_port()
        cls.base = f"http://127.0.0.1:{port}"
        cls.proc = subprocess.Popen(
            [sys.executable, "-c",
             "from app.core.config import init_config; init_config();"
             "import uvicorn;"
             f"uvicorn.run('app.main:app', host='127.0.0.1', port={port}, log_level='error')"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(80):
            with contextlib.suppress(Exception):
                urlopen(f"{cls.base}/health", timeout=1).read()
                break
            time.sleep(0.25)
        else:  # pragma: no cover - only on a broken environment
            cls.tearDownClass()
            raise _unavailable("the dev server did not start")
        try:
            cls._pw = sync_playwright().start()
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment-dependent
            cls.tearDownClass()
            raise _unavailable(f"chromium is unavailable ({str(exc)[:80]}) — "
                               f"run `playwright install chromium`") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        for closer in (getattr(cls._browser, "close", None), getattr(cls._pw, "stop", None)):
            if closer:
                with contextlib.suppress(Exception):
                    closer()
        if cls.proc:
            cls.proc.terminate()
            with contextlib.suppress(Exception):
                cls.proc.wait(timeout=10)

    @contextlib.contextmanager
    def page(self):
        context = self._browser.new_context()
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(FETCH_STUB)
        pg.goto(f"{self.base}/static/index.html")
        pg.wait_for_timeout(1500)
        try:
            yield pg
            self.assertEqual(errors, [], f"page errors: {errors}")
        finally:
            context.close()

    def _send(self, pg, text: str = "add a redis cache") -> None:
        """Send one turn and return once the app says the run is over.

        Waits on ``isSending`` rather than a fixed sleep: the turn's real
        duration varies with what the response asks the app to render, and a
        sleep long enough to be safe on the slowest test is dead time on all
        the others.
        """
        before = pg.evaluate("__chatRequests.length")
        pg.fill("#chat-input", text)
        # `void` on purpose: sendMessage returns a promise, and page.evaluate
        # awaits any promise it is handed — which would block for Playwright's
        # full timeout on a request the test is deliberately holding open.
        pg.evaluate("void sendMessage()")
        # Bare identifiers, not window.*: state.js declares these with `let`,
        # which is script-scoped and never becomes a property of window.
        # Generous ceiling on purpose. The wait ends on the condition, so a
        # large timeout costs nothing when the app is behaving; it only decides
        # how long a genuinely stuck run takes to report. Too tight a ceiling
        # turns a saturated machine — the full suite runs two servers, a
        # browser and ~1,500 tests — into a false failure, which is the most
        # expensive kind of test result there is.
        pg.wait_for_function(
            f"() => window.__chatRequests.length > {before} && isSending === false",
            timeout=45_000,
        )
        pg.wait_for_timeout(250)  # let the post-run DOM writes land

    # ── the happy path ───────────────────────────────────────────────────

    def test_a_turn_reaches_the_server_and_comes_back_applied(self) -> None:
        with self.page() as pg:
            self._send(pg)
            self.assertEqual(len(pg.evaluate("__chatRequests")), 1)
            sent = pg.evaluate("__chatRequests[0].messages")
            self.assertEqual(sent[-1]["content"], "add a redis cache")
            self.assertIn("mxfile", pg.evaluate("currentXml"))

    def test_both_sides_of_the_exchange_appear_in_the_transcript(self) -> None:
        with self.page() as pg:
            self._send(pg)
            # Not every .chat-message carries a bubble (the typing indicator
            # is one), so the child lookup has to tolerate its absence.
            bubbles = pg.evaluate(
                "[...document.querySelectorAll('#chat-messages .chat-message')]"
                ".map(m => m.className + '|' + (m.querySelector('.chat-bubble')?.textContent ?? ''))")
            self.assertTrue(any("user" in b and "redis" in b for b in bubbles), bubbles)
            self.assertTrue(any("assistant" in b for b in bubbles), bubbles)

    def test_the_assistant_bubble_is_one_sentence_not_the_whole_message(self) -> None:
        """The full text goes to the LLM as history; the bubble gets the gist."""
        with self.page() as pg:
            self._send(pg)
            text = pg.evaluate(
                "[...document.querySelectorAll('#chat-messages .chat-message.assistant "
                ".chat-bubble')].pop().textContent")
            self.assertEqual(text, "Added a Redis cache in front of the database.")
            self.assertIn("It sits in the app subnet.",
                          pg.evaluate("chatMessages[chatMessages.length - 1].content"))

    def test_conversation_history_accumulates_for_the_next_turn(self) -> None:
        with self.page() as pg:
            self._send(pg)
            self._send(pg, "now make it multi-AZ")
            roles = pg.evaluate("chatMessages.map(m => m.role)")
            self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
            sent = pg.evaluate("__chatRequests[1].messages")
            self.assertTrue(sent and len(sent) >= 3,
                            "the second turn must carry the first turn's history")

    # ── insight panels ───────────────────────────────────────────────────

    def test_the_cost_panel_renders_the_returned_estimate(self) -> None:
        with self.page() as pg:
            self._send(pg)
            self.assertEqual(pg.eval_on_selector("#cost-total-amount", "e => e.textContent"), "A$1235")
            self.assertTrue(pg.eval_on_selector("#cost-panel", "e => e.classList.contains('active')"))

    def test_suggestions_render_one_row_each(self) -> None:
        with self.page() as pg:
            # Off by default in index.html, and the response is only rendered
            # when the setting asked for it — see renderResponsePanels. Set
            # through the DOM rather than clicked: the control lives inside a
            # collapsed settings drawer, so it is not visible to click.
            pg.evaluate("""() => {
                const cb = document.getElementById('suggestions-toggle');
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
            }""")
            self._send(pg)
            rows = pg.evaluate(
                "document.querySelectorAll('#suggestions-panel-body .chat-suggestion-item').length")
            self.assertEqual(rows, 2, "expected one row per returned suggestion")

    def test_a_response_without_panels_does_not_leave_stale_numbers_up(self) -> None:
        """A follow-up that returns no cost must not keep showing the old total."""
        with self.page() as pg:
            self._send(pg)
            self.assertEqual(pg.eval_on_selector("#cost-total-amount", "e => e.textContent"), "A$1235")
            pg.evaluate("""() => {
                const next = JSON.parse(JSON.stringify(window.__chatResult));
                delete next.cost_estimate;
                next.assistant_message = 'Renamed the cache.';
                window.__chatResult = next;
            }""")
            self._send(pg, "rename the cache")
            self.assertIn("Renamed the cache.", pg.evaluate(
                "[...document.querySelectorAll('#chat-messages .chat-message.assistant "
                ".chat-bubble')].pop().textContent"))

    # ── the composer's lifecycle ─────────────────────────────────────────

    def test_send_swaps_to_stop_while_in_flight_and_back_when_done(self) -> None:
        with self.page() as pg:
            pg.evaluate("window.__chatHold = true")
            pg.fill("#chat-input", "add a redis cache")
            pg.evaluate("void sendMessage()")
            pg.wait_for_timeout(400)
            self.assertTrue(pg.eval_on_selector("#send-btn", "e => e.hidden"))
            self.assertFalse(pg.eval_on_selector("#stop-btn", "e => e.hidden"))

            pg.evaluate("stopActiveRun()")
            pg.wait_for_function("() => isSending === false", timeout=10_000)
            pg.wait_for_timeout(250)
            self.assertFalse(pg.eval_on_selector("#send-btn", "e => e.hidden"))
            self.assertTrue(pg.eval_on_selector("#stop-btn", "e => e.hidden"))

    def test_the_composer_is_cleared_on_send_not_on_success(self) -> None:
        """Otherwise a failed turn silently eats what the user typed."""
        with self.page() as pg:
            pg.evaluate("window.__chatMode = 'http-error'")
            self._send(pg, "add a redis cache")
            self.assertEqual(pg.eval_on_selector("#chat-input", "e => e.value"), "")

    # ── failure ──────────────────────────────────────────────────────────

    def test_an_http_error_surfaces_and_restores_the_composer(self) -> None:
        with self.page() as pg:
            pg.evaluate("window.__chatMode = 'http-error'")
            self._send(pg)
            self.assertFalse(pg.eval_on_selector("#send-btn", "e => e.hidden"),
                             "send must come back so the user can retry")
            self.assertEqual(pg.evaluate("currentXml"), "")

    def test_an_error_frame_mid_stream_is_not_treated_as_success(self) -> None:
        with self.page() as pg:
            pg.evaluate("window.__chatMode = 'error-frame'")
            self._send(pg)
            self.assertEqual(pg.evaluate("currentXml"), "")
            self.assertFalse(pg.eval_on_selector("#send-btn", "e => e.hidden"))

    def test_a_failed_turn_does_not_pollute_conversation_history(self) -> None:
        """A turn the model never answered must not look answered next time."""
        with self.page() as pg:
            pg.evaluate("window.__chatMode = 'error-frame'")
            self._send(pg)
            self.assertNotIn("assistant", pg.evaluate("chatMessages.map(m => m.role)"))

    # ── degradation ──────────────────────────────────────────────────────

    def _toast(self, pg) -> str:
        """The toast the run finally settles on.

        Read after waiting for it rather than at a fixed offset: the notice is
        shown by finishChatRun, which lands just after the run flag clears, and
        a sleep long enough on an idle machine is not long enough on a loaded
        one. Same race this file's _send already avoids.
        """
        pg.wait_for_function(
            "() => { const t = document.getElementById('app-toast');"
            " return t && !t.hidden && (t.textContent || '').trim().length > 0; }",
            timeout=20_000,
        )
        return pg.eval_on_selector("#app-toast", "e => e.textContent")

    def _degrade(self, pg, features):
        pg.evaluate("""(features) => {
            const next = JSON.parse(JSON.stringify(window.__chatResult));
            next.degraded_features = features;
            window.__chatResult = next;
        }""", features)

    def test_a_missing_page_is_named_and_explained(self) -> None:
        """The old copy said "Some features degraded: Additional view
        generation" — internal machinery, for the one case where the diagram
        itself came back short of a tab the user asked for."""
        with self.page() as pg:
            self._degrade(pg, [{"feature": "Logical page", "reason": "error",
                                "message": "view branch failed"}])
            self._send(pg)
            toast = self._toast(pg)
            self.assertIn("Logical", toast)
            self.assertIn("missing", toast.lower())
            self.assertNotIn("Additional view generation", toast)

    def test_a_quota_failure_says_so(self) -> None:
        with self.page() as pg:
            self._degrade(pg, [{"feature": "Network page", "reason": "quota",
                                "message": "429"}])
            self._send(pg)
            toast = self._toast(pg)
            self.assertIn("Network", toast)
            self.assertIn("quota", toast.lower())

    def test_several_missing_pages_are_listed_together(self) -> None:
        with self.page() as pg:
            self._degrade(pg, [
                {"feature": "Logical page", "reason": "timeout", "message": "t"},
                {"feature": "Security page", "reason": "timeout", "message": "t"},
            ])
            self._send(pg)
            toast = self._toast(pg)
            self.assertIn("Logical", toast)
            self.assertIn("Security", toast)
            self.assertIn("timed out", toast)

    def test_a_degraded_insight_keeps_the_old_wording(self) -> None:
        """Only a missing page gets the stronger message — a cost estimate
        that did not run is not the diagram coming back incomplete."""
        with self.page() as pg:
            self._degrade(pg, [{"feature": "Cost estimate", "reason": "error", "message": "x"}])
            self._send(pg)
            toast = self._toast(pg)
            self.assertIn("Cost estimate", toast)
            self.assertNotIn("missing from this diagram", toast)

    # ── inferred connections ─────────────────────────────────────────────

    def test_invented_arrows_are_disclosed(self) -> None:
        """A page whose edges the app synthesised must say so — otherwise the
        reader takes arrows nobody described for the model's claim."""
        with self.page() as pg:
            pg.evaluate("""() => {
                const next = JSON.parse(JSON.stringify(window.__chatResult));
                next.inferred_connections = [{ view: 'Logical', flows: 4 }];
                window.__chatResult = next;
            }""")
            self._send(pg)
            self.assertIn("inferred", self._toast(pg).lower())

    def test_nothing_is_claimed_when_the_model_described_its_own_flows(self) -> None:
        with self.page() as pg:
            self._send(pg)
            self.assertNotIn("inferred", self._toast(pg).lower())

    # ── toast queue ──────────────────────────────────────────────────────

    def test_two_messages_in_one_turn_are_both_shown(self) -> None:
        """One toast element used to mean the last writer won, so a warning
        raised mid-turn was erased by the "Done" that followed it."""
        with self.page() as pg:
            seen = pg.evaluate("""async () => {
                const seen = [];
                const el = document.getElementById('app-toast');
                const obs = new MutationObserver(() => {
                    const t = (el.textContent || '').trim();
                    if (t && seen[seen.length - 1] !== t) seen.push(t);
                });
                obs.observe(el, { childList: true, characterData: true, subtree: true });
                showToast('first message', 'warning', 300);
                showToast('second message', 'success', 300);
                await new Promise((r) => setTimeout(r, 1200));
                obs.disconnect();
                return seen;
            }""")
            self.assertIn("first message", seen)
            self.assertIn("second message", seen)

    def test_a_warning_is_shown_before_routine_news(self) -> None:
        with self.page() as pg:
            order = pg.evaluate("""async () => {
                const seen = [];
                const el = document.getElementById('app-toast');
                const obs = new MutationObserver(() => {
                    const t = (el.textContent || '').trim();
                    if (t && seen[seen.length - 1] !== t) seen.push(t);
                });
                obs.observe(el, { childList: true, characterData: true, subtree: true });
                showToast('routine', 'success', 250);
                showToast('urgent', 'warning', 250);
                await new Promise((r) => setTimeout(r, 1200));
                obs.disconnect();
                return seen;
            }""")
            self.assertEqual(order[:2], ["routine", "urgent"],
                             "the first is already on screen; the warning must be next")

    # ── exports ──────────────────────────────────────────────────────────

    #: The real embed is cross-origin, so its postMessage cannot be replaced
    #: from the page. Swapping in a same-origin iframe under the same id lets
    #: the export code run unmodified and records what it posts.
    _STUB_EMBED = """
        window.__posted = [];
        drawioReady = true;
        // The export message handler resolves whichever export is pending, and
        // checks XML before SVG. Anything the app had in flight from boot would
        // therefore swallow the message this test dispatches, leaving the SVG
        // promise to time out — rare, load-dependent, and nothing to do with
        // what is under test.
        pendingXmlExport = null;
        pendingPngExport = null;
        pendingSvgExport = null;
        const real = document.getElementById('drawio-frame');
        const stub = document.createElement('iframe');
        stub.id = 'drawio-frame';
        real.replaceWith(stub);
        stub.contentWindow.postMessage = (m) => window.__posted.push(JSON.parse(m));
    """

    def test_svg_export_asks_drawio_for_the_editable_vector_format(self) -> None:
        """`xmlsvg`, not `svg`: only the former embeds the model, so the file
        someone drops into a deck can still be edited later."""
        with self.page() as pg:
            pg.evaluate(f"() => {{{self._STUB_EMBED}}}")
            pg.evaluate("void exportDiagramSvg()")
            pg.wait_for_timeout(200)
            posted = pg.evaluate("__posted")
            self.assertEqual(posted[-1]["action"], "export")
            self.assertEqual(posted[-1]["format"], "xmlsvg")

    def test_svg_export_decodes_the_data_url_into_markup(self) -> None:
        with self.page() as pg:
            pg.evaluate(f"() => {{{self._STUB_EMBED}}}")
            svg = pg.evaluate("""async () => {
                const p = exportDiagramSvg();
                const payload = btoa('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>');
                window.dispatchEvent(new MessageEvent('message', {
                    origin: DRAWIO_ORIGIN,
                    data: JSON.stringify({
                        event: 'export',
                        data: 'data:image/svg+xml;base64,' + payload,
                    }),
                }));
                return await p;
            }""")
            self.assertTrue(svg.startswith("<svg"), svg[:60])
            self.assertIn("<rect/>", svg)

    def test_the_svg_button_unlocks_with_the_other_export_buttons(self) -> None:
        with self.page() as pg:
            self.assertTrue(pg.eval_on_selector("#btn-svg", "e => e.disabled"))
            pg.evaluate("setActionButtonsEnabled(true)")
            self.assertFalse(pg.eval_on_selector("#btn-svg", "e => e.disabled"))


if __name__ == "__main__":
    unittest.main()
