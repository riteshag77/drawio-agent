function getDiagramMode() {
    const select = document.getElementById('diagram-mode-select');
    const mode = select ? select.value : 'auto';
    if (mode === 'cloud' || mode === 'simple') return mode;
    return 'auto';
}

function saveDiagramMode() {
    localStorage.setItem(DIAGRAM_MODE_STORAGE_KEY, getDiagramMode());
}

function initDiagramMode() {
    const stored = localStorage.getItem(DIAGRAM_MODE_STORAGE_KEY) || 'auto';
    const select = document.getElementById('diagram-mode-select');
    if (select && ['auto', 'cloud', 'simple'].includes(stored)) {
        select.value = stored;
    }
}

function updateValidationProviderVisibility() {
    /* VLM validation UI removed */
}

function getPromptSettings() {
    return {
        custom_system_message: document.getElementById('custom-system-message').value.slice(0, 5000),
        vlm_validation: false,
        validation_provider: 'auto',
        cost_estimate: document.getElementById('cost-estimate-toggle').checked,
        well_architected: document.getElementById('well-architected-toggle').checked,
        compliance: document.getElementById('compliance-toggle').checked,
        compliance_frameworks: getSelectedComplianceFrameworks(),
    };
}

function savePromptSettings() {
    const custom = document.getElementById('custom-system-message').value.slice(0, 5000);
    const cost = document.getElementById('cost-estimate-toggle').checked;
    const waf = document.getElementById('well-architected-toggle').checked;
    const comp = document.getElementById('compliance-toggle').checked;
    localStorage.setItem(CUSTOM_SYSTEM_MESSAGE_KEY, custom);
    localStorage.setItem(COST_ESTIMATE_STORAGE_KEY, cost ? '1' : '0');
    localStorage.setItem(WELL_ARCHITECTED_KEY, waf ? '1' : '0');
    localStorage.setItem(COMPLIANCE_KEY, comp ? '1' : '0');
    updateComplianceFrameworksVisibility();
}

function getSelectedComplianceFrameworks() {
    const cbs = document.querySelectorAll('.compliance-fw-cb');
    const selected = [];
    cbs.forEach(function (cb) {
        if (cb.checked) selected.push(cb.value);
    });
    return selected;
}

function updateComplianceFrameworksVisibility() {
    const row = document.getElementById('compliance-frameworks-row');
    if (row) row.hidden = !document.getElementById('compliance-toggle').checked;
}

function saveComplianceFrameworkSelection() {
    const cbs = document.querySelectorAll('.compliance-fw-cb');
    const selected = [];
    cbs.forEach(function (cb) {
        if (cb.checked) selected.push(cb.value);
    });
    localStorage.setItem(COMPLIANCE_FRAMEWORKS_KEY, JSON.stringify(selected));
}

function toggleCostPanelPreview() {
    var panel = document.getElementById('cost-panel');
    var body = document.getElementById('cost-panel-body');
    var total = document.getElementById('cost-total-amount');
    if (!panel || !body) return;
    if (document.getElementById('cost-estimate-toggle').checked) {
        panel.classList.add('active');
        body.innerHTML = '<div class="cost-empty">Generate a diagram to see cost estimates</div>';
        if (total) total.textContent = 'A$--';
    } else {
        panel.classList.remove('active');
        body.innerHTML = '';
        if (total) total.textContent = 'A$0';
    }
    updateRightPanelVisibility();
}

function initPromptSettings() {
    document.getElementById('custom-system-message').value =
        localStorage.getItem(CUSTOM_SYSTEM_MESSAGE_KEY) || '';
    const costStored = localStorage.getItem(COST_ESTIMATE_STORAGE_KEY);
    document.getElementById('cost-estimate-toggle').checked = costStored === '1';
    const wafStored = localStorage.getItem(WELL_ARCHITECTED_KEY);
    document.getElementById('well-architected-toggle').checked = wafStored === '1';
    const compStored = localStorage.getItem(COMPLIANCE_KEY);
    document.getElementById('compliance-toggle').checked = compStored === '1';
    const fwStored = localStorage.getItem(COMPLIANCE_FRAMEWORKS_KEY);
    if (fwStored) {
        try {
            const selected = JSON.parse(fwStored);
            document.querySelectorAll('.compliance-fw-cb').forEach(function (cb) {
                cb.checked = selected.indexOf(cb.value) !== -1;
            });
        } catch (_) {}
    }
    document.querySelectorAll('.compliance-fw-cb').forEach(function (cb) {
        cb.addEventListener('change', saveComplianceFrameworkSelection);
    });
    updateValidationProviderVisibility();
    updateComplianceFrameworksVisibility();
    toggleCostPanelPreview();
    toggleWellArchitectedPanel();
    toggleCompliancePanel();
}

