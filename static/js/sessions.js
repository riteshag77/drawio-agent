/** Cross-session picker. GET /history/sessions already lists every past
 * session (not just versions within the current one) — it existed server-side
 * but nothing in the frontend called it, so there was no way to reopen a
 * session after starting a new one. openSession() switches into one by
 * silently resetting the current chat, pointing the session id at the
 * target, then restoring its latest saved version (diagram + transcript,
 * via restoreDiagramVersion's messages handling). */

function openSessionsDialog() {
    document.getElementById('sessions-backdrop').hidden = false;
    document.getElementById('sessions-dialog').hidden = false;
    loadSessionsGrid();
}

function closeSessionsDialog() {
    document.getElementById('sessions-backdrop').hidden = true;
    document.getElementById('sessions-dialog').hidden = true;
}

async function loadSessionsGrid() {
    const grid = document.getElementById('sessions-grid');
    if (!grid) return;
    grid.innerHTML = '<p class="history-empty">Loading…</p>';
    try {
        const res = await fetch('/history/sessions?limit=50');
        const data = await res.json();
        const sessions = data.sessions || [];
        grid.innerHTML = sessions.map(sessionCardHtml).join('') || '<p class="history-empty">No past sessions yet.</p>';
    } catch (_) {
        grid.innerHTML = '<p class="history-empty">Could not load sessions.</p>';
    }
}

function sessionCardHtml(s) {
    const title = (s.title || 'Untitled').replace(/</g, '&lt;');
    const isCurrent = typeof currentSessionId !== 'undefined' && s.session_id === currentSessionId;
    const time = typeof historyRelativeTime === 'function' ? historyRelativeTime(s.last_updated) : '';
    return `
        <div class="history-card${isCurrent ? ' history-card-current' : ''}">
            <span class="history-card-version">${s.versions} version${s.versions === 1 ? '' : 's'}</span>
            <span class="history-card-title" title="${title}">${title}</span>
            <span class="history-card-time">${time}</span>
            <div class="history-card-actions">
                ${isCurrent
                    ? '<span class="history-card-current-label">Current</span>'
                    : `<button type="button" class="history-card-open" onclick="openSession('${s.session_id}')">Open</button>`}
                <button type="button" class="history-card-delete" onclick="deleteSessionFromPicker('${s.session_id}')" title="Delete session" aria-label="Delete session">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6"/></svg>
                </button>
            </div>
        </div>`;
}

async function openSession(sessionId) {
    if (typeof currentSessionId !== 'undefined' && sessionId === currentSessionId) {
        closeSessionsDialog();
        return;
    }
    let res;
    try {
        res = await fetch(`/history/sessions/${sessionId}?limit=50`);
    } catch (_) {
        showToast('Could not open that session.', 'error');
        return;
    }
    if (!res.ok) {
        showToast('Could not open that session.', 'error');
        return;
    }
    const data = await res.json();
    const versions = (data.versions || []).slice().sort((a, b) => a.version - b.version);
    if (!versions.length) {
        showToast('That session has no saved versions.', 'error');
        return;
    }

    if (typeof _resetChatState === 'function') _resetChatState();
    if (typeof setActiveSessionId === 'function') setActiveSessionId(sessionId);
    sessionVersions = versions;
    activeVersionId = null;
    if (typeof updateSessionUrlParam === 'function') updateSessionUrlParam(sessionId);

    await restoreDiagramVersion(versions[versions.length - 1].id);
    closeSessionsDialog();
    if (typeof setActionButtonsEnabled === 'function') setActionButtonsEnabled(true);
    document.getElementById('diagram-placeholder')?.classList.add('hidden');
    appendVerboseLog('ui', 'Opened session ' + sessionId, 'info');
}

async function deleteSessionFromPicker(sessionId) {
    if (!window.confirm('Delete this saved session? This cannot be undone.')) return;
    try {
        await fetch(`/history/sessions/${sessionId}`, { method: 'DELETE' });
        if (sessionId === currentSessionId) {
            sessionVersions = [];
            activeVersionId = null;
            if (typeof renderVersionStrip === 'function') renderVersionStrip();
        }
        loadSessionsGrid();
    } catch (_) {
        showToast('Could not delete session.', 'error');
    }
}
