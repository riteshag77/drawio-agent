let speechRecognition = null;
let isListening = false;
let voiceBaseText = '';
let voiceManuallyStopping = false;
let voiceStatusBeforeListen = '';

// ── Spoken confirmation (text-to-speech) ───────────────────────────────────
// Opt-in and off by default — narrating every turn is intrusive for anyone
// typing normally. Only turns actually started by a voice command (quick
// edit / send) get spoken back, so this stays useful for hands-free use
// without talking over a user who's just clicking around.
let voiceRepliesEnabled = localStorage.getItem('voiceRepliesEnabled') === '1';
let voiceInitiatedTurn = false;

function setVoiceRepliesEnabled(on) {
    voiceRepliesEnabled = !!on;
    localStorage.setItem('voiceRepliesEnabled', voiceRepliesEnabled ? '1' : '0');
}

function markVoiceInitiatedTurn() {
    voiceInitiatedTurn = true;
}

/** Read-and-clear so exactly one sendMessage() call is attributed to voice,
 *  never leaking into a later typed turn. */
function consumeVoiceInitiatedTurn() {
    const was = voiceInitiatedTurn;
    voiceInitiatedTurn = false;
    return was;
}

function speakVoiceReply(text) {
    if (!voiceRepliesEnabled || !text) return;
    if (!('speechSynthesis' in window)) return;
    try {
        window.speechSynthesis.cancel(); // don't queue behind a stale utterance
        const utter = new SpeechSynthesisUtterance(text);
        utter.rate = 1.05;
        window.speechSynthesis.speak(utter);
    } catch (_) {
        // best-effort only — a TTS failure must never break the actual turn
    }
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

        // While mic is on, Space always stops it (even if composer is focused).
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
        btn.title = 'Voice: Space or click to toggle (dictate or say a command; say “help”)';
        btn.setAttribute('aria-label', 'Voice: press Space or click to toggle microphone');
    }
    speechRecognition = new SR();
    speechRecognition.continuous = true;
    speechRecognition.interimResults = true;
    speechRecognition.maxAlternatives = 1;
    speechRecognition.lang = navigator.language || 'en-US';

    speechRecognition.onstart = () => {
        isListening = true;
        voiceManuallyStopping = false;
        voiceBaseText = document.getElementById('chat-input').value.trim();
        voiceStatusBeforeListen = document.getElementById('diagram-status').textContent;
        if (btn) btn.classList.add('listening');
        setVoiceStatus(true, 'Listening… Space to stop · dictate or say a command');
        setComposerRunStatus('Listening… Space to stop, or say a command (try “help”)', 'running');
        setDiagramStatus('Listening… Space to stop');
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
        if (finals.length) {
            finals.forEach((chunk) => {
                const joined = String(chunk || '').trim();
                if (!joined) return;
                if (typeof tryRunVoiceCommand === 'function') {
                    const result = tryRunVoiceCommand(joined);
                    if (result && result.handled) {
                        // Command consumed — do not append to composer.
                        _voiceSyncComposerDisplay('');
                        return;
                    }
                }
                voiceBaseText = voiceBaseText ? `${voiceBaseText} ${joined}`.trim() : joined;
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

function stopVoiceInput() {
    isListening = false;
    const btn = document.getElementById('btn-voice');
    if (btn) btn.classList.remove('listening');
    setVoiceStatus(false);
    if (!isSending) {
        setComposerRunStatus('Ready — type, dictate (Space), or say a voice command', 'info');
        if (voiceStatusBeforeListen && (currentXml || currentMermaid)) {
            setDiagramStatus(voiceStatusBeforeListen);
        } else if (currentXml || currentMermaid) {
            setDiagramStatus('Diagram loaded — pan and zoom to explore');
        } else {
            setDiagramStatus('Waiting for generation');
        }
    }
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
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach(t => t.stop());
        } catch (err) {
            showError('Microphone permission is required for voice input. Please allow access and try again.');
            appendVerboseLog('client', `getUserMedia denied: ${err.name}`, 'warn');
            return;
        }
    }
    const startRecognition = () => {
        speechRecognition.start();
        document.getElementById('chat-input').focus();
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
