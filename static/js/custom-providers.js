/** Bring-your-own-key custom LLM providers — stored only in the browser
 * (localStorage), sent per-request as `custom_provider` when selected. Never
 * persisted server-side. Mirrors settings.js's "Saved templates" pattern.
 * Each provider also remembers its last connectivity-test result so the
 * pass/fail status survives closing and reopening Settings, matching
 * next-ai-draw-io's per-model validation UX. */

const CUSTOM_PROVIDER_ICONS = {
    openai: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="12" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><circle cx="8.5" cy="13" r="1.2" fill="currentColor" stroke="none"/><circle cx="15.5" cy="13" r="1.2" fill="currentColor" stroke="none"/></svg>',
    anthropic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 18h-4l-1.6-4H9.6L8 21H4L12 3z"/><path d="M9.6 15h4.8"/></svg>',
    google: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4l3 2"/></svg>',
};

const CUSTOM_PROVIDER_LABELS = {
    openai: 'OpenAI-compatible',
    anthropic: 'Anthropic-compatible',
    google: 'Google-compatible',
};

function toggleCustomProviderForm(show) {
    const form = document.getElementById('custom-provider-form');
    const btn = document.getElementById('custom-provider-add-btn');
    if (!form || !btn) return;
    form.hidden = !show;
    btn.hidden = show;
    if (show) {
        document.getElementById('custom-provider-label').value = '';
        document.getElementById('custom-provider-client').value = 'openai';
        document.getElementById('custom-provider-api-key').value = '';
        document.getElementById('custom-provider-api-key').type = 'password';
        document.getElementById('custom-provider-base-url').value = '';
        document.getElementById('custom-provider-model').value = '';
        const eyeBtn = document.getElementById('custom-provider-key-toggle');
        if (eyeBtn) eyeBtn.setAttribute('aria-pressed', 'false');
    }
}

function toggleCustomProviderKeyVisibility(btn) {
    const input = document.getElementById('custom-provider-api-key');
    if (!input) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    btn.setAttribute('aria-pressed', showing ? 'false' : 'true');
}

function getCustomProviders() {
    try {
        return JSON.parse(localStorage.getItem(CUSTOM_PROVIDERS_STORAGE_KEY) || '[]');
    } catch (_) {
        return [];
    }
}

function getCustomProviderById(id) {
    return getCustomProviders().find(p => p.id === id) || null;
}

function loadCustomProviders() {
    const list = document.getElementById('custom-provider-list');
    const providers = getCustomProviders();
    if (list) {
        list.innerHTML = '';
        if (!providers.length) {
            list.innerHTML = '<small style="color:var(--text-muted)">No custom providers yet.</small>';
        } else {
            providers.forEach(p => list.appendChild(_buildCustomProviderCard(p)));
        }
    }
    if (typeof initProviderSelect === 'function') initProviderSelect();
    return providers;
}

function _providerTestStatusHtml(p) {
    if (p.lastTestStatus === 'pass') {
        return '<span class="provider-test-status pass" title="Last test passed"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg></span>';
    }
    if (p.lastTestStatus === 'fail') {
        return '<span class="provider-test-status fail" title="Last test failed"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg></span>';
    }
    return '<span class="provider-test-status idle" title="Not tested yet"></span>';
}

function _buildCustomProviderCard(p) {
    const card = document.createElement('div');
    card.className = 'provider-card';
    card.dataset.providerId = p.id;

    const header = document.createElement('div');
    header.className = 'provider-card-header';
    header.innerHTML = `
        <span class="provider-icon provider-icon-${p.client}">${CUSTOM_PROVIDER_ICONS[p.client] || CUSTOM_PROVIDER_ICONS.openai}</span>
        <span class="provider-card-title">
            <span class="provider-card-label">${p.label}</span>
            <span class="provider-card-client">${CUSTOM_PROVIDER_LABELS[p.client] || p.client}</span>
        </span>
        ${_providerTestStatusHtml(p)}`;
    card.appendChild(header);

    const meta = document.createElement('div');
    meta.className = 'provider-card-meta';
    const masked = p.api_key ? ('••••' + p.api_key.slice(-4)) : '(no key)';
    meta.textContent = `${p.model} · ${masked}`;
    card.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'provider-card-actions';

    const useBtn = document.createElement('button');
    useBtn.type = 'button';
    useBtn.className = 'btn-chat-clear';
    useBtn.textContent = 'Use';
    useBtn.onclick = () => useCustomProvider(p.id);
    actions.appendChild(useBtn);

    const testBtn = document.createElement('button');
    testBtn.type = 'button';
    testBtn.className = 'btn-chat-clear';
    testBtn.textContent = 'Test';
    testBtn.onclick = () => testCustomProvider(p.id, testBtn);
    actions.appendChild(testBtn);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn-chat-clear provider-card-delete';
    delBtn.textContent = '×';
    delBtn.title = 'Delete provider';
    delBtn.onclick = () => deleteCustomProvider(p.id);
    actions.appendChild(delBtn);

    card.appendChild(actions);
    return card;
}

