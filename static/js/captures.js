/** Captured Runs playback — read-only viewer for past /chat responses persisted
 *  server-side when Settings → Captured runs → "Save new runs" is enabled
 *  (app/adapters/outbound/capture_store.py).
 *
 *  This is deliberately NOT a "run without calling the LLM/MCP" simulation
 *  mode: selecting a capture just re-renders its already-saved data through
 *  the exact same functions a live response uses (loadDiagramFromResponse,
 *  displayCostEstimate, etc.) — it never calls sendMessage() or /chat. */

async function loadCapturesList() {
    const select = document.getElementById('captures-select');
    if (!select) return;
    select.innerHTML = '<option value="">Loading…</option>';
    try {
        const res = await fetch('/captures');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const captures = data.captures || [];
        if (captures.length === 0) {
            select.innerHTML = '<option value="">No captured runs yet</option>';
            return;
        }
        select.innerHTML = captures.map((c) => {
            const when = c.timestamp ? new Date(c.timestamp * 1000).toLocaleString() : '';
            const snippet = (c.prompt_snippet || '').slice(0, 60);
            return `<option value="${c.id}">${when} — ${escapeHtml(snippet)}</option>`;
        }).join('');
    } catch (err) {
        select.innerHTML = '<option value="">Failed to load captures</option>';
        if (typeof showToast === 'function') showToast(`Couldn't load captured runs: ${err.message}`, 'error');
    }
}

async function playbackSelectedCapture() {
    const select = document.getElementById('captures-select');
    const captureId = select && select.value;
    if (!captureId) {
        if (typeof showError === 'function') showError('Select a captured run first.');
        return;
    }
    try {
        const res = await fetch(`/captures/${encodeURIComponent(captureId)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const record = await res.json();
        renderCapturedResponse(record.response || {});
        if (typeof showToast === 'function') {
            const when = record.timestamp ? new Date(record.timestamp * 1000).toLocaleString() : captureId;
            showToast(`Loaded captured run from ${when} (no LLM/MCP call made)`, 'success');
        }
    } catch (err) {
        if (typeof showError === 'function') showError(`Couldn't load capture: ${err.message}`);
    }
}

function renderCapturedResponse(data) {
    if (typeof mergeServerLogs === 'function') mergeServerLogs(data.logs);

    try {
        if (data.cost_estimate) {
            displayCostEstimate(data.cost_estimate);
        } else {
            hideCostEstimate();
        }
    } catch (e) { appendVerboseLog('client', 'Playback: cost panel error', 'error', { error: String(e) }); }

    try {
        if (data.well_architected) displayWellArchitected(data.well_architected);
    } catch (e) { appendVerboseLog('client', 'Playback: WAF panel error', 'error', { error: String(e) }); }

    try {
        if (data.compliance) {
            window._lastComplianceData = data.compliance;
            displayCompliance(data.compliance);
        }
    } catch (e) { appendVerboseLog('client', 'Playback: compliance panel error', 'error', { error: String(e) }); }

    try {
        // Same placeholder as live chat (`#suggestions-panel-body` via displaySuggestions).
        // Show captured suggestions whenever present — do not gate on the Settings toggle,
        // or playback silently leaves the Suggestions tab empty even when the capture has them.
        if (Array.isArray(data.suggestions) && data.suggestions.length) {
            displaySuggestions(data.suggestions);
        } else if (typeof clearSuggestionsPanel === 'function') {
            clearSuggestionsPanel();
        } else if (typeof displaySuggestions === 'function') {
            displaySuggestions([]);
        }
    } catch (e) { appendVerboseLog('client', 'Playback: suggestions panel error', 'error', { error: String(e) }); }

    try {
        if (data.llm_usage) {
            displayTokenUsage(data.llm_usage);
        } else {
            hideTokenUsage();
        }
    } catch (e) { appendVerboseLog('client', 'Playback: token usage badge error', 'error', { error: String(e) }); }

    if (typeof updateRightPanelVisibility === 'function') updateRightPanelVisibility();

    currentXml = data.xml || '';
    loadDiagramFromResponse(data);
}
