"""Voice mode's turn-taking, in a real browser.

The only frontend test in the suite, and it exists because voice mode is the
one piece of client code where a regression is *silent*: endpointing, the echo
filter and barge-in are async, timing-dependent state transitions with no
server round-trip to assert on. Everything else in static/js either renders
something a person would notice or is exercised through the API.

Hermetic by construction:

* ``SpeechRecognition`` and ``speechSynthesis`` are replaced with fakes, so no
  microphone, no network speech service, and no waiting on real audio.
* ``POST /voice/route`` is stubbed, so no LLM call, no API key, no cost. What
  is under test is how the browser *reacts* to a route, not the routing —
  tests/test_voice_intent.py covers the routing itself.

Skips (never fails) when Playwright is not installed, so `pytest` stays green
on a machine that only wants the Python suite. Install with:

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import contextlib
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


#: Injected before any page script runs. Fakes the two Web Speech APIs and the
#: router endpoint, and records what the app tried to do.
BROWSER_STUBS = r"""
class FakeSR {
    constructor() {
        this.continuous = false; this.interimResults = false;
        this._results = []; this._started = false;
        window.__sr = this;
    }
    start() {
        if (this._started) throw new Error('already started');
        this._started = true;
        setTimeout(() => this.onstart && this.onstart(), 0);
    }
    stop() { this._started = false; setTimeout(() => this.onend && this.onend(), 0); }
    abort() { this.stop(); }
}
window.SpeechRecognition = FakeSR;
window.webkitSpeechRecognition = FakeSR;

/** Feed one recognition result through, shaped exactly as Chrome shapes it. */
window.__emit = (text, isFinal) => {
    const sr = window.__sr;
    if (!sr || !sr.onresult) return 'no recognition';
    const idx = sr._results.length;
    sr._results.push({ 0: { transcript: text }, isFinal: !!isFinal, length: 1 });
    sr.onresult({ resultIndex: idx, results: sr._results });
    return 'ok';
};

window.__spoken = [];
window.__cancels = 0;
window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
// speechSynthesis is a read-only accessor on window — a plain assignment is
// silently ignored, which once made this harness "pass" with no fake at all.
Object.defineProperty(window, 'speechSynthesis', {
    configurable: true, writable: true,
    value: {
        speak(u) { window.__spoken.push(u.text); window.__utter = u; },
        cancel() { window.__cancels++; window.__utter = null; },
    },
});

navigator.mediaDevices = navigator.mediaDevices || {};
navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [] });

