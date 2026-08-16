/**
 * app.js — boot sequence.
 *
 * This file must load LAST: unlike its siblings, which only declare functions,
 * everything below the boot banner executes at load time and expects every
 * other module to already be defined.
 *
 * Responsibilities kept here:
 *   - the boot sequence itself
 *   - downloadXML / copyXML / openInDrawio / toggleXML  (dedupe guard vs exports.js)
 *
 * The chat turn used to live here too. It now spans four focused siblings —
 * chat-api (transport), chat-request (assembly), chat-response (applying a
 * result), chat-send (running the turn) — plus provider-select and
 * visual-validation. There is no module system here: every file declares plain
 * globals and index.html's ordered <script> tags are the dependency graph, so
 * a new file needs a tag adding there and nothing else.
 */

// ── Diagram XML actions (guard against duplicate export stubs) ────────────

function downloadXML() {
    if (!currentXml) return;
    const blob = new Blob([currentXml], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `architecture_${selectedPlatform}.drawio`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function copyXML() {
    // Copy XML UI removed — keep stub for any leftover callers.
}

function openInDrawio() {
    if (!currentXml) return;
    const encoded = encodeURIComponent(currentXml);
    window.open(`https://app.diagrams.net/#R${encoded}`, '_blank');
}

function toggleXML() {
    // XML panel UI removed — keep stub for any leftover callers.
}

// ── Boot sequence ─────────────────────────────────────────
initAppTheme();
initInsightSelection();
initSuggestionsPanel();
initVerboseLogging();
initPromptSettings();
initDiagramMode();
initProviderSelect();
initValidationProviderSelect();
hideValidationBanner();
updateSendStopButtons();
loadTemplates();
if (typeof ensureSessionId === 'function') ensureSessionId();
const _urlSessionId = new URLSearchParams(window.location.search).get('session');
if (_urlSessionId && typeof openSession === 'function') {
    openSession(_urlSessionId);
} else if (typeof refreshVersionStrip === 'function') {
    refreshVersionStrip();
}
if (typeof loadCustomProviders === 'function') loadCustomProviders();
if (typeof initVoiceCommands === 'function') initVoiceCommands();
initVoiceInput();
ensureDrawioFrame();
const bootTheme = localStorage.getItem(THEME_STORAGE_KEY) || 'kennedy';
appendVerboseLog('drawio', 'Embed iframe created', 'info', { url: getDrawioUrl(bootTheme) });
applyPlatformUi('aws');
renderExampleChips();
renderPatternGallery();

const chatInput = document.getElementById('chat-input');
chatInput.addEventListener('input', autoResizeChatInput);
chatInput.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' && !e.shiftKey) || ((e.ctrlKey || e.metaKey) && e.key === 'Enter')) {
        e.preventDefault();
        sendMessage();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSettings();
});
