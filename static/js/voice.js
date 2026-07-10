let speechRecognition = null;
let isListening = false;
let voiceBaseText = '';
let voiceManuallyStopping = false;
let voiceStatusBeforeListen = '';

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

function initVoiceInput() {
    const btn = document.getElementById('btn-voice');
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
        if (btn) {
            btn.classList.add('voice-unsupported');
            btn.title = 'Voice input is not supported in this browser. Try Chrome or Edge.';
        }
        return;
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
        setVoiceStatus(true, 'Listening… speak now');
        setComposerRunStatus('Listening… speak your instruction, click mic again to finish', 'running');
        setDiagramStatus('Listening… speak your instruction');
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
            const joined = finals.join('').trim();
            voiceBaseText = voiceBaseText ? `${voiceBaseText} ${joined}`.trim() : joined;
        }
        const input = document.getElementById('chat-input');
        const display = voiceBaseText
            ? (interim ? `${voiceBaseText} ${interim}`.trim() : voiceBaseText)
            : interim.trim();
        input.value = display;
        autoResizeChatInput();
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
        setComposerRunStatus('Ready — type or dictate your next message', 'info');
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
    if (isSending) {
        showError('Wait for the current generation to finish, or press Stop.');
        return;
    }
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
