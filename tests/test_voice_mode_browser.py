"""Voice mode's turn-taking, in a real browser.

The only frontend test in the suite, and it exists because voice mode is the
one piece of client code where a regression is *silent*: endpointing, the echo
filter and barge-in are async, timing-dependent state transitions with no
server round-trip to assert on. Everything else in static/js either renders
something a person would notice or is exercised through the API.

Hermetic by construction:

* ``SpeechRecognition`` and ``speechSynthesis`` are replaced with fakes, so no
  microphone, no network speech service, and no waiting on real audio.
* ``sendMessage`` is recorded rather than run, so no diagram is generated and
  no LLM is called. Routing itself is local (voice-router.js) and runs for
  real — there is nothing to stub.

Skips (never fails) when Playwright is not installed, so `pytest` stays green
on a machine that only wants the Python suite. Install with:

    pip install playwright && playwright install chromium

That leniency is wrong in CI, where a skip is indistinguishable from a pass and
the job would go green having tested nothing. Set ``REQUIRE_BROWSER_TESTS=1``
(the `frontend` job does) to turn every "browser unavailable" path into a
failure instead.
"""

from __future__ import annotations

import contextlib
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

#: CI sets this. A browser job that skips is a green check proving nothing.
_REQUIRE_BROWSER = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"


def _unavailable(reason: str) -> Exception:
    """Skip locally, fail where the browser was promised."""
    if _REQUIRE_BROWSER:
        return RuntimeError(f"REQUIRE_BROWSER_TESTS=1 but {reason}")
    return unittest.SkipTest(reason)


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

