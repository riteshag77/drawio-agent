/** Speech input, in two modes.
 *
 * **Dictation** (the original): the mic fills the composer, and the turn only
 * fires when the user says "send". Still reachable, and still the only thing
 * that works for someone who wants to mix typing and speech.
 *
 * **Voice mode** (the default for the mic button): a conversational loop. A
 * pause ends the turn, natural speech is routed rather than exact-matched, the
 * app speaks back, and the user can interrupt it mid-sentence.
 *
 * The two share one recognition object and one command registry. What differs
 * is who decides a turn is over, and whether the mic keeps listening while the
 * app talks.
 */

/** Field-tunable thresholds. Grouped because they interact — lengthening the
 *  endpoint delay makes barge-in feel slower, and loosening the echo overlap
 *  makes the app more likely to interrupt itself. */
const VOICE_TUNING = {
    /** Silence that ends a turn. Long enough to think mid-sentence, short
     *  enough that the user is not left wondering whether it heard them. */
    endpointMs: 1200,
    /** Words that must survive the echo filter before we treat speech during
     *  playback as a real interruption. One stray word must not stop the app
     *  mid-sentence. */
    bargeInMinWords: 2,
    /** Token overlap with what is currently being spoken, above which a
     *  transcript is assumed to be the mic hearing our own voice. */
    echoOverlap: 0.6,
    /** Self-echoes caught during one session before dropping to half-duplex.
     *
     *  The count is a *leading* indicator, not a failure count. If the room and
     *  hardware are not leaking, the filter never fires at all — so every hit
     *  is proof that our own playback is reaching the microphone, and the
     *  filter is one unlucky overlap score away from letting a phantom turn
     *  through. Degrading on the third hit trades barge-in (a convenience) for
     *  never talking to ourselves (a correctness bug that looks like the app
     *  possessed). */
    halfDuplexAfterEchoes: 3,
    /** Floor between spoken progress updates during a long generation. */
    progressSpeakMs: 8000,
};

/** off | listening | capturing | thinking | speaking */
let voiceState = 'off';
let voiceMode = false;

let speechRecognition = null;
let isListening = false;
let voiceBaseText = '';
let voiceManuallyStopping = false;
let voiceStatusBeforeListen = '';

let _voiceTurnBuffer = '';
let _voiceEndpointTimer = null;
let _voiceInterim = '';
/** Utterance buffered because it arrived while a turn was still running. */
let _voicePendingTurn = '';

// ── Spoken confirmation (text-to-speech) ───────────────────────────────────
// In dictation mode this stays opt-in and off by default — narrating every
// turn is intrusive for anyone typing normally, so only turns actually started
// by a voice command get spoken back. Voice mode forces it on: speech is the
// only channel a hands-free user has.
let voiceRepliesEnabled = localStorage.getItem('voiceRepliesEnabled') === '1';
let voiceInitiatedTurn = false;

function setVoiceRepliesEnabled(on) {
    voiceRepliesEnabled = !!on;
    localStorage.setItem('voiceRepliesEnabled', voiceRepliesEnabled ? '1' : '0');
}

function voiceRepliesActive() {
    return voiceMode || voiceRepliesEnabled;
}

function markVoiceInitiatedTurn() {
    voiceInitiatedTurn = true;
}

/** Read-and-clear so exactly one sendMessage() call is attributed to voice,
 *  never leaking into a later typed turn. In voice mode every turn is a voice
 *  turn, so this reports true without consuming anything — which is what keeps
 *  the existing speak/resume call sites in app.js working unchanged. */
function consumeVoiceInitiatedTurn() {
    if (voiceMode) return true;
    const was = voiceInitiatedTurn;
    voiceInitiatedTurn = false;
    return was;
}

let lastSpokenReply = '';
let _speechQueue = [];
let _speaking = false;

function getVoiceState() {
    return voiceState;
}

