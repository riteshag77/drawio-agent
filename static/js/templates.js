/** Saved prompt templates — backend-persisted CRUD list in the Settings drawer. */

let templateCache = [];

async function loadTemplates(query) {
    const list = document.getElementById('template-list');
    if (!list) return;
    const q = query !== undefined ? query : (document.getElementById('template-search')?.value || '');
    try {
        const res = await fetch(`/templates?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        templateCache = data.templates || [];
    } catch (_) {
        list.innerHTML = '<small style="color:var(--text-muted)">Could not load templates.</small>';
        return;
    }
    renderTemplateList();
}

function onTemplateSearchInput() {
    clearTimeout(window._templateSearchDebounce);
    window._templateSearchDebounce = setTimeout(() => loadTemplates(), 200);
}

function renderTemplateList() {
    const list = document.getElementById('template-list');
    if (!list) return;
    list.innerHTML = '';
    if (!templateCache.length) {
        list.innerHTML = '<small style="color:var(--text-muted)">No saved templates yet.</small>';
        return;
    }
    templateCache.forEach((t) => {
        const row = document.createElement('div');
        row.className = 'template-row';

        const pinBtn = document.createElement('button');
        pinBtn.type = 'button';
        pinBtn.className = 'btn-chat-clear template-pin-btn';
        pinBtn.title = t.pinned ? 'Unpin' : 'Pin';
        pinBtn.innerHTML = t.pinned
            ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.77 5.82 21 7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.77 5.82 21 7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';
        pinBtn.onclick = () => togglePinTemplate(t.id, !t.pinned);
        row.appendChild(pinBtn);

        const titleSpan = document.createElement('span');
        titleSpan.textContent = t.title || (t.prompt.length > 40 ? t.prompt.slice(0, 40) + '…' : t.prompt);
        titleSpan.title = t.description || t.prompt;
        row.appendChild(titleSpan);

        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'btn-chat-clear';
        useBtn.textContent = 'Use';
        useBtn.onclick = () => useTemplate(t.id);
        row.appendChild(useBtn);

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn-chat-clear template-delete-btn';
        delBtn.title = 'Delete';
        delBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
        delBtn.onclick = () => deleteTemplate(t.id);
        row.appendChild(delBtn);

        list.appendChild(row);
    });
}

async function saveCurrentAsTemplate() {
    const prompt = document.getElementById('chat-input').value.trim();
    if (!prompt) {
        showError('Enter a prompt to save as template.');
        return;
    }
    const title = prompt.length > 40 ? prompt.slice(0, 40) + '…' : prompt;
    try {
        const res = await fetch('/templates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, prompt }),
        });
        if (!res.ok) throw new Error('save failed');
        showToast('Template saved.', 'success');
        loadTemplates();
    } catch (_) {
        showError('Could not save template.');
    }
}

async function useTemplate(id) {
    const input = document.getElementById('chat-input');
    if (input.value.trim() && !window.confirm('Replace current message with this template?')) return;
    try {
        const res = await fetch(`/templates/${id}/use`, { method: 'POST' });
        if (!res.ok) throw new Error('use failed');
        const data = await res.json();
        input.value = data.prompt;
        autoResizeChatInput();
        closeSettings();
    } catch (_) {
        showError('Could not load template.');
    }
}

async function deleteTemplate(id) {
    try {
        await fetch(`/templates/${id}`, { method: 'DELETE' });
        loadTemplates();
    } catch (_) {
        showError('Could not delete template.');
    }
}

async function togglePinTemplate(id, pinned) {
    try {
        await fetch(`/templates/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pinned }),
        });
        loadTemplates();
    } catch (_) {
        showError('Could not update template.');
    }
}