function setComposerRunStatus(text, level = 'info') {
    if (!text) return;
    if (!verboseLogging) return;
    const levelMap = { error: 'error', running: 'info', success: 'success', stopped: 'warn', info: 'info' };
    appendVerboseLog('run', text, levelMap[level] || 'info');
}

function showError(msg) {
    setComposerRunStatus(msg, 'error');
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

async function handleFileSelect(event) {
    const files = Array.from(event.target.files || []);
    const preview = document.getElementById('attachment-preview');
    preview.innerHTML = '';
    preview.hidden = true;
    attachmentContext = '';
    attachmentImageData = null;
    hasImageAttachment = false;

    for (const file of files.slice(0, 3)) {
        if (file.type.startsWith('image/')) {
            const dataUrl = await new Promise((res, rej) => {
                const r = new FileReader();
                r.onload = () => res(r.result);
                r.onerror = rej;
                r.readAsDataURL(file);
            });
            attachmentImageData = dataUrl;
            hasImageAttachment = true;
            preview.hidden = false;
            preview.textContent = `📷 ${file.name}`;
        } else if (file.type === 'application/pdf' || file.name.endsWith('.pdf')) {
            preview.hidden = false;
            preview.textContent = `📄 ${file.name} (PDF — describe in prompt or paste text)`;
            attachmentContext += `[Attached PDF: ${file.name} — extract architecture from user description]\n`;
        } else {
            const text = await file.text();
            attachmentContext += `\n--- File: ${file.name} ---\n${text.slice(0, 8000)}\n`;
            preview.hidden = false;
            preview.textContent = `📄 ${file.name}`;
        }
    }
    event.target.value = '';
}

async function parseUrlContext(url) {
    const res = await fetch('/parse-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'URL parse failed');
    return `--- URL: ${url} (${data.title}) ---\n${data.content}`;
}

async function postChatStream(chatBody, signal) {
    const res = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        signal,
        body: JSON.stringify(chatBody),
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const errBody = await res.json();
            detail = errBody.detail || detail;
        } catch (_) {}
        throw new Error(detail);
    }
    if (!res.body || !res.body.getReader) {
        throw new Error('Streaming body unavailable');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult = null;
    let progress = 20;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';
        for (const chunk of chunks) {
            const line = chunk.split('\n').find((l) => l.startsWith('data: '));
            if (!line) continue;
            let evt;
            try {
                evt = JSON.parse(line.slice(6));
            } catch (_) {
                continue;
            }
            if (evt.logs) mergeServerLogs(evt.logs);
            if (evt.type === 'progress') {
                progress = Math.min(85, progress + 8);
                setProgress(progress);
                if (evt.message) {
                    setDiagramStatus(evt.message);
                    setComposerRunStatus(evt.message, 'running');
                }
                appendVerboseLog(evt.step || 'progress', evt.message || 'Progress', 'info', evt.details || null);
            } else if (evt.type === 'result') {
                finalResult = evt;
            } else if (evt.type === 'error') {
                throw new Error(evt.detail || 'Stream error');
            }
        }
    }

    if (!finalResult) throw new Error('Stream ended without a result');
    if (finalResult.logs) mergeServerLogs(finalResult.logs);
    return finalResult;
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
    ['btn-download', 'btn-copy', 'btn-open', 'btn-xml'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !enabled;
    });
}

function setPlatformTabsBusy(busy) {
    document.querySelectorAll('.platform-card').forEach(card => {
        card.classList.toggle('is-busy', busy);
        card.setAttribute('aria-busy', busy ? 'true' : 'false');
    });
}