function setVoiceState(next) {
    if (voiceState === next) return;
    voiceState = next;
    renderVoiceHud();
}

// ── Speech output ─────────────────────────────────────────────────────────

/** Resolves once the utterance actually finishes (or immediately if replies
 *  are off/unsupported). Dictation callers that resume the mic afterward must
 *  wait for real playback to end, or the mic hears its own output. Voice mode
 *  never stops the mic, and relies on the echo filter instead. */
function speakVoiceReply(text) {
    if (!text) return Promise.resolve();
    if (!voiceRepliesActive()) return Promise.resolve();
    if (!('speechSynthesis' in window)) return Promise.resolve();
    lastSpokenReply = text;
    return _enqueueSpeech(text);
}

function _enqueueSpeech(text) {
    return new Promise((resolve) => {
        _speechQueue.push({ text, resolve });
        _drainSpeechQueue();
    });
}

function _drainSpeechQueue() {
    if (_speaking) return;
    const item = _speechQueue.shift();
    if (!item) {
        // Nothing left to say — hand the mic back to a plain listening state.
        if (voiceMode && voiceState === 'speaking') setVoiceState('listening');
        return;
    }
    _speaking = true;
    if (voiceMode) setVoiceState('speaking');
    try {
        const utter = new SpeechSynthesisUtterance(toSpokenText(item.text));
        utter.rate = 1.05;
        const done = () => {
            _speaking = false;
            item.resolve();
            _drainSpeechQueue();
        };
        utter.onend = done;
        utter.onerror = done;
        window.speechSynthesis.speak(utter);
    } catch (_) {
        // best-effort only — a TTS failure must never break the actual turn
        _speaking = false;
        item.resolve();
        _drainSpeechQueue();
    }
}

/** Drops everything pending, not just the sentence in flight — an interrupted
 *  user does not want the queue to carry on afterwards. */
function cancelVoiceSpeech() {
    const pending = _speechQueue.splice(0, _speechQueue.length);
    pending.forEach((item) => item.resolve());
    _speaking = false;
    if ('speechSynthesis' in window) {
        try { window.speechSynthesis.cancel(); } catch (_) {}
    }
}

/** Strips what reads fine but *sounds* wrong: a TTS voice pronounces markdown
 *  punctuation, cell ids and XML fragments literally. */
