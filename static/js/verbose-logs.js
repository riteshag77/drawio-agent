/** Verbose log panel — append, clear, panel toggle, mergeServerLogs. */

function updateLogCount() {
    const badge = document.getElementById('log-count-badge');
    badge.textContent = `${logEntryCount} ${logEntryCount === 1 ? 'entry' : 'entries'}`;
}

function appendVerboseLog(step, message, level = 'info', details = null, timestamp = null) {
    if (!verboseLogging) return;

    const body = document.getElementById('verbose-log-entries');
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
    const body = document.getElementById('verbose-log-entries');
    body.innerHTML = '<div class="log-empty">Log cleared. Generate a diagram to see new pipeline steps.</div>';
    logEntryCount = 0;
    updateLogCount();
}

function updateRightPanelVisibility() {
    const costOn = document.getElementById('cost-estimate-toggle')?.checked;
    const suggOn = document.getElementById('suggestions-toggle')?.checked;
    const wafOn = document.getElementById('well-architected-toggle')?.checked;
    const compOn = document.getElementById('compliance-toggle')?.checked;
    // Only auto-open for a governance failure if the user has actually
    // turned the Governance findings toggle on — same opt-in rule as every
    // other insight here.
    const govOn = document.getElementById('governance-toggle')?.checked;
    const govFailing = govOn && typeof lastGovernanceFailedCount !== 'undefined' && lastGovernanceFailedCount > 0;
    const open = verboseLogging || costOn || suggOn || wafOn || compOn || govFailing;
    document.getElementById('log-panel')?.classList.toggle('active', open);
    const reopenTab = document.getElementById('log-panel-reopen-tab');
    if (reopenTab) reopenTab.hidden = open;
    if (!open && typeof renderCollapsedRailTabs === 'function') renderCollapsedRailTabs();
}

/** Mirrors the currently-visible .insights-tab buttons as icon-only buttons
 * on the collapsed right rail, so a click both reopens the panel and jumps
 * straight to that tab — one tap instead of two. */
function renderCollapsedRailTabs() {
    const container = document.getElementById('collapsed-rail-tabs');
    if (!container) return;
    const tabs = Array.from(document.querySelectorAll('.insights-tab')).filter((btn) => !btn.hidden);
    container.innerHTML = tabs.map((btn) => {
        const tab = btn.dataset.tab;
        const icon = btn.querySelector('.insights-tab-icon');
        const label = btn.getAttribute('aria-label') || (icon ? icon.nextSibling?.textContent?.trim() : '') || tab;
        return `<button type="button" class="collapsed-rail-tab-btn" onclick="setLogPanelOpen(); switchInsightsTab('${tab}')" title="${btn.textContent.trim()}" aria-label="${label}">${icon ? icon.outerHTML : ''}</button>`;
    }).join('');
}

/** Re-syncs whether the right panel (and collapsed rail) is shown, purely
 * from whichever Settings toggles are already on — never itself flips
 * Pipeline logs (or any other toggle) on. The panel's open/closed state and
 * the Logs toggle used to be the same thing; now that Cost/WAF/Governance/
 * Suggestions/Logs are independent tabs, "reopen the panel" must not carry
 * the old side effect of silently re-enabling Logs. Only
 * toggleVerbosePanel() (the Logs toggle's own onchange) and
 * initVerboseLogging() (restoring a saved Logs preference) are allowed to
 * change verboseLogging itself. */
function setLogPanelOpen() {
    updateRightPanelVisibility();
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
}

function toggleVerbosePanel() {
    const open = document.getElementById('verbose-toggle').checked;
    verboseLogging = open;
    localStorage.setItem(VERBOSE_STORAGE_KEY, open ? '1' : '0');
    updateRightPanelVisibility();
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
    if (open) {
        appendVerboseLog('ui', 'Pipeline log panel opened', 'info');
        if (typeof switchInsightsTab === 'function') switchInsightsTab('logs');
    }
}

function hideVerboseLogsPanel() {
    verboseLogging = false;
    const toggle = document.getElementById('verbose-toggle');
    if (toggle) toggle.checked = false;
    localStorage.setItem(VERBOSE_STORAGE_KEY, '0');
    updateRightPanelVisibility();
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
}

function closeLogPanel() {
    hideValidationBanner();
    hideCostEstimate();
    if (typeof hideSuggestionsPanel === 'function') hideSuggestionsPanel();
    hideWellArchitectedPanel();
    hideCompliancePanel();
    if (typeof hideGovernancePanel === 'function') hideGovernancePanel();
    hideVerboseLogsPanel();
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
    verboseLogging = open;
    const toggle = document.getElementById('verbose-toggle');
    if (toggle) toggle.checked = open;
    updateRightPanelVisibility();
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
    updateLogCount();
}