function applyPlatformUi(platform) {
    selectedPlatform = platform;
    document.querySelectorAll('.platform-card').forEach(card => {
        const isActive = card.getAttribute('data-platform') === platform;
        card.classList.toggle('active', isActive);
        card.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    const root = document.documentElement;
    const chatInput = document.getElementById('chat-input');

    if (platform === 'aws') {
        root.style.setProperty('--accent', '#ED7100');
        root.style.setProperty('--accent-glow', 'rgba(237, 113, 0, 0.2)');
        root.style.setProperty('--accent-hover', '#ff8c2a');
    } else if (platform === 'gcp') {
        root.style.setProperty('--accent', '#4285F4');
        root.style.setProperty('--accent-glow', 'rgba(66, 133, 244, 0.2)');
        root.style.setProperty('--accent-hover', '#669df6');
    } else if (platform === 'azure') {
        root.style.setProperty('--accent', '#0078D4');
        root.style.setProperty('--accent-glow', 'rgba(0, 120, 212, 0.2)');
        root.style.setProperty('--accent-hover', '#2b90f5');
    } else {
        root.style.setProperty('--accent', '#8C4FFF');
        root.style.setProperty('--accent-glow', 'rgba(140, 79, 255, 0.2)');
        root.style.setProperty('--accent-hover', '#a67cff');
    }

    chatInput.placeholder = CHAT_PLACEHOLDERS[platform] || CHAT_PLACEHOLDERS.aws;
    if (document.getElementById('example-chips')) {
        renderExampleChips();
    }
}

function shouldRegenerateOnPlatformSwitch() {
    if (isSending) return false;
    if (currentMermaid && !currentArchitecture) return false;
    if (currentArchitecture?.nodes?.length > 0) return true;
    if (currentXml && currentXml.length > 300 && !currentMermaid) return true;
    return false;
}

async function regenerateForPlatform(previousPlatform) {
    const targetLabel = selectedPlatform.toUpperCase();
    const message =
        `Convert this architecture from ${previousPlatform} to ${selectedPlatform}. ` +
        `Keep the same topology and flow; use equivalent ${selectedPlatform} services and icons only.`;
    await sendMessage({
        skipInput: true,
        platformConvert: true,
        previousPlatform,
        message,
        showUserBubble: true,
    });
}

function selectPlatform(platform) {
    if (platform === selectedPlatform) return;
    if (isSending) return;

    const previousPlatform = selectedPlatform;
    applyPlatformUi(platform);
    renderPatternGallery();

    if (shouldRegenerateOnPlatformSwitch()) {
        regenerateForPlatform(previousPlatform);
    } else if (currentMermaid && !currentArchitecture) {
        setDiagramStatus('Platform applies to new cloud diagrams');
    }
}

function updateLogCount() {
    const badge = document.getElementById('log-count-badge');
    badge.textContent = `${logEntryCount} ${logEntryCount === 1 ? 'entry' : 'entries'}`;
}

function appendVerboseLog(step, message, level = 'info', details = null, timestamp = null) {
    if (!verboseLogging) return;

    const body = document.getElementById('verbose-log-body');
    const empty = body.querySelector('.log-empty');
    if (empty) empty.remove();

    const entry = document.createElement('div');
    entry.className = `log-entry ${level}`;

    const head = document.createElement('div');
    head.className = 'log-entry-head';

    const meta = document.createElement('div');
    meta.className = 'log-entry-meta';

    const timeEl = document.createElement('span');
    timeEl.className = 'log-time';
    timeEl.textContent = formatLogTime(timestamp);

    const stepEl = document.createElement('span');
    stepEl.className = 'log-step';
    stepEl.textContent = step;

    meta.appendChild(timeEl);
    meta.appendChild(stepEl);

    const msgEl = document.createElement('span');
    msgEl.className = 'log-message';
    msgEl.textContent = message;

    head.appendChild(meta);
    head.appendChild(msgEl);
    entry.appendChild(head);

    if (details) {
        const detailEl = document.createElement('div');
        detailEl.className = 'log-details';
        detailEl.textContent = formatDetails(details);
        entry.appendChild(detailEl);
    }

    body.appendChild(entry);
    body.scrollTop = body.scrollHeight;

    logEntryCount += 1;
    updateLogCount();
}

function clearVerboseLogs() {
    const body = document.getElementById('verbose-log-body');
    body.innerHTML = '<div class="log-empty">Log cleared. Generate a diagram to see new pipeline steps.</div>';
    logEntryCount = 0;
    updateLogCount();
}

function updateRightPanelVisibility() {
    const costPanelActive = document.getElementById('cost-panel').classList.contains('active');
    const wafPanelActive = document.getElementById('well-architected-panel').classList.contains('active');
    const compPanelActive = document.getElementById('compliance-panel').classList.contains('active');
    const open = verboseLogging || validationPanelActive || costPanelActive || wafPanelActive || compPanelActive;
    document.getElementById('log-panel').classList.toggle('active', open);
}

function setLogPanelOpen(open) {
    verboseLogging = open;
    document.getElementById('verbose-toggle').checked = open;
    localStorage.setItem(VERBOSE_STORAGE_KEY, open ? '1' : '0');
    updateRightPanelVisibility();
}

function toggleVerbosePanel() {
    const open = document.getElementById('verbose-toggle').checked;
    setLogPanelOpen(open);
    if (open) {
        appendVerboseLog('ui', 'Pipeline log panel opened', 'info');
    }
}

function closeLogPanel() {
    hideValidationBanner();
    setLogPanelOpen(false);
}

function mergeServerLogs(logs) {
    (logs || []).forEach(entry => {
        appendVerboseLog(
            entry.step || 'server',
            entry.message || '',
            entry.level || 'info',
            entry.details || null,
            entry.timestamp || null
        );
    });
}

function initVerboseLogging() {
    const open = localStorage.getItem(VERBOSE_STORAGE_KEY) === '1';
    setLogPanelOpen(open);
    updateLogCount();
}

function displayCostEstimate(costData) {
    if (!costData) return;
    const panel = document.getElementById('cost-panel');
    const body = document.getElementById('cost-panel-body');
    const totalEl = document.getElementById('cost-total-amount');
    if (!panel || !body || !totalEl) return;
    panel.classList.add('active');
    var grandTotal = costData.total || 0;
    totalEl.textContent = 'A$' + grandTotal.toFixed(0);
    body.innerHTML = '';
    var categories = costData.breakdown || {};
    var catKeys = Object.keys(categories);
    if (catKeys.length === 0) {
        body.innerHTML = '<div class="cost-empty">No billable services detected</div>';
        return;
    }
    var catTotals = {};
    catKeys.forEach(function (cat) {
        var svcs = categories[cat] || [];
        var sub = 0;
        svcs.forEach(function (s) { sub += s.monthly || 0; });
        catTotals[cat] = sub;
    });
    catKeys.forEach(function (cat) {
        var svcs = categories[cat] || [];
        var subTotal = catTotals[cat] || 0;
        var pct = grandTotal > 0 ? (subTotal / grandTotal * 100) : 0;
        var card = document.createElement('div');
        card.className = 'cost-category-card';
        var head = document.createElement('div');
        head.className = 'cost-category-head';
        var name = document.createElement('span');
        name.className = 'cost-category-name';
        name.textContent = cat;
        var subEl = document.createElement('span');
        subEl.className = 'cost-category-subtotal';
        subEl.textContent = 'A$' + subTotal.toFixed(0);
        head.appendChild(name);
        head.appendChild(subEl);
        card.appendChild(head);
        var bar = document.createElement('div');
        bar.className = 'cost-category-bar';
        var fill = document.createElement('div');
        fill.className = 'cost-category-bar-fill';
        fill.style.width = Math.max(pct, 2) + '%';
        bar.appendChild(fill);
        card.appendChild(bar);
        svcs.forEach(function (svc) {
            var row = document.createElement('div');
            row.className = 'cost-service-row';
            var nameSpan = document.createElement('span');
            nameSpan.className = 'cost-service-name';
            nameSpan.textContent = svc.name || svc.key;
            var priceSpan = document.createElement('span');
            priceSpan.className = 'cost-service-price';
            priceSpan.textContent = 'A$' + (svc.monthly || 0).toFixed(0);
            row.appendChild(nameSpan);
            row.appendChild(priceSpan);
            card.appendChild(row);
        });
        body.appendChild(card);
    });
    if (costData.services_unmatched > 0) {
        var note = document.createElement('div');
        note.className = 'cost-unmatched-note';
        note.textContent = costData.services_unmatched + ' service(s) without exact pricing — estimated at default cost';
        body.appendChild(note);
    }
}

function hideCostEstimate() {
    var panel = document.getElementById('cost-panel');
    if (panel) {
        panel.classList.remove('active');
        updateRightPanelVisibility();
    }
}

function toggleWellArchitectedPanel() {
    var panel = document.getElementById('well-architected-panel');
    var body = document.getElementById('waf-panel-body');
    var badge = document.getElementById('waf-overall-badge');
    if (!panel) return;
    if (document.getElementById('well-architected-toggle').checked) {
        panel.classList.add('active');
        if (body) body.innerHTML = '<div class="waf-empty">Generate a diagram to see Well-Architected review</div>';
        if (badge) { badge.textContent = '--'; badge.className = 'waf-overall-badge'; }
    } else {
        panel.classList.remove('active');
        if (body) body.innerHTML = '';
        if (badge) { badge.textContent = '--'; badge.className = 'waf-overall-badge'; }
    }
    updateRightPanelVisibility();
}

function hideWellArchitectedPanel() {
    var panel = document.getElementById('well-architected-panel');
    if (panel) {
        panel.classList.remove('active');
        document.getElementById('well-architected-toggle').checked = false;
        localStorage.setItem(WELL_ARCHITECTED_KEY, '0');
        updateRightPanelVisibility();
    }
}

function displayWellArchitected(wafData) {
    if (!wafData) return;
    console.log('WAF data received', wafData.overall_status, wafData.pillars?.length, 'pillars');
    var panel = document.getElementById('well-architected-panel');
    var body = document.getElementById('waf-panel-body');
    var badge = document.getElementById('waf-overall-badge');
    if (!panel || !body || !badge) { console.warn('WAF panel elements missing'); return; }
    panel.classList.add('active');
    var toggle = document.getElementById('well-architected-toggle');
    if (toggle) toggle.checked = true;
    localStorage.setItem(WELL_ARCHITECTED_KEY, '1');
    var status = wafData.overall_status || 'pass';
    badge.textContent = status.toUpperCase();
    badge.className = 'waf-overall-badge ' + status;
    body.innerHTML = '';
    var pillars = wafData.pillars || [];
    if (pillars.length === 0) {
        body.innerHTML = '<div class="waf-empty">No recommendations available</div>';
        return;
    }
    pillars.forEach(function (p) {
        var div = document.createElement('div');
        div.className = 'waf-pillar';
        var header = document.createElement('div');
        header.className = 'waf-pillar-header';
        var icon = document.createElement('span');
        icon.className = 'waf-pillar-status ' + (p.status || 'pass');
        icon.innerHTML = p.status === 'pass' ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>' : p.status === 'warn' ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        header.appendChild(icon);
        var name = document.createElement('span');
        name.className = 'waf-pillar-name';
        name.textContent = p.name || '';
        header.appendChild(name);
        div.appendChild(header);
        if (p.description) {
            var desc = document.createElement('div');
            desc.className = 'waf-pillar-desc';
            desc.textContent = p.description;
            div.appendChild(desc);
        }
        (p.findings || []).forEach(function (f) {
            var finding = document.createElement('div');
            finding.className = 'waf-finding';
            var dot = document.createElement('span');
            dot.className = 'waf-finding-dot ' + (f.severity || 'low');
            finding.appendChild(dot);
            var text = document.createElement('div');
            text.className = 'waf-finding-text';
            text.textContent = f.message || '';
            if (f.recommendation) {
                var rec = document.createElement('span');
                rec.className = 'waf-finding-recommendation';
                rec.textContent = '→ ' + f.recommendation;
                text.appendChild(rec);
            }
            finding.appendChild(text);
            div.appendChild(finding);
        });
        body.appendChild(div);
    });
    var summary = wafData.summary || '';
    if (summary) {
        var s = document.createElement('div');
        s.style.cssText = 'margin-top:0.5rem;font-size:0.7rem;color:var(--text-muted);line-height:1.4';
        s.textContent = summary;
        body.appendChild(s);
    }
    updateRightPanelVisibility();
}

// ── Compliance Panel ─────────────────────────────────────

function toggleCompliancePanel() {
    var panel = document.getElementById('compliance-panel');
    var body = document.getElementById('compliance-body');
    if (!panel) return;
    if (document.getElementById('compliance-toggle').checked) {
        panel.classList.add('active');
        if (body) body.innerHTML = '<div class="compliance-empty">Generate a diagram to see compliance assessment results</div>';
    } else {
        panel.classList.remove('active');
        if (body) body.innerHTML = '';
    }
    updateRightPanelVisibility();
}

function hideCompliancePanel() {
    var panel = document.getElementById('compliance-panel');
    if (panel) {
        panel.classList.remove('active');
        document.getElementById('compliance-toggle').checked = false;
        localStorage.setItem(COMPLIANCE_KEY, '0');
        updateRightPanelVisibility();
    }
}

function displayCompliance(compData) {
    if (!compData || !compData.frameworks) return;
    var panel = document.getElementById('compliance-panel');
    var tabs = document.getElementById('compliance-tabs');
    var body = document.getElementById('compliance-body');
    if (!panel || !tabs || !body) return;
    panel.classList.add('active');
    var toggle = document.getElementById('compliance-toggle');
    if (toggle) toggle.checked = true;
    localStorage.setItem(COMPLIANCE_KEY, '1');

    var frameworks = compData.frameworks;
    var fwIds = Object.keys(frameworks);
    if (fwIds.length === 0) {
        body.innerHTML = '<div class="compliance-empty">No compliance frameworks evaluated</div>';
        return;
    }

    tabs.innerHTML = '';
    fwIds.forEach(function (fwId, idx) {
        var fw = frameworks[fwId];
        var tab = document.createElement('button');
        tab.className = 'compliance-tab' + (idx === 0 ? ' active' : '');
        tab.setAttribute('data-framework', fwId);
        var badge = document.createElement('span');
        badge.className = 'compliance-tab-badge ' + (fw.overall_status || 'pass');
        badge.textContent = (fw.overall_status || 'pass').toUpperCase();
        tab.appendChild(document.createTextNode(fw.name || fwId));
        tab.appendChild(badge);
        tab.addEventListener('click', function () {
            switchComplianceTab(fwId);
        });
        tabs.appendChild(tab);
    });

    switchComplianceTab(fwIds[0]);
    updateRightPanelVisibility();
}

function switchComplianceTab(fwId) {
    var body = document.getElementById('compliance-body');
    var tabs = document.getElementById('compliance-tabs');
    if (!body || !tabs) return;

    var compData = window._lastComplianceData;
    if (!compData || !compData.frameworks) return;
    var fw = compData.frameworks[fwId];
    if (!fw) return;

    tabs.querySelectorAll('.compliance-tab').forEach(function (t) {
        t.classList.toggle('active', t.getAttribute('data-framework') === fwId);
    });

    var inScope = fw.services_in_scope || [];
    var outScope = fw.services_out_of_scope || [];

    var html = '';
    html += '<div class="compliance-fw-summary">';
    html += '<span class="compliance-fw-status ' + (fw.overall_status || 'pass') + '">' + (fw.overall_status || '').toUpperCase() + '</span>';
    html += '<span class="compliance-fw-summary-text">' + (fw.summary || '') + '</span>';
    html += '</div>';

    var controls = fw.controls || [];
    if (controls.length === 0) {
        html += '<div class="compliance-empty">No applicable controls for this diagram</div>';
    } else {
        controls.forEach(function (ctrl) {
            var statusClass = ctrl.status || 'pass';
            var icon = statusClass === 'pass' ? '&#x2713;' : statusClass === 'warn' ? '&#x26A0;' : '&#x2717;';
            html += '<div class="compliance-control-card ' + statusClass + '">';
            html += '<div class="compliance-control-header">';
            html += '<span class="compliance-control-status compliance-status-' + statusClass + '">' + icon + '</span>';
            html += '<span class="compliance-control-name">' + escapeHtml(ctrl.name || ctrl.id || '') + '</span>';
            html += '</div>';
            (ctrl.findings || []).forEach(function (f) {
                var sev = f.severity || 'low';
                var sevIcon = sev === 'high' ? '&#x2717;' : sev === 'medium' ? '&#x26A0;' : '&#x2713;';
                html += '<div class="compliance-finding ' + sev + '">';
                html += '<span class="compliance-finding-dot compliance-severity-' + sev + '">' + sevIcon + '</span>';
                html += '<div class="compliance-finding-text">';
                html += '<span>' + escapeHtml(f.message || '') + '</span>';
                if (f.affected_services && f.affected_services.length > 0) {
                    html += '<span class="compliance-finding-services">Affects: ' + f.affected_services.join(', ') + '</span>';
                }
                if (f.recommendation) {
                    html += '<span class="compliance-finding-rec">&#x2192; ' + escapeHtml(f.recommendation) + '</span>';
                }
                html += '</div>';
                html += '</div>';
            });
            html += '</div>';
        });
    }

    if (inScope.length > 0 || outScope.length > 0) {
        html += '<div class="compliance-scope-section">';
        html += '<div class="compliance-scope-title">Service Scope</div>';
        if (inScope.length > 0) {
            html += '<div class="compliance-scope-list"><span class="scope-label-in">In scope:</span> ' + inScope.join(', ') + '</div>';
        }
        if (outScope.length > 0) {
            html += '<div class="compliance-scope-list"><span class="scope-label-out">Out of scope:</span> ' + outScope.join(', ') + '</div>';
        }
        html += '</div>';
    }

    if (fw.llm_findings && fw.llm_findings.length > 0) {
        html += '<div class="compliance-llm-note">Enhanced with LLM analysis</div>';
    }

    body.innerHTML = html;
}

function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

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
    if (!currentXml) return;
    navigator.clipboard.writeText(currentXml).then(() => {
        const toast = document.getElementById('copy-toast');
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2000);
    });
}

