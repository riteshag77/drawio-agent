function openSettings() {
    document.getElementById('settings-backdrop').classList.add('open');
    document.getElementById('settings-drawer').classList.add('open');
    if (typeof loadCapturesList === 'function') loadCapturesList();
}

function closeSettings() {
    document.getElementById('settings-backdrop').classList.remove('open');
    document.getElementById('settings-drawer').classList.remove('open');
}

function toggleDrawerGroup(toggleBtn) {
    // Prefer the drawer-group that owns this toggle as a direct child, so nested
    // groups (e.g. WAF under Network) do not collapse their parent section.
    let group = null;
    if (toggleBtn && toggleBtn.parentElement && toggleBtn.parentElement.classList.contains('drawer-group')) {
        group = toggleBtn.parentElement;
    } else {
        group = toggleBtn?.closest('.drawer-group');
    }
    if (!group) return;
    const body = Array.from(group.children).find(
        (el) => el.classList && el.classList.contains('drawer-group-body')
    ) || group.querySelector(':scope > .drawer-group-body');
    const open = !group.classList.contains('is-open');
    group.classList.toggle('is-open', open);
    toggleBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (body) {
        if (open) body.removeAttribute('hidden');
        else body.setAttribute('hidden', '');
    }
}

function loadTemplates() {
    const list = document.getElementById('template-list');
    if (!list) return;
    let templates = [];
    try {
        templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
    } catch (_) { templates = []; }
    list.innerHTML = '';
    if (!templates.length) {
        list.innerHTML = '<small style="color:var(--text-muted)">No saved templates yet.</small>';
        return;
    }
    templates.forEach(t => {
        const row = document.createElement('div');
        row.className = 'template-row';
        const titleSpan = document.createElement('span');
        titleSpan.textContent = t.title || '';
        row.appendChild(titleSpan);
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'btn-chat-clear';
        useBtn.textContent = 'Use';
        useBtn.onclick = () => {
            document.getElementById('chat-input').value = t.prompt;
            autoResizeChatInput();
        };
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn-chat-clear';
        delBtn.textContent = '×';
        delBtn.onclick = () => deleteTemplate(t.id);
        row.appendChild(useBtn);
        row.appendChild(delBtn);
        list.appendChild(row);
    });
}

function saveCurrentAsTemplate() {
    const prompt = document.getElementById('chat-input').value.trim();
    if (!prompt) {
        showError('Enter a prompt to save as template.');
        return;
    }
    let templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
    const title = prompt.length > 24 ? prompt.slice(0, 24) + '…' : prompt;
    templates.unshift({
        id: 't_' + Date.now(),
        title,
        prompt,
        createdAt: Date.now(),
    });
    templates = templates.slice(0, 20);
    localStorage.setItem(TEMPLATES_STORAGE_KEY, JSON.stringify(templates));
    loadTemplates();
}

function deleteTemplate(id) {
    let templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
    templates = templates.filter(t => t.id !== id);
    localStorage.setItem(TEMPLATES_STORAGE_KEY, JSON.stringify(templates));
    loadTemplates();
}
