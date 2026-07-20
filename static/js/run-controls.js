/** Run controls — send/stop buttons, progress bar, composer status, finishChatRun. */

function setComposerRunStatus(text, level = 'info') {
    if (!text) return;
    if (!verboseLogging) return;
    const levelMap = { error: 'error', running: 'info', success: 'success', stopped: 'warn', info: 'info' };
    appendVerboseLog('run', text, levelMap[level] || 'info');
}

function showError(msg) {
    setComposerRunStatus(msg, 'error');
    if (typeof showToast === 'function') showToast(msg, 'error');
}

function hideError() {
    // Merged into pipeline logs — no separate panel needed
}

function updateSendStopButtons() {
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const voiceBtn = document.getElementById('btn-voice');
    if (sendBtn) {
        sendBtn.hidden = isSending;
        sendBtn.disabled = isSending;
    }
    if (stopBtn) stopBtn.hidden = !isSending;
    if (voiceBtn) voiceBtn.disabled = isSending;
}

function stopActiveRun() {
    if (!isSending || !chatAbortController) return;
    runStoppedByUser = true;
    setComposerRunStatus('Stopping…', 'stopped');
    setDiagramStatus('Stopping generation…');
    chatAbortController.abort();
}

function finishChatRun({ outcome = 'idle', message = '', diagramMessage = '' } = {}) {
    setTyping(false);
    document.getElementById('diagram-loading').classList.remove('active');
    document.getElementById('progress-track').classList.remove('active');
    document.getElementById('progress-bar').style.width = '0%';

    if (message) {
        const level = outcome === 'success' ? 'success'
            : outcome === 'error' ? 'error'
            : outcome === 'stopped' ? 'warn'
            : 'info';
        setComposerRunStatus(message, level);
        if (typeof showToast === 'function' && (outcome === 'success' || outcome === 'error' || outcome === 'stopped')) {
            showToast(message, outcome === 'stopped' ? 'info' : outcome);
        }
    }

    if (diagramMessage) {
        setDiagramStatus(diagramMessage);
    } else if (outcome === 'stopped') {
        setDiagramStatus('Stopped — ready for your next message');
    } else if (outcome === 'error') {
        setDiagramStatus('Generation failed');
    } else if (outcome === 'success' && !diagramMessage) {
        setDiagramStatus('Rendering diagram…');
    } else if (outcome === 'idle' && !isSending) {
        if (currentXml || currentMermaid) {
            setDiagramStatus('Diagram loaded — pan and zoom to explore');
        } else {
            setDiagramStatus('Waiting for generation');
        }
    }
}

function setProgress(percent) {
    const track = document.getElementById('progress-track');
    track.classList.add('active');
    document.getElementById('progress-bar').style.width = percent + '%';
    if (percent >= 100) {
        setTimeout(() => track.classList.remove('active'), 1200);
    }
}

function setDiagramStatus(text) {
    document.getElementById('diagram-status').textContent = text;
}

function setActionButtonsEnabled(enabled) {
    ['btn-download', 'btn-copy', 'btn-open', 'btn-xml', 'btn-png'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !enabled;
    });
}