function openInDrawio() {
    if (!currentXml) return;
    const encoded = encodeURIComponent(currentXml);
    window.open(`https://app.diagrams.net/#R${encoded}`, '_blank');
}

function toggleXML() {
    const panel = document.getElementById('xml-panel');
    const btn = document.getElementById('btn-xml');
    if (!panel || !btn || btn.disabled) return;
    const isOpen = panel.classList.toggle('open');
    btn.setAttribute('aria-pressed', isOpen ? 'true' : 'false');
    btn.title = isOpen ? 'Hide Draw.io XML source' : 'View Draw.io XML source';
    btn.setAttribute('aria-label', btn.title);
}

async function sendMessage(options = {}) {
    const input = document.getElementById('chat-input');
    const text = (options.skipInput ? options.message : input.value).trim();
    const provider = document.getElementById('provider-select').value;
    const sendBtn = document.getElementById('send-btn');
    const validationFeedback = options.validationFeedback || null;

    if (!text || isSending) {
        if (!text && !validationFeedback) appendVerboseLog('run', 'Please enter a message.', 'warn');
        return;
    }

    hideValidationBanner();
    vlmAutoRetryCount = 0;
    if (isListening) {
        voiceManuallyStopping = true;
        try { speechRecognition.stop(); } catch (_) {}
    }
    isSending = true;
    sendBtn.disabled = true;
    input.disabled = true;
    updateSendStopButtons();
    setPlatformTabsBusy(true);
    setSuggestionPanelsBusy(true);

    const parsedApplied = parseImplementedSuggestions(text);
    if (parsedApplied.length) {
        registerAppliedSuggestions(parsedApplied);
        markAllMatchingSuggestionsApplied(parsedApplied);
    }

    if (!options.skipInput) {
        chatMessages.push({ role: 'user', content: text });
        appendChatBubble('user', text);
        input.value = '';
        autoResizeChatInput();
    } else if (options.platformConvert && text) {
        chatMessages.push({ role: 'user', content: text });
        if (options.showUserBubble !== false) {
            appendChatBubble('user', text);
        }
    }

    setProgress(10);
    setTyping(true);
    setComposerRunStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : 'Generating diagram…',
        'running'
    );
    setDiagramStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : 'Analyzing architecture...'
    );
    document.getElementById('diagram-loading').classList.add('active');

    chatAbortController = new AbortController();
    runStoppedByUser = false;

    if (verboseLogging) {
        appendVerboseLog('client', 'Sending chat message', 'info', {
            platform: selectedPlatform,
            provider,
            turn: chatMessages.length,
            has_existing_diagram: !!currentArchitecture,
        });
    }

    try {
        const promptSettings = getPromptSettings();
        // With architecture JSON, skip iframe XML export (up to 5s) and stay on JSON edit path.
        let xmlForRequest = null;
        if (!currentArchitecture) {
            const liveXml = await exportDiagramXml();
            xmlForRequest = (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);
        }
        previousXml = currentXml || null;

        let contextText = attachmentContext || '';
        const urlMatch = text.match(/https?:\/\/[^\s<>"']+/);
        // Don't block first-gen on URL fetch when we already have attachment context.
        if (urlMatch && !contextText.includes(urlMatch[0])) {
            try {
                const urlCtxPromise = parseUrlContext(urlMatch[0]);
                // Race with a short timeout so a slow URL does not stall /chat.
                const timed = await Promise.race([
                    urlCtxPromise,
                    new Promise((_, reject) => setTimeout(() => reject(new Error('URL parse timeout')), 2500)),
                ]);
                contextText = (contextText ? contextText + '\n' : '') + timed;
            } catch (urlErr) {
                appendVerboseLog('client', `URL parse skipped: ${urlErr.message}`, 'warn');
            }
        }

        appendVerboseLog('client', 'POST /chat/stream', 'info', {
            ...promptSettings,
            has_current_xml: !!xmlForRequest,
            xml_chars: (xmlForRequest || '').length,
        });
        const chatBody = {
            messages: chatMessages,
            provider,
            validation_provider: promptSettings.validation_provider,
            platform: selectedPlatform,
            diagram_mode: options.platformConvert ? 'cloud' : getDiagramMode(),
            platform_convert: !!options.platformConvert,
            previous_platform: options.previousPlatform || null,
            diagram_renderer: document.getElementById('diagram-renderer-select')?.value || 'auto',
            current_architecture: currentArchitecture,
            previous_architecture: previousArchitecture,
            current_xml: xmlForRequest,
            previous_xml: previousXml,
            custom_system_message: promptSettings.custom_system_message,
            cost_estimate: promptSettings.cost_estimate,
            well_architected: promptSettings.well_architected,
            compliance: promptSettings.compliance,
            compliance_frameworks: promptSettings.compliance_frameworks,
            validation_feedback: validationFeedback,
            context_text: contextText || null,
            image_data: attachmentImageData,
            has_image_input: hasImageAttachment,
            applied_suggestions: getAppliedSuggestionsPayload(),
        };

        let data;
        try {
            data = await postChatStream(chatBody, chatAbortController.signal);
        } catch (streamErr) {
            if (streamErr.name === 'AbortError') throw streamErr;
            appendVerboseLog('client', `Stream unavailable, falling back to /chat: ${streamErr.message}`, 'warn');
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: chatAbortController.signal,
                body: JSON.stringify(chatBody),
            });
            setProgress(45);
            setDiagramStatus('Building diagram XML...');
            data = await res.json();
            mergeServerLogs(data.logs);
            if (!res.ok) throw new Error(data.detail || 'Server error');
        }

        setProgress(45);
        setDiagramStatus('Building diagram XML...');

        if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

        const assistantText = data.assistant_message || 'Diagram updated.';
        chatMessages.push({ role: 'assistant', content: assistantText });

        appendVerboseLog('client', 'Server response received', 'success', {
            suggestions: (data.suggestions || []).length,
            diagram_type: data.diagram_type || 'cloud',
            xml_chars: (data.xml || '').length,
            mermaid_chars: (data.mermaid || '').length,
            edit_mode: !!data.edit_mode,
        });

        currentXml = data.xml || '';
        previousXml = xmlForRequest;
        previousArchitecture = currentArchitecture;
        if (data.edit_mode) {
            currentArchitecture = null;
        } else {
            currentArchitecture = data.architecture || null;
        }

        attachmentContext = '';
        attachmentImageData = null;
        hasImageAttachment = false;
        const attachPreview = document.getElementById('attachment-preview');
        if (attachPreview) { attachPreview.hidden = true; attachPreview.textContent = ''; }

        try {
            if (data.cost_estimate) {
                displayCostEstimate(data.cost_estimate);
                updateRightPanelVisibility();
            } else {
                hideCostEstimate();
            }
        } catch (e) { console.error('Cost panel error', e); }

        try {
            if (data.well_architected) {
                displayWellArchitected(data.well_architected);
                updateRightPanelVisibility();
            }
        } catch (e) { console.error('WAF panel error', e); }

        try {
            if (data.compliance) {
                window._lastComplianceData = data.compliance;
                displayCompliance(data.compliance);
                updateRightPanelVisibility();
            }
        } catch (e) { console.error('Compliance panel error', e); }

        if (data.validation?.feedback) {
            showValidationBanner(data.validation.structural || data.validation.vlm, data.validation.feedback);
        } else if (data.validation?.structural && !data.validation.structural.valid) {
            showValidationBanner(data.validation.structural, data.validation.feedback);
        }

        setTyping(false);
        appendChatBubble('assistant', assistantText, data.suggestions || []);

        setProgress(75);
        setDiagramStatus('Loading into Draw.io...');
        loadDiagramFromResponse(data);
        setActionButtonsEnabled(true);
        setProgress(100);
        finishChatRun({
            outcome: 'success',
            message: 'Done — diagram updated. Pan and zoom in the preview.',
        });
        appendVerboseLog('client', 'Chat turn complete', 'success');
    } catch (err) {
        console.error(err);
        if (options.platformConvert && options.previousPlatform) {
            applyPlatformUi(options.previousPlatform);
        }
        if (err.name === 'AbortError' || runStoppedByUser) {
            if (!options.skipInput || !options.platformConvert) chatMessages.pop();
            appendVerboseLog('client', 'Request stopped by user', 'warn');
            finishChatRun({
                outcome: 'stopped',
                message: 'Stopped — edit your message and send again when ready.',
            });
        } else {
            if (!options.skipInput || !options.platformConvert) chatMessages.pop();
            setTyping(false);
            appendVerboseLog('client', err.message || 'Request failed', 'error');
            finishChatRun({
                outcome: 'error',
                message: err.message || 'Request failed. Check the server logs.',
            });
        }
    } finally {
        chatAbortController = null;
        runStoppedByUser = false;
        isSending = false;
        sendBtn.disabled = false;
        input.disabled = false;
        updateSendStopButtons();
        setPlatformTabsBusy(false);
        setSuggestionPanelsBusy(false);
        input.focus();
    }
}

// ── Boot sequence ─────────────────────────────────────────
initVerboseLogging();
initPromptSettings();
initDiagramMode();
hideValidationBanner();
updateSendStopButtons();
loadTemplates();
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
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSettings();
});
