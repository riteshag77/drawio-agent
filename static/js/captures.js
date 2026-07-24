/** Captured Runs playback — read-only viewer for full, untruncated past
 *  /chat responses persisted server-side (app/adapters/outbound/capture_store.py).
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
    } catch (e) { console.error('Playback: cost panel error', e); }

    try {
        if (data.well_architected) displayWellArchitected(data.well_architected);
    } catch (e) { console.error('Playback: WAF panel error', e); }

    try {
        if (data.compliance) {
            window._lastComplianceData = data.compliance;
            displayCompliance(data.compliance);
        }
    } catch (e) { console.error('Playback: compliance panel error', e); }

    try {
        if (data.llm_usage) {
            displayTokenUsage(data.llm_usage);
        } else {
            hideTokenUsage();
        }
    } catch (e) { console.error('Playback: token usage badge error', e); }

    if (typeof updateRightPanelVisibility === 'function') updateRightPanelVisibility();

    currentXml = data.xml || '';
    loadDiagramFromResponse(data);
}
