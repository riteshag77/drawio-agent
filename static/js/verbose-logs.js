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

/** Reflects rightPanelOpen onto the DOM — the panel's own expand/collapse
 * state, independent of which tabs are toggled on. Call openRightPanel() /
 * closeRightPanel() to actually change it; this function only re-renders. */
function updateRightPanelVisibility() {
    document.getElementById('log-panel')?.classList.toggle('active', rightPanelOpen);
    const reopenTab = document.getElementById('log-panel-reopen-tab');
    if (reopenTab) reopenTab.hidden = rightPanelOpen;
    if (!rightPanelOpen && typeof renderCollapsedRailTabs === 'function') renderCollapsedRailTabs();
}

/** Expands the panel — called when a toggle is switched on (auto-open
 * convenience) or a reopen affordance is clicked. Never called just because
 * a toggle turns off; turning a tab off only hides that tab (see
 * updateInsightsTabVisibility), it must never collapse the whole panel. */
function openRightPanel() {
    rightPanelOpen = true;
    localStorage.setItem(RIGHT_PANEL_OPEN_KEY, '1');
    updateRightPanelVisibility();
}

function closeRightPanel() {
    rightPanelOpen = false;
    localStorage.setItem(RIGHT_PANEL_OPEN_KEY, '0');
    updateRightPanelVisibility();
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

/** Explicit "reopen" affordance (the collapsed-rail arrow and its per-tab
 * icons) — expands the panel without touching any individual toggle. */
function setLogPanelOpen() {
    openRightPanel();
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
}

function toggleVerbosePanel() {
    const open = document.getElementById('verbose-toggle').checked;
    verboseLogging = open;
    localStorage.setItem(VERBOSE_STORAGE_KEY, open ? '1' : '0');
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
    if (open) {
        openRightPanel();
        appendVerboseLog('ui', 'Pipeline log panel opened', 'info');
        if (typeof switchInsightsTab === 'function') switchInsightsTab('logs');
    } else {
        updateRightPanelVisibility();
    }
}

/** X button — collapses the panel only. Deliberately does NOT touch any
 * individual toggle (Cost/WAF/Compliance/Governance/Suggestions/Logs) —
 * panel open/closed is independent of tab content, so reopening (via the
 * collapsed rail) shows exactly the same tabs as before closing. */
function closeLogPanel() {
    hideValidationBanner();
    closeRightPanel();
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

/** Restores the saved Logs preference into the checkbox/variable only —
 * does NOT touch panel open/closed state. initPromptSettings() does the
 * authoritative rightPanelOpen restore once, after every toggle (including
 * this one) has been synced, so a reload never silently reopens the panel
 * just because a toggle happens to still be checked from a prior session. */
function initVerboseLogging() {
    const open = localStorage.getItem(VERBOSE_STORAGE_KEY) === '1';
    verboseLogging = open;
    const toggle = document.getElementById('verbose-toggle');
    if (toggle) toggle.checked = open;
    updateLogCount();
}
