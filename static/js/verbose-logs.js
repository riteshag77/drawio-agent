/** Verbose log panel — append, clear, panel toggle, mergeServerLogs. */

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
    const costOn = document.getElementById('cost-estimate-toggle')?.checked;
    const wafOn = document.getElementById('well-architected-toggle')?.checked;
    const compOn = document.getElementById('compliance-toggle')?.checked;
    const open = verboseLogging || costOn || wafOn || compOn;
    document.getElementById('log-panel')?.classList.toggle('active', open);
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
        if (typeof switchInsightsTab === 'function') switchInsightsTab('logs');
    }
}

function closeLogPanel() {
    hideValidationBanner();
    hideCostEstimate();
    hideWellArchitectedPanel();
    hideCompliancePanel();
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
