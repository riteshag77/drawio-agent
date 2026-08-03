/** Bring-your-own-key custom LLM providers — stored only in the browser
 * (localStorage), sent per-request as `custom_provider` when selected. Never
 * persisted server-side. Mirrors settings.js's "Saved templates" pattern. */

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
        document.getElementById('custom-provider-base-url').value = '';
        document.getElementById('custom-provider-model').value = '';
    }
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
            providers.forEach(p => list.appendChild(_buildCustomProviderRow(p)));
        }
    }
    if (typeof initProviderSelect === 'function') initProviderSelect();
    return providers;
}

function _buildCustomProviderRow(p) {
    const row = document.createElement('div');
    row.className = 'template-row';

    const label = document.createElement('span');
    const masked = p.api_key ? ('••••' + p.api_key.slice(-4)) : '(no key)';
    label.textContent = `${p.label} (${p.client}) ${masked}`;
    row.appendChild(label);

    const useBtn = document.createElement('button');
    useBtn.type = 'button';
    useBtn.className = 'btn-chat-clear';
    useBtn.textContent = 'Use';
    useBtn.onclick = () => useCustomProvider(p.id);
    row.appendChild(useBtn);

    const testBtn = document.createElement('button');
    testBtn.type = 'button';
    testBtn.className = 'btn-chat-clear';
    testBtn.textContent = 'Test';
    testBtn.onclick = () => testCustomProvider(p.id, testBtn);
    row.appendChild(testBtn);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn-chat-clear';
    delBtn.textContent = '×';
    delBtn.onclick = () => deleteCustomProvider(p.id);
    row.appendChild(delBtn);

    return row;
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
}

async function testCustomProvider(id, buttonEl) {
    const p = getCustomProviderById(id);
    if (!p) return;
    const original = buttonEl ? buttonEl.textContent : '';
    if (buttonEl) { buttonEl.disabled = true; buttonEl.textContent = '…'; }
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
        if (data.valid) {
            showToast(`${p.label}: connection OK (${data.latency_ms}ms)`, 'success');
        } else {
            showError(`${p.label}: ${data.error || 'Validation failed'}`);
        }
    } catch (err) {
        showError(`${p.label}: ${(err && err.message) || 'Validation request failed'}`);
    } finally {
        if (buttonEl) { buttonEl.disabled = false; buttonEl.textContent = original; }
    }
}