function toSpokenText(text) {
    return String(text || '')
        .replace(/<[^>]+>/g, ' ')
        .replace(/`{1,3}[^`]*`{1,3}/g, ' ')
        .replace(/[*_#>|]+/g, ' ')
        .replace(/\b[a-zA-Z]+[-_][0-9a-fA-F]{6,}\b/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

let _lastProgressSpokenAt = 0;

/** Narrates the pipeline while it runs. A fresh generation can take 30s+, and
 *  silence that long is indistinguishable from the app having died — but
 *  reading every step aloud would talk over the whole run, hence the floor. */
function speakVoiceProgress(message, { force = false } = {}) {
    if (!voiceMode || !message) return;
    const now = Date.now();
    if (!force && now - _lastProgressSpokenAt < VOICE_TUNING.progressSpeakMs) return;
    _lastProgressSpokenAt = now;
    _enqueueSpeech(message);
}

function resetVoiceProgressThrottle() {
    _lastProgressSpokenAt = 0;
}

// ── Echo filter ───────────────────────────────────────────────────────────

function _voiceTokens(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/[^\w\s-]/g, ' ')
        .split(/\s+/)
        .filter(Boolean);
}

/** True when a transcript heard during playback is probably our own voice.
 *
 * This is the load-bearing guess of voice mode. Barge-in needs the mic open
 * while the app speaks, and the Web Speech API opens its own capture stream —
 * it takes no getUserMedia constraints, so echoCancellation cannot be relied
 * on the way it could with a raw MediaStream. Comparing against what is
 * actually being said is the mitigation available at this layer.
 *
 * If this proves too leaky on real hardware, the escape hatch is half-duplex:
 * during playback accept only registry stop-phrases and discard everything
 * else. That loses conversational interruption but never loops on itself.
 */
function _isLikelySelfEcho(text) {
    const heard = _voiceTokens(text);
    if (!heard.length) return true;
    const spoken = _voiceTokens(lastSpokenReply);
    if (!spoken.length) return false;
    const spokenSet = new Set(spoken);
    const overlap = heard.filter((t) => spokenSet.has(t)).length / heard.length;
    const isEcho = overlap >= VOICE_TUNING.echoOverlap;
    if (isEcho) _noteSelfEcho();
    return isEcho;
}

/** Phrases that must still be obeyed while the app is talking, even after
 *  half-duplex has shut the rest of the microphone out. Mirrors the stop
 *  entries in voice-commands.js; the registry still *executes* them, this list
 *  only decides what is allowed through the gate. */
const _INTERRUPT_PHRASES = new Set([
    'stop', 'cancel', 'stop generation', 'cancel generation',
    'stop listening', 'stop voice', 'mic off', 'microphone off',
]);

let _echoHits = 0;
let _halfDuplex = false;

function isHalfDuplex() {
    return _halfDuplex;
}

/** Count a caught echo and drop to half-duplex once they stop looking like
 *  chance. Announced, because the user is about to lose the ability to
 *  interrupt by talking and silently removing that reads as a bug. */
function _noteSelfEcho() {
    if (_halfDuplex) return;
    _echoHits += 1;
    if (_echoHits < VOICE_TUNING.halfDuplexAfterEchoes) return;
    _halfDuplex = true;
    if (typeof appendVerboseLog === 'function') {
        appendVerboseLog('voice', 'Switched to half-duplex — own audio is reaching the mic',
            'warn', { echo_hits: _echoHits });
    }
    if (typeof showToast === 'function') {
        showToast('Your speakers are bleeding into the mic, so I\'ll stop listening '
            + 'while I talk. Say “stop” any time, or use headphones to keep '
            + 'interrupting me.', 'warning', 8000);
    }
}

/** Recomputed per session: plugging in headphones between sessions should win
 *  back barge-in without a reload. Three filtered echoes is a cheap price to
 *  re-learn, since they are caught correctly either way. */
function _resetDuplexLearning() {
    _echoHits = 0;
    _halfDuplex = false;
}

// ── Turn commit ───────────────────────────────────────────────────────────

function _clearEndpointTimer() {
    if (_voiceEndpointTimer) {
        clearTimeout(_voiceEndpointTimer);
        _voiceEndpointTimer = null;
    }
}

function _restartEndpointTimer() {
    _clearEndpointTimer();
    _voiceEndpointTimer = setTimeout(_commitVoiceTurn, VOICE_TUNING.endpointMs);
}

/** A pause ended the turn. Registry first (synchronous, zero latency — "stop"
 *  must never wait on a round-trip), then the router for everything else. */
async function _commitVoiceTurn() {
    _clearEndpointTimer();
    const utterance = _voiceTurnBuffer.trim();
    _voiceTurnBuffer = '';
    _voiceInterim = '';
    if (!utterance) return;

    if (typeof tryRunVoiceCommand === 'function') {
        const hit = tryRunVoiceCommand(utterance);
        if (hit && hit.handled) {
            renderVoiceHud();
            return;
        }
    }

    // A turn is already running: hold this one rather than dropping it, and
    // flush when the pipeline comes back. Talking over a 30s generation is
    // normal in a conversation and must not lose what was said.
    if (typeof isSending !== 'undefined' && isSending) {
        _voicePendingTurn = utterance;
        setVoiceStatus(true, 'Heard — I\'ll do that when this finishes');
        return;
    }

    // No filler here any more: routing is local and synchronous, so the
    // route's own line ("Okay, updating the diagram.") lands immediately.
    // A separate "Okay." on top of it just made the app talk twice.
    setVoiceState('thinking');

    const route = await routeVoiceUtterance(utterance);
    await _applyVoiceRoute(route, utterance);
}

async function _applyVoiceRoute(route, utterance) {
    if (!route) return;
    if (route.kind === 'ui_command') {
        const cmd = (typeof VOICE_COMMANDS !== 'undefined')
            ? VOICE_COMMANDS.find((c) => c.id === route.command_id)
            : null;
        if (cmd) {
            try {
                const guard = cmd.when ? cmd.when(null) : null;
                if (guard) {
                    speakVoiceReply(guard);
                    setVoiceStatus(true, guard);
                    return;
                }
                const result = cmd.run(null);
                const msg = (result && typeof result === 'string') ? result : route.speak;
                if (result && typeof result === 'object' && result.error) {
                    speakVoiceReply(result.error);
                    return;
                }
                speakVoiceReply(msg || 'Done.');
            } catch (err) {
                speakVoiceReply('That didn\'t work.');
            }
            return;
        }
        speakVoiceReply(route.speak || "I'm not sure how to do that.");
        return;
    }

    if (route.kind === 'diagram_turn') {
        const prompt = (route.prompt || utterance).trim();
        if (!prompt) return;
        if (route.speak) speakVoiceReply(route.speak);
        resetVoiceProgressThrottle();
        const input = document.getElementById('chat-input');
        if (input) {
            input.value = prompt;
            if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
        }
        if (typeof sendMessage === 'function') sendMessage();
        return;
    }

    // clarify — say why, and leave the words somewhere visible so a mishearing
    // is recoverable rather than simply lost.
    if (route.prompt) {
        const input = document.getElementById('chat-input');
        if (input) {
            input.value = route.prompt;
            if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
        }
    }
    speakVoiceReply(route.speak || "Sorry, I didn't catch that.");
}

/** Called by app.js when a turn finishes, so anything said mid-generation runs
 *  now instead of being dropped. */
function flushPendingVoiceTurn() {
    if (!voiceMode || !_voicePendingTurn) return;
    const pending = _voicePendingTurn;
    _voicePendingTurn = '';
    _voiceTurnBuffer = pending;
    _commitVoiceTurn();
}

// ── HUD ───────────────────────────────────────────────────────────────────

const _VOICE_STATE_LABELS = {
    listening: 'Listening…',
    capturing: 'Listening…',
    thinking: 'Thinking…',
    speaking: 'Speaking — just talk to interrupt',
};

/** The speaking hint promises barge-in, which half-duplex has taken away —
 *  leaving it up would have the user talking at an app that stopped listening. */
function _voiceStateLabel(state) {
    if (state === 'speaking' && _halfDuplex) return 'Speaking — say “stop” to cut in';
    return _VOICE_STATE_LABELS[state] || 'Voice mode on';
}

/** Shows state *and* the live transcript. With a pause committing the turn on
 *  its own, a silent mishearing is the main failure mode — seeing the words
 *  before they fire is what makes it recoverable. */
function renderVoiceHud() {
    const bar = document.getElementById('voice-status');
    const label = document.getElementById('voice-status-text');
    if (!bar) return;
    if (!voiceMode) {
        bar.removeAttribute('data-voice-state');
        return;
    }
    bar.hidden = false;
    bar.setAttribute('data-voice-state', voiceState);
    if (!label) return;
    const heard = (_voiceTurnBuffer + ' ' + _voiceInterim).trim();
    label.textContent = heard || _voiceStateLabel(voiceState);
}

function isVoiceSecureContext() {
    if (window.isSecureContext) return true;
    const h = location.hostname;
    return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '';
}

function setVoiceStatus(visible, text) {
    const bar = document.getElementById('voice-status');
    const label = document.getElementById('voice-status-text');
    if (!bar) return;
    if (text && label) label.textContent = text;
    bar.hidden = !visible;
}

function _voiceSyncComposerDisplay(interim) {
    const input = document.getElementById('chat-input');
    if (!input) return;
    const display = voiceBaseText
        ? (interim ? `${voiceBaseText} ${interim}`.trim() : voiceBaseText)
        : (interim || '').trim();
    input.value = display;
    if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
}

function isEditableForVoiceHotkey(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'select') return true;
    if (tag === 'input') {
        const type = (el.type || 'text').toLowerCase();
        return !['button', 'submit', 'checkbox', 'radio', 'file', 'reset', 'range', 'color', 'hidden', 'image'].includes(type);
    }
    return !!el.isContentEditable;
}

function initVoiceHotkeys() {
    if (initVoiceHotkeys._bound) return;
    initVoiceHotkeys._bound = true;
    document.addEventListener('keydown', (e) => {
        if (e.code !== 'Space' && e.key !== ' ') return;
        if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;

        // While the mic is on, Space always stops it (even if composer focused).
        if (isListening) {
            e.preventDefault();
            toggleVoiceInput();
            return;
        }

        // Idle: don't steal Space while typing in inputs/textareas.
        if (isEditableForVoiceHotkey(e.target)) return;

        e.preventDefault();
        toggleVoiceInput();
    });
}

// ── Recognition ───────────────────────────────────────────────────────────

/** Voice mode is the default for the mic button; dictation stays available via
 *  the settings toggle. Read once at init so a mid-session flip does not swap
 *  turn-taking rules out from under a running session. */
function voiceModePreferred() {
    return localStorage.getItem('voiceDictationOnly') !== '1';
}

function setVoiceDictationOnly(on) {
    localStorage.setItem('voiceDictationOnly', on ? '1' : '0');
}

function initVoiceInput() {
    const btn = document.getElementById('btn-voice');
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    initVoiceHotkeys();
    if (!SR) {
        if (btn) {
            btn.classList.add('voice-unsupported');
            btn.title = 'Voice input is not supported in this browser. Try Chrome or Edge.';
            btn.setAttribute('aria-label', 'Voice input not supported');
        }
        return;
    }
    if (btn) {
        btn.title = 'Voice mode: Space or click to start a spoken conversation';
        btn.setAttribute('aria-label', 'Voice mode: press Space or click to toggle');
    }
    const modeToggle = document.getElementById('voice-dictation-only-toggle');
    if (modeToggle) modeToggle.checked = !voiceModePreferred();
    speechRecognition = new SR();
    speechRecognition.continuous = true;
    speechRecognition.interimResults = true;
    speechRecognition.maxAlternatives = 1;
    speechRecognition.lang = navigator.language || 'en-US';

    speechRecognition.onstart = () => {
        isListening = true;
        voiceManuallyStopping = false;
        voiceBaseText = voiceMode ? '' : document.getElementById('chat-input').value.trim();
        voiceStatusBeforeListen = document.getElementById('diagram-status').textContent;
        if (btn) btn.classList.add('listening');
        if (voiceMode) {
            setVoiceState('listening');
            renderVoiceHud();
            setComposerRunStatus('Voice mode on — just talk. Space to end.', 'running');
            setDiagramStatus('Voice mode on');
        } else {
            setVoiceStatus(true, 'Listening… Space to stop · dictate or say a command');
            setComposerRunStatus('Listening… Space to stop, or say a command (try “help”)', 'running');
            setDiagramStatus('Listening… Space to stop');
        }
    };

    speechRecognition.onresult = (event) => {
        let interim = '';
        const finals = [];
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const piece = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                finals.push(piece);
            } else {
                interim += piece;
            }
        }
        if (voiceMode) {
            _onVoiceModeResult(finals, interim);
            return;
        }
        if (finals.length) {
            finals.forEach((chunk) => {
                const joined = String(chunk || '').trim();
                if (!joined) return;
                // Commands are matched on the *raw* chunk, before any cloud
                // vocabulary fixes: the registry's phrases are ordinary words
                // ("switch to aws"), and rewriting them first risks turning a
                // command into prose that no longer matches.
                if (typeof tryRunVoiceCommand === 'function') {
                    const result = tryRunVoiceCommand(joined);
                    if (result && result.handled) {
                        // Command consumed — do not append to composer.
                        _voiceSyncComposerDisplay('');
                        return;
                    }
                }
                // Dictation gets the same vocabulary correction voice mode
                // does. It used to land raw, so "add an easy two instance"
                // stayed exactly that and had to be hand-fixed every time —
                // the transcript is visible here, which made it defensible,
                // but not a reason to make the user do it.
                const text = (typeof normalizeCloudSpeech === 'function')
                    ? normalizeCloudSpeech(joined)
                    : joined;
                voiceBaseText = voiceBaseText ? `${voiceBaseText} ${text}`.trim() : text;
            });
        }
        _voiceSyncComposerDisplay(interim);
    };

    speechRecognition.onerror = (e) => {
        if (e.error === 'aborted' && voiceManuallyStopping) {
            voiceManuallyStopping = false;
            stopVoiceInput();
            return;
        }
        // Silence between turns is normal in a conversation, not an error —
        // the auto-restart in onend keeps the session alive.
        if (voiceMode && (e.error === 'no-speech' || e.error === 'aborted')) return;
        appendVerboseLog('client', `Voice input error: ${e.error}`, 'warn');
        let msg = '';
        switch (e.error) {
            case 'not-allowed':
            case 'service-not-allowed':
                msg = 'Microphone access was blocked. Click the lock icon in the address bar and allow the microphone, then try again.';
                break;
            case 'no-speech':
                msg = 'No speech detected. Try speaking a bit louder or closer to the mic.';
                break;
            case 'audio-capture':
                msg = 'No microphone was found. Please connect a microphone and try again.';
                break;
            case 'network':
                msg = 'Voice recognition needs an internet connection and a secure (https/localhost) page.';
                break;
            case 'aborted':
                break;
            default:
                msg = `Voice input error: ${e.error}`;
        }
        if (msg) showError(msg);
        stopVoiceInput();
    };

    speechRecognition.onend = () => {
        // Chrome ends the session on its own every so often; voice mode depends
        // on this restart, which is why the loop never actually stops.
        if (isListening && !voiceManuallyStopping) {
            try {
                speechRecognition.start();
            } catch (_) {
                stopVoiceInput();
            }
            return;
        }
        stopVoiceInput();
    };
}

/** Voice mode's result handler: echo filter, barge-in, endpointing. */
function _onVoiceModeResult(finals, interim) {
    const speakingNow = voiceState === 'speaking' || _speaking;

    if (speakingNow) {
        const candidate = (finals.join(' ') + ' ' + interim).trim();
        if (!candidate) return;

        // Half-duplex: the mic has been proven to hear our own playback, so
        // nothing said during it can be trusted as the user — except the one
        // thing that must always work. Checked against the phrase list rather
        // than the echo filter, because "stop" is short enough to plausibly
        // overlap whatever we are saying and get eaten as an echo.
        if (_halfDuplex) {
            if (!_INTERRUPT_PHRASES.has(normalizeVoiceText(candidate))) return;
            cancelVoiceSpeech();
            if (typeof tryRunVoiceCommand === 'function') tryRunVoiceCommand(candidate);
            setVoiceState('listening');
            return;
        }

        if (_isLikelySelfEcho(candidate)) return;
        if (_voiceTokens(candidate).length < VOICE_TUNING.bargeInMinWords) return;
        // Real speech over the top of playback — the user is interrupting.
        cancelVoiceSpeech();
        setVoiceState('capturing');
    }

    if (finals.length) {
        finals.forEach((chunk) => {
            const joined = String(chunk || '').trim();
            if (!joined) return;
            if (speakingNow && _isLikelySelfEcho(joined)) return;
            _voiceTurnBuffer = _voiceTurnBuffer ? `${_voiceTurnBuffer} ${joined}`.trim() : joined;
        });
    }
    _voiceInterim = interim || '';
    if (_voiceTurnBuffer || _voiceInterim) {
        if (voiceState === 'listening') setVoiceState('capturing');
        renderVoiceHud();
        _restartEndpointTimer();
    }
}

function stopVoiceInput() {
    isListening = false;
    _clearEndpointTimer();
    _voiceTurnBuffer = '';
    _voiceInterim = '';
    _voicePendingTurn = '';
    const wasVoiceMode = voiceMode;
    voiceMode = false;
    setVoiceState('off');
    cancelVoiceSpeech();
    const btn = document.getElementById('btn-voice');
    if (btn) btn.classList.remove('listening');
    const bar = document.getElementById('voice-status');
    if (bar) bar.removeAttribute('data-voice-state');
    setVoiceStatus(false);
    if (!isSending) {
        setComposerRunStatus(
            wasVoiceMode
                ? 'Voice mode off — type, or press Space to start talking again'
                : 'Ready — type, dictate (Space), or say a voice command',
            'info',
        );
        if (voiceStatusBeforeListen && currentXml) {
            setDiagramStatus(voiceStatusBeforeListen);
        } else if (currentXml) {
            setDiagramStatus('Diagram loaded — pan and zoom to explore');
        } else {
            setDiagramStatus('Waiting for generation');
        }
    }
}

/** Kept for the dictation path and for voice-commands.js's "stop listening". */
function resumeVoiceListening() {
    if (voiceMode) return; // never stopped in the first place
    if (isListening) return;
    if (typeof toggleVoiceInput !== 'function') return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || !isVoiceSecureContext()) return;
    toggleVoiceInput();
}

async function toggleVoiceInput() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || !speechRecognition) {
        showError('Voice input is not supported in this browser. Please use Chrome, Edge, or Safari.');
        return;
    }
    if (!isVoiceSecureContext()) {
        showError('Voice input requires a secure connection. Open the app via http://localhost:8000 (not a raw IP) or serve it over https.');
        return;
    }
    if (isListening) {
        voiceManuallyStopping = true;
        try { speechRecognition.stop(); } catch (_) {}
        return;
    }
    // Allow mic while generating so the user can say “stop”.
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        try {
            // echoCancellation is requested for the permission stream even
            // though SpeechRecognition opens its own capture — where the
            // browser applies it device-wide it reduces how much of our own
            // playback comes back, which is exactly what _isLikelySelfEcho
            // is otherwise left to clean up alone.
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            });
            stream.getTracks().forEach(t => t.stop());
        } catch (err) {
            showError('Microphone permission is required for voice input. Please allow access and try again.');
            appendVerboseLog('client', `getUserMedia denied: ${err.name}`, 'warn');
            return;
        }
    }
    voiceMode = voiceModePreferred();
    _voiceTurnBuffer = '';
    _voiceInterim = '';
    _voicePendingTurn = '';
    _resetDuplexLearning();
    resetVoiceProgressThrottle();
    const startRecognition = () => {
        speechRecognition.start();
        if (!voiceMode) document.getElementById('chat-input').focus();
    };
    try {
        startRecognition();
    } catch (err) {
        appendVerboseLog('client', `Voice start failed: ${err.message}`, 'warn');
        try { speechRecognition.stop(); } catch (_) {}
        setTimeout(() => {
            try {
                startRecognition();
            } catch (retryErr) {
                showError('Could not start voice input. Try again or type your message.');
                appendVerboseLog('client', `Voice retry failed: ${retryErr.message}`, 'warn');
            }
        }, 150);
    }
}