// Record sends rather than running a real generation. Routing is local,
// so nothing else needs intercepting.
window.__sent = [];
window.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        window.sendMessage = function () {
            window.__sent.push(document.getElementById('chat-input').value);
            return Promise.resolve();
        };
    }, 300);
});
"""


@unittest.skipIf(sync_playwright is None and not _REQUIRE_BROWSER, "playwright not installed")
class VoiceModeBrowserTests(unittest.TestCase):
    proc: subprocess.Popen | None = None
    base: str = ""
    _pw = None
    _browser = None

    @classmethod
    def setUpClass(cls) -> None:
        if sync_playwright is None:  # pragma: no cover - only when CI's install step failed
            raise _unavailable("playwright is not installed")
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
            raise _unavailable("the dev server did not start")
        # Importing playwright is not the same as being able to launch it: the
        # browser binary comes from a separate `playwright install chromium`
        # step. A developer who only ran `pip install` has the package but no
        # binary, so treat a failed launch as "not available" rather than
        # letting it error the run — unless CI promised the binary is there.
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
    def page(self, *, dictation_only: bool = False):
        # A fresh context per test, so localStorage from one does not leak
        # into the next — the dictation-mode test depends on that.
        context = self._browser.new_context()
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(BROWSER_STUBS)
        if dictation_only:
            # Top frame only, and swallow denial. An init script runs in
            # *every* frame, including the cross-origin draw.io embed where
            # touching localStorage throws — which surfaces as a page error
            # and fails the assertion at the end of this context manager.
            pg.add_init_script(
                "try { if (window.top === window) "
                "localStorage.setItem('voiceDictationOnly','1'); } catch (e) {}"
            )
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
            self._start_voice(pg)
            self.assertEqual(pg.evaluate("getVoiceState()"), "listening")

            pg.evaluate("__emit('add a redis cache', true)")
            pg.wait_for_timeout(600)
            self.assertEqual(pg.evaluate("__sent").__len__(), 0,
                             "fired before the endpoint delay elapsed")

            # Wait for the turn to fire rather than for a duration. The
            # negative check above has to be a fixed wait — it asserts nothing
            # happened *yet* — but this one asserts something eventually does,
            # and on a loaded machine the endpoint timer plus the work behind
            # it can outlast any sleep short enough to be worth writing.
            pg.wait_for_function("() => __sent.length > 0", timeout=30_000)
            # Routed locally: vocabulary corrected, shaped into an instruction,
            # and fired — with no network call in the path at all.
            self.assertEqual(pg.evaluate("__sent"), ["Add a Redis cache."])

    def test_the_live_transcript_is_visible_before_it_fires(self) -> None:
        """Auto-send makes a silent mishearing the main failure mode; seeing
        the words before they commit is what makes it recoverable."""
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("__emit('make the database multi-AZ', false)")
            pg.wait_for_timeout(200)
            self.assertEqual(pg.evaluate("getVoiceState()"), "capturing")
            self.assertEqual(
                pg.eval_on_selector("#voice-status-text", "s => s.textContent"),
                "make the database multi-AZ")
            # and it must not pollute the composer on the way
            self.assertEqual(pg.eval_on_selector("#chat-input", "s => s.value"), "")

    def test_an_exact_phrase_runs_as_a_command_not_a_diagram_turn(self) -> None:
        """The registry claims it first, so it never becomes a prompt."""
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("__emit('clear chat', true)")
            pg.wait_for_timeout(2200)
            self.assertEqual(pg.evaluate("__sent"), [])

    def test_routing_makes_no_network_request(self) -> None:
        """Latency is the whole point: a spoken turn must not wait on a server
        to find out what it meant."""
        with self.page() as pg:
            pg.evaluate("""() => {
                window.__fetches = [];
                const of = window.fetch;
                window.fetch = function (u) { window.__fetches.push(String(u)); return of.apply(this, arguments); };
            }""")
            self._start_voice(pg)
            pg.evaluate("__emit('add an easy two instance behind the load balancer', true)")
            pg.wait_for_function("() => __sent.length > 0", timeout=30_000)
            self.assertEqual(pg.evaluate("__fetches"), [])
            self.assertEqual(pg.evaluate("__sent"),
                             ["Add an EC2 instance behind the load balancer."])

    def test_jargon_is_corrected_locally(self) -> None:
        cases = {
            "add an easy two instance": "Add an EC2 instance.",
            "put it in the v p c": "Put it in the VPC.",
            "make the are d s database multi a z": "Make the RDS database Multi-AZ.",
            "store files in s three": "Store files in S3.",
            "add a redis cache": "Add a Redis cache.",
        }
        with self.page() as pg:
            for said, want in cases.items():
                got = pg.evaluate("(t) => routeVoiceUtterance(t).then(r => r.prompt)", said)
                self.assertEqual(got, want, f"for {said!r}")

    def test_ordinary_speech_is_not_rewritten_into_infrastructure(self) -> None:
        """"easy to" is English. Turning it into EC2 would corrupt normal
        sentences, so it only converts when a compute noun follows."""
        with self.page() as pg:
            got = pg.evaluate(
                "() => routeVoiceUtterance('make it easy to read the diagram').then(r => r.prompt)")
            self.assertNotIn("EC2", got)

    # ── duplex ───────────────────────────────────────────────────────────

    def _start_voice(self, pg, *, dictation: bool = False) -> None:
        """Turn the mic on and wait until the app has actually got there.

        Recognition start is asynchronous: the fake fires ``onstart`` on a
        timer, and voice mode's handler is what moves the state to
        ``listening``. A fixed sleep here was a race — under a loaded machine
        (the full suite runs two servers and a browser) that callback can land
        *after* the wait, so a test that had already forced state to
        ``speaking`` would see it stomped back to ``listening``. That is
        exactly the shape of the one intermittent failure seen in a full-suite
        run, and it is the harness racing the app, not a defect in either.

        Waiting on the state the app reports removes the guess.
        """
        pg.evaluate("void toggleVoiceInput()")
        # Dictation mode drives the composer, not the conversational state
        # machine, so `voiceState` legitimately stays 'off' there — `isListening`
        # is the only signal both modes share.
        condition = (
            "() => isListening === true" if dictation
            else "() => isListening === true && getVoiceState() === 'listening'"
        )
        pg.wait_for_function(condition, timeout=30_000)

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
            self._start_voice(pg)
            self._start_speaking(pg, "Adding an EC2 instance behind the load balancer.")
            pg.evaluate("__emit('adding an EC2 instance behind the load balancer', true)")
            pg.wait_for_timeout(250)
            self.assertEqual(pg.evaluate("__cancels"), 0, "own speech interrupted playback")
            self.assertEqual(pg.evaluate("_voiceTurnBuffer"), "")
            self.assertEqual(pg.evaluate("getVoiceState()"), "speaking")

    def test_real_speech_over_playback_interrupts(self) -> None:
        with self.page() as pg:
            self._start_voice(pg)
            self._start_speaking(pg, "Adding an EC2 instance behind the load balancer.")
            pg.evaluate("__emit('no wait remove that', true)")
            pg.wait_for_timeout(250)
            self.assertGreaterEqual(pg.evaluate("__cancels"), 1, "playback was not cancelled")
            self.assertEqual(pg.evaluate("_voiceTurnBuffer"), "no wait remove that")

    def test_repeated_echo_degrades_to_half_duplex(self) -> None:
        """The echo filter is a guess about hardware we cannot test against.

        Rather than trust it, the app watches its own hit rate: a filter that
        keeps firing proves playback IS reaching the mic, so it stops listening
        during playback instead of waiting to be fooled.
        """
        with self.page() as pg:
            self._start_voice(pg)
            self.assertFalse(pg.evaluate("isHalfDuplex()"))

            spoken = "Adding an EC2 instance behind the load balancer."
            for _ in range(3):
                self._start_speaking(pg, spoken)
                pg.evaluate("__emit('adding an EC2 instance behind the load balancer', true)")
                pg.wait_for_timeout(120)
            self.assertTrue(pg.evaluate("isHalfDuplex()"),
                            "kept trusting the filter after repeated echoes")

            # Real speech no longer interrupts...
            self._start_speaking(pg, spoken)
            pg.evaluate("__cancels = 0")
            pg.evaluate("__emit('no wait remove that', true)")
            pg.wait_for_timeout(200)
            self.assertEqual(pg.evaluate("__cancels"), 0, "barge-in survived half-duplex")
            self.assertEqual(pg.evaluate("_voiceTurnBuffer"), "")

    def test_stop_still_cuts_through_half_duplex(self) -> None:
        """Half-duplex shuts the mic out during playback — but a user who says
        "stop" and is ignored has an app that will not shut up."""
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("_halfDuplex = true")

            self._start_speaking(pg, "Generating the diagram now.")
            pg.evaluate("__cancels = 0")
            pg.evaluate("__emit('stop', true)")
            pg.wait_for_timeout(250)
            self.assertGreaterEqual(pg.evaluate("__cancels"), 1, "'stop' was swallowed")

    def test_the_hud_stops_promising_barge_in(self) -> None:
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("_halfDuplex = true; _voiceTurnBuffer=''; _voiceInterim='';"
                        "setVoiceState('speaking'); renderVoiceHud();")
            pg.wait_for_timeout(120)
            label = pg.eval_on_selector("#voice-status-text", "s => s.textContent")
            self.assertIn("stop", label.lower())
            self.assertNotIn("just talk", label.lower())

    def test_duplex_learning_resets_between_sessions(self) -> None:
        """Plugging in headphones should win barge-in back without a reload."""
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("_halfDuplex = true; _echoHits = 9")
            pg.evaluate("_speaking = false; toggleVoiceInput()")
            pg.wait_for_timeout(400)
            self._start_voice(pg)
            self.assertFalse(pg.evaluate("isHalfDuplex()"))

    def test_a_single_stray_word_does_not_interrupt(self) -> None:
        with self.page() as pg:
            self._start_voice(pg)
            self._start_speaking(pg, "Generating the diagram now.")
            pg.evaluate("__emit('hmm', true)")
            pg.wait_for_timeout(250)
            self.assertEqual(pg.evaluate("__cancels"), 0)

    # ── modes ────────────────────────────────────────────────────────────

    def test_leaving_voice_mode_resets_everything(self) -> None:
        with self.page() as pg:
            self._start_voice(pg)
            pg.evaluate("_speaking = false; toggleVoiceInput()")
            pg.wait_for_timeout(500)
            self.assertEqual(pg.evaluate("getVoiceState()"), "off")
            self.assertFalse(pg.evaluate("voiceMode"))
            self.assertTrue(pg.eval_on_selector("#voice-status", "s => s.hidden"))

    def test_dictation_gets_the_same_vocabulary_correction(self) -> None:
        """Dictation used to land the raw transcript, so "easy two" had to be
        hand-fixed every time. The text being visible made that defensible, not
        a reason to make the user do it."""
        with self.page(dictation_only=True) as pg:
            self._start_voice(pg, dictation=True)
            pg.evaluate("__emit('add an easy two instance and an are d s database', true)")
            pg.wait_for_timeout(600)
            self.assertEqual(pg.eval_on_selector("#chat-input", "s => s.value"),
                             "add an EC2 instance and an RDS database")

    def test_dictation_commands_match_before_correction(self) -> None:
        """Commands are matched on the raw chunk — rewriting "switch to aws"
        first could turn a command into prose that no longer matches."""
        with self.page(dictation_only=True) as pg:
            self._start_voice(pg, dictation=True)
            pg.evaluate("selectPlatform('gcp')")
            pg.evaluate("__emit('switch to aws', true)")
            pg.wait_for_timeout(600)
            self.assertEqual(pg.evaluate("selectedPlatform"), "aws")
            # consumed as a command, not typed into the composer
            self.assertEqual(pg.eval_on_selector("#chat-input", "s => s.value"), "")

    def test_dictation_mode_still_waits_for_send(self) -> None:
        """Voice mode is the default, but dictation is the only thing that
        works for someone mixing typing and speech — it must not regress."""
        with self.page(dictation_only=True) as pg:
            self._start_voice(pg, dictation=True)
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