function addCustomProvider() {
    const label = document.getElementById('custom-provider-label').value.trim() || 'Custom';
    const client = document.getElementById('custom-provider-client').value;
    const apiKey = document.getElementById('custom-provider-api-key').value.trim();
    const baseUrl = document.getElementById('custom-provider-base-url').value.trim();
    const model = document.getElementById('custom-provider-model').value.trim();
    if (!apiKey) {
        showError('Enter an API key for the custom provider.');
        return;
    }
    if (!model) {
        showError('Enter a model id for the custom provider.');
        return;
    }
    const providers = getCustomProviders();
    providers.push({
        id: 'cp_' + Date.now(),
        label, client, api_key: apiKey, base_url: baseUrl, model,
        lastTestStatus: null,
    });
    localStorage.setItem(CUSTOM_PROVIDERS_STORAGE_KEY, JSON.stringify(providers));
    toggleCustomProviderForm(false);
    loadCustomProviders();
}

function deleteCustomProvider(id) {
    const providers = getCustomProviders().filter(p => p.id !== id);
    localStorage.setItem(CUSTOM_PROVIDERS_STORAGE_KEY, JSON.stringify(providers));
    loadCustomProviders();
}

/** Returns the CustomProviderConfig payload for the current provider-select
 * value, or null when a regular (server-registered) provider is selected. */
function getSelectedCustomProviderPayload() {
    const select = document.getElementById('provider-select');
    if (!select || !select.value || !select.value.startsWith('custom:')) return null;
    const p = getCustomProviderById(select.value.slice('custom:'.length));
    if (!p) return null;
    return { label: p.label, client: p.client, api_key: p.api_key, base_url: p.base_url, model: p.model };
}

async function useCustomProvider(id) {
    const select = document.getElementById('provider-select');
    if (!select) return;
    const value = 'custom:' + id;
    if (!select.querySelector(`option[value="${CSS.escape(value)}"]`)) {
        await initProviderSelect();
    }
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    if (typeof renderProviderPickerButton === 'function') renderProviderPickerButton();
}

function _setProviderCardTestState(id, state) {
    const card = document.querySelector(`.provider-card[data-provider-id="${CSS.escape(id)}"]`);
    if (!card) return;
    const statusEl = card.querySelector('.provider-test-status');
    if (!statusEl) return;
    if (state === 'testing') {
        statusEl.className = 'provider-test-status testing';
        statusEl.title = 'Testing…';
        statusEl.innerHTML = '<span class="validation-spinner"></span>';
    } else {
        const providers = getCustomProviders();
        const p = providers.find((x) => x.id === id);
        statusEl.outerHTML = _providerTestStatusHtml(p || {});
    }
}

async function testCustomProvider(id, buttonEl) {
    const p = getCustomProviderById(id);
    if (!p) return;
    if (buttonEl) buttonEl.disabled = true;
    _setProviderCardTestState(id, 'testing');
    let passed = false;
    try {
        const res = await fetch('/providers/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                label: p.label, client: p.client, api_key: p.api_key,
                base_url: p.base_url, model: p.model,
            }),
        });
        const data = await res.json();
        passed = !!data.valid;
        if (passed) {
            showToast(`${p.label}: connection OK (${data.latency_ms}ms)`, 'success');
        } else {
            showError(`${p.label}: ${data.error || 'Validation failed'}`);
        }
    } catch (err) {
        showError(`${p.label}: ${(err && err.message) || 'Validation request failed'}`);
    } finally {
        if (buttonEl) buttonEl.disabled = false;
        const providers = getCustomProviders();
        const idx = providers.findIndex((x) => x.id === id);
        if (idx !== -1) {
            providers[idx].lastTestStatus = passed ? 'pass' : 'fail';
            localStorage.setItem(CUSTOM_PROVIDERS_STORAGE_KEY, JSON.stringify(providers));
        }
        _setProviderCardTestState(id, passed ? 'pass' : 'fail');
    }
}