// Stub the router and record sends, so a turn costs no LLM call.
window.__routed = [];
window.__sent = [];
window.__routeReply = { kind: 'diagram_turn', prompt: 'Add a Redis cache.', speak: 'Adding a cache.' };
window.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        const realFetch = window.fetch;
        window.fetch = function (u, o) {
            if (String(u).includes('/voice/route')) {
                window.__routed.push(JSON.parse(o.body));
                return Promise.resolve(new Response(JSON.stringify(window.__routeReply),
                    { status: 200, headers: { 'Content-Type': 'application/json' } }));
            }
            return realFetch.apply(this, arguments);
        };
        window.sendMessage = function () {
            window.__sent.push(document.getElementById('chat-input').value);
            return Promise.resolve();
        };
    }, 300);
});
"""


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class VoiceModeBrowserTests(unittest.TestCase):
    proc: subprocess.Popen | None = None
    base: str = ""
    _pw = None
    _browser = None

    @classmethod
    def setUpClass(cls) -> None:
        # One server and one browser for the whole class. Launching Chromium
        # per test costs ~15s each and a slow suite is a suite people skip;
        # a fresh *page* per test is enough isolation, since all app state
        # lives in the page.
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
            raise unittest.SkipTest("dev server did not start")
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

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
    def page(self, *, dictation_only: bool = False):
        # A fresh context per test, so localStorage from one does not leak
        # into the next — the dictation-mode test depends on that.
        context = self._browser.new_context()
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(BROWSER_STUBS)
        if dictation_only:
            pg.add_init_script("localStorage.setItem('voiceDictationOnly','1')")
        pg.goto(f"{self.base}/static/index.html")
        pg.wait_for_timeout(2000)
        try:
            yield pg
            self.assertEqual(errors, [], f"page errors: {errors}")
        finally:
            context.close()

    # ── turn-taking ──────────────────────────────────────────────────────

    def test_a_pause_ends_the_turn_without_a_send_command(self) -> None:
        """The core of voice mode: nobody says "send"."""
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self.assertEqual(pg.evaluate("getVoiceState()"), "listening")

            pg.evaluate("__emit('add a redis cache', true)")
            pg.wait_for_timeout(600)
            self.assertEqual(pg.evaluate("__sent").__len__(), 0,
                             "fired before the endpoint delay elapsed")

            pg.wait_for_timeout(2500)
            self.assertEqual(pg.evaluate("__sent"), ["Add a Redis cache."])

    def test_the_live_transcript_is_visible_before_it_fires(self) -> None:
        """Auto-send makes a silent mishearing the main failure mode; seeing
        the words before they commit is what makes it recoverable."""
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            pg.evaluate("__emit('make the database multi-AZ', false)")
            pg.wait_for_timeout(200)
            self.assertEqual(pg.evaluate("getVoiceState()"), "capturing")
            self.assertEqual(
                pg.eval_on_selector("#voice-status-text", "s => s.textContent"),
                "make the database multi-AZ")
            # and it must not pollute the composer on the way
            self.assertEqual(pg.eval_on_selector("#chat-input", "s => s.value"), "")

    def test_an_exact_phrase_never_reaches_the_router(self) -> None:
        """"stop" waiting on a network round-trip is not a stop."""
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            pg.evaluate("__emit('clear chat', true)")
            pg.wait_for_timeout(2200)
            self.assertEqual(pg.evaluate("__routed"), [])

    # ── duplex ───────────────────────────────────────────────────────────

    def _start_speaking(self, pg, spoken: str) -> None:
        pg.evaluate(f"""
            cancelVoiceSpeech(); __cancels = 0; _voiceTurnBuffer = '';
            lastSpokenReply = {spoken!r};
            setVoiceState('speaking'); _speaking = true;
        """)

    def test_the_app_does_not_hear_itself(self) -> None:
        """Barge-in needs the mic open during playback, and Web Speech opens
        its own capture stream that takes no echoCancellation constraint — so
        this filter is the only thing standing between the app and a loop."""
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self._start_speaking(pg, "Adding an EC2 instance behind the load balancer.")
            pg.evaluate("__emit('adding an EC2 instance behind the load balancer', true)")
            pg.wait_for_timeout(250)
            self.assertEqual(pg.evaluate("__cancels"), 0, "own speech interrupted playback")
            self.assertEqual(pg.evaluate("_voiceTurnBuffer"), "")
            self.assertEqual(pg.evaluate("getVoiceState()"), "speaking")

    def test_real_speech_over_playback_interrupts(self) -> None:
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self._start_speaking(pg, "Adding an EC2 instance behind the load balancer.")
            pg.evaluate("__emit('no wait remove that', true)")
            pg.wait_for_timeout(250)
            self.assertGreaterEqual(pg.evaluate("__cancels"), 1, "playback was not cancelled")
            self.assertEqual(pg.evaluate("_voiceTurnBuffer"), "no wait remove that")

    def test_a_single_stray_word_does_not_interrupt(self) -> None:
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self._start_speaking(pg, "Generating the diagram now.")
            pg.evaluate("__emit('hmm', true)")
            pg.wait_for_timeout(250)
            self.assertEqual(pg.evaluate("__cancels"), 0)

    # ── modes ────────────────────────────────────────────────────────────

    def test_leaving_voice_mode_resets_everything(self) -> None:
        with self.page() as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            pg.evaluate("_speaking = false; toggleVoiceInput()")
            pg.wait_for_timeout(500)
            self.assertEqual(pg.evaluate("getVoiceState()"), "off")
            self.assertFalse(pg.evaluate("voiceMode"))
            self.assertTrue(pg.eval_on_selector("#voice-status", "s => s.hidden"))

    def test_dictation_mode_still_waits_for_send(self) -> None:
        """Voice mode is the default, but dictation is the only thing that
        works for someone mixing typing and speech — it must not regress."""
        with self.page(dictation_only=True) as pg:
            pg.evaluate("toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self.assertFalse(pg.evaluate("voiceMode"))
            self.assertTrue(pg.evaluate("isListening"))

            pg.evaluate("__emit('build a three tier app on AWS', true)")
            pg.wait_for_timeout(2200)
            self.assertEqual(pg.eval_on_selector("#chat-input", "s => s.value"),
                             "build a three tier app on AWS")
            self.assertEqual(pg.evaluate("__sent"), [], "auto-sent in dictation mode")

            pg.evaluate("__emit('send message', true)")
            pg.wait_for_timeout(900)
            self.assertEqual(len(pg.evaluate("__sent")), 1)


if __name__ == "__main__":
    unittest.main()
