/** Full diagram history dialog — browses/restores/deletes backend-persisted versions. */

function openHistoryDialog() {
    document.getElementById('history-backdrop').hidden = false;
    document.getElementById('history-dialog').hidden = false;
    loadHistoryGrid();
}

function closeHistoryDialog() {
    document.getElementById('history-backdrop').hidden = true;
    document.getElementById('history-dialog').hidden = true;
}

function historyRelativeTime(isoString) {
    const then = new Date(isoString).getTime();
    if (Number.isNaN(then)) return '';
    const diffSec = Math.round((Date.now() - then) / 1000);
    if (diffSec < 60) return 'just now';
    const diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHour = Math.round(diffMin / 60);
    if (diffHour < 24) return `${diffHour}h ago`;
    const diffDay = Math.round(diffHour / 24);
    return `${diffDay}d ago`;
}

async function loadHistoryGrid() {
    const grid = document.getElementById('history-grid');
    if (!grid) return;
    if (typeof currentSessionId === 'undefined' || !currentSessionId) {
        grid.innerHTML = '<p class="history-empty">No saved versions yet.</p>';
        return;
    }
    grid.innerHTML = '<p class="history-empty">Loading…</p>';
    try {
        const res = await fetch(`/history/sessions/${currentSessionId}?limit=50`);
        const data = await res.json();
        const versions = (data.versions || []).slice().sort((a, b) => b.version - a.version);
        grid.innerHTML = versions.map(historyCardHtml).join('') || '<p class="history-empty">No saved versions yet.</p>';
    } catch (_) {
        grid.innerHTML = '<p class="history-empty">Could not load history.</p>';
    }
}

function historyCardHtml(v) {
    const title = (v.title || v.prompt || 'Untitled').replace(/</g, '&lt;');
    return `
        <div class="history-card">
            <span class="history-card-version">v${v.version}${v.diagram_type === 'mermaid' ? ' · mermaid' : ''}</span>
            <span class="history-card-title" title="${title}">${title}</span>
            <span class="history-card-time">${historyRelativeTime(v.created_at)}</span>
            <div class="history-card-actions">
                <button type="button" class="history-card-open" onclick="restoreFromHistoryDialog('${v.id}')">Restore</button>
            </div>
        </div>`;
}

async function restoreFromHistoryDialog(versionId) {
    await restoreDiagramVersion(versionId);
    closeHistoryDialog();
}

async function clearSessionHistory() {
    if (typeof currentSessionId === 'undefined' || !currentSessionId) return;
    if (!window.confirm("Clear this session's diagram history? This can't be undone.")) return;
    try {
        await fetch(`/history/sessions/${currentSessionId}`, { method: 'DELETE' });
        sessionVersions = [];
        activeVersionId = null;
        if (typeof renderVersionStrip === 'function') renderVersionStrip();
        loadHistoryGrid();
    } catch (_) {
        showToast('Could not clear history.', 'error');
    }
}
