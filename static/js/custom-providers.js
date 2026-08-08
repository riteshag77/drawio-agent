/** Bring-your-own-key custom LLM providers — stored only in the browser
 * (localStorage), sent per-request as `custom_provider` when selected. Never
 * persisted server-side. A provider can hold multiple candidate models
 * ("AI Model Configuration" dialog) — each becomes its own
 * selectable row in the provider picker, value-encoded as
 * `custom:<providerId>::<modelId>`, mirroring how server multi-model
 * providers (e.g. NVIDIA NIM) already work in initProviderSelect(). Each
 * provider also remembers its last connectivity-test result so the
 * pass/fail status survives closing and reopening the dialog. */

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

const _PC_ICON = {
    tag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41 11 3.83A2 2 0 0 0 9.59 3.24H4a1 1 0 0 0-1 1v5.59a2 2 0 0 0 .59 1.41l9.58 9.59a2 2 0 0 0 2.83 0l4.59-4.59a2 2 0 0 0 0-2.83z"/><circle cx="7.5" cy="7.5" r="1.5" fill="currentColor" stroke="none"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6M15.5 7.5 18 5l2 2-2.5 2.5"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    models: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/></svg>',
    config: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
};

/** null = no selection made yet (initial empty state); 'new' = draft
 * add-provider form; any other string = an existing provider's id. */
let _activeConfigProviderId = null;

function getCustomProviders() {
    let raw;
    try {
        raw = JSON.parse(localStorage.getItem(CUSTOM_PROVIDERS_STORAGE_KEY) || '[]');
    } catch (_) {
        return [];
    }
    // Migrate pre-multi-model entries (single `model` field) in place.
    return raw.map((p) => (p.models ? p : { ...p, models: p.model ? [p.model] : [] }));
}

function saveCustomProviders(list) {
    localStorage.setItem(CUSTOM_PROVIDERS_STORAGE_KEY, JSON.stringify(list));
}

function getCustomProviderById(id) {
    return getCustomProviders().find((p) => p.id === id) || null;
}

/** Settings-drawer quick-glance list — click opens the config dialog scoped
 * to that provider; the dialog (not this card) owns editing/testing/models. */
function loadCustomProviders() {
    const list = document.getElementById('custom-provider-summary-list');
    const providers = getCustomProviders();
    if (list) {
        list.innerHTML = providers.length
            ? providers.map(_summaryCardHtml).join('')
            : '<small style="color:var(--text-muted)">No custom providers yet.</small>';
    }
    if (typeof initProviderSelect === 'function') initProviderSelect();
    return providers;
}

function _summaryCardHtml(p) {
    const modelCount = (p.models || []).length;
    return `
        <div class="provider-card" data-provider-id="${p.id}" style="cursor:pointer" onclick="openProviderConfigDialog('${p.id}')">
            <div class="provider-card-header">
                <span class="provider-icon provider-icon-${p.client}">${CUSTOM_PROVIDER_ICONS[p.client] || CUSTOM_PROVIDER_ICONS.openai}</span>
                <span class="provider-card-title">
                    <span class="provider-card-label">${p.label}</span>
                    <span class="provider-card-client">${CUSTOM_PROVIDER_LABELS[p.client] || p.client}</span>
                </span>
                ${_providerTestStatusHtml(p)}
            </div>
            <div class="provider-card-meta">${modelCount} model${modelCount === 1 ? '' : 's'} configured</div>
        </div>`;
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

/* ── Config dialog ─────────────────────────────────────────────────── */

function openProviderConfigDialog(scopeToProviderId) {
    document.getElementById('provider-config-backdrop').hidden = false;
    document.getElementById('provider-config-dialog').hidden = false;
    const providers = getCustomProviders();
    const initial = scopeToProviderId || (providers.length ? providers[0].id : null);
    renderProviderConfigList();
    selectProviderConfig(initial);
}

function closeProviderConfigDialog() {
    document.getElementById('provider-config-backdrop').hidden = true;
    document.getElementById('provider-config-dialog').hidden = true;
    _activeConfigProviderId = null;
}

function renderProviderConfigList() {
    const container = document.getElementById('provider-config-list');
    if (!container) return;
    const providers = getCustomProviders();
    if (!providers.length) {
        container.innerHTML = '<div class="provider-config-list-empty">Add a provider to get started</div>';
        return;
    }
    container.innerHTML = providers.map((p) => `
        <button type="button" class="provider-config-list-item${p.id === _activeConfigProviderId ? ' active' : ''}" onclick="selectProviderConfig('${p.id}')">
            <span class="provider-icon provider-icon-${p.client}" style="width:22px;height:22px;">${CUSTOM_PROVIDER_ICONS[p.client] || CUSTOM_PROVIDER_ICONS.openai}</span>
            <span class="provider-config-list-item-label">${p.label}</span>
        </button>`).join('');
}

function selectProviderConfig(id) {
    _activeConfigProviderId = id;
    renderProviderConfigList();
    renderProviderConfigDetail();
}

function renderProviderConfigDetail() {
    const container = document.getElementById('provider-config-detail');
    if (!container) return;
    if (_activeConfigProviderId === null) {
        container.innerHTML = `
            <div class="provider-config-detail-empty">
                ${_PC_ICON.config}
                <h3>Configure AI Providers</h3>
                <p>Select a provider from the list or add a new one to configure API keys and models.</p>
            </div>`;
        return;
    }
    if (_activeConfigProviderId === 'new') {
        container.innerHTML = _newProviderFormHtml();
        return;
    }
    const p = getCustomProviderById(_activeConfigProviderId);
    if (!p) {
        _activeConfigProviderId = null;
        renderProviderConfigDetail();
        return;
    }
    container.innerHTML = _existingProviderDetailHtml(p);
}

function _newProviderFormHtml() {
    return `
        <div class="provider-config-detail-head">
            <div class="provider-config-detail-title"><h3>Add a provider</h3></div>
        </div>
        <div class="provider-config-field-label">${_PC_ICON.tag} Display Name</div>
        <input type="text" id="pc-new-label" placeholder="e.g. My Ollama">
        <div class="provider-config-field-label" style="margin-top:0.7rem">Client</div>
        <select id="pc-new-client">
            <option value="openai">OpenAI-compatible</option>
            <option value="anthropic">Anthropic-compatible</option>
            <option value="google">Google (Gemini)-compatible</option>
        </select>
        <div class="provider-config-field-label" style="margin-top:0.7rem">${_PC_ICON.key} API Key</div>
        <div class="provider-config-key-row">
            <input type="password" id="pc-new-key" placeholder="sk-…" autocomplete="off">
            <button type="button" class="icon-btn" onclick="toggleProviderConfigKeyVisibility(this,'pc-new-key')" aria-label="Show/hide key">${_PC_ICON.eye}</button>
        </div>
        <div class="provider-config-field-label">${_PC_ICON.link} Base URL (optional)</div>
        <input type="text" id="pc-new-base-url" placeholder="e.g. http://localhost:11434/v1" style="margin-bottom:0.7rem">
        <div class="provider-config-field-label">${_PC_ICON.models} First model ID</div>
        <input type="text" id="pc-new-model" placeholder="e.g. llama3.1">
        <div class="composer-actions" style="margin-top:1rem">
            <button type="button" class="btn-soft" onclick="createProviderConfig()">Save provider</button>
        </div>`;
}

function _existingProviderDetailHtml(p) {
    const masked = p.api_key ? ('••••' + p.api_key.slice(-4)) : '';
    const models = p.models || [];
    return `
        <div class="provider-config-detail-head">
            <span class="provider-icon provider-icon-${p.client}">${CUSTOM_PROVIDER_ICONS[p.client] || CUSTOM_PROVIDER_ICONS.openai}</span>
            <div class="provider-config-detail-title">
                <h3>${p.label}</h3>
                <small>${models.length} model(s) configured</small>
            </div>
            <button type="button" class="provider-config-delete-btn" onclick="deleteProviderConfig('${p.id}')">${_PC_ICON.trash} Delete Provider</button>
        </div>

        <div class="provider-config-section-label">${_PC_ICON.config} Configuration</div>
        <div class="provider-config-field-label">${_PC_ICON.tag} Display Name</div>
        <input type="text" id="pc-edit-label" value="${p.label}" style="margin-bottom:0.7rem">
        <div class="provider-config-field-label">${_PC_ICON.key} API Key</div>
        <div class="provider-config-key-row">
            <input type="password" id="pc-edit-key" placeholder="${masked || 'sk-…'}" autocomplete="off">
            <button type="button" class="icon-btn" onclick="toggleProviderConfigKeyVisibility(this,'pc-edit-key')" aria-label="Show/hide key">${_PC_ICON.eye}</button>
            <button type="button" class="btn-chat-clear" onclick="testProviderConfigConnection('${p.id}')">Test</button>
        </div>
        <div class="provider-config-field-label">${_PC_ICON.link} Base URL (optional)</div>
        <input type="text" id="pc-edit-base-url" value="${p.base_url || ''}" placeholder="https://api.example.com/v1" style="margin-bottom:0.4rem">
        <div id="provider-config-test-result-${p.id}" class="admin-test-result"></div>
        <div class="composer-actions" style="margin-top:0.5rem">
            <button type="button" class="btn-soft" onclick="saveProviderConfigCredentials('${p.id}')">Save changes</button>
        </div>

        <div class="provider-config-section-label">${_PC_ICON.models} Models</div>
        <div class="provider-config-model-row">
            <input type="text" id="pc-add-model-${p.id}" placeholder="Add a model ID…">
            <button type="button" class="btn-chat-clear" onclick="addProviderConfigModel('${p.id}')">＋ Add</button>
        </div>
        <div class="provider-config-model-list">
            ${models.length
                ? models.map((m) => `
                    <div class="provider-config-model-chip">
                        <span class="provider-config-model-chip-name">${m}</span>
                        <button type="button" class="provider-config-model-chip-remove" onclick="removeProviderConfigModel('${p.id}','${m.replace(/'/g, "\\'")}')" aria-label="Remove ${m}">✕</button>
                    </div>`).join('')
                : '<div class="provider-config-models-empty">No models yet — add one above.</div>'}
        </div>`;
}

function toggleProviderConfigKeyVisibility(btn, inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.type = input.type === 'text' ? 'password' : 'text';
}

function createProviderConfig() {
    const label = document.getElementById('pc-new-label').value.trim() || 'Custom';
    const client = document.getElementById('pc-new-client').value;
    const apiKey = document.getElementById('pc-new-key').value.trim();
    const baseUrl = document.getElementById('pc-new-base-url').value.trim();
    const model = document.getElementById('pc-new-model').value.trim();
    if (!apiKey) { showError('Enter an API key for the provider.'); return; }
    if (!model) { showError('Enter at least one model ID.'); return; }
    const providers = getCustomProviders();
    const id = 'cp_' + Date.now();
    providers.push({ id, label, client, api_key: apiKey, base_url: baseUrl, models: [model], lastTestStatus: null });
    saveCustomProviders(providers);
    loadCustomProviders();
    renderProviderConfigList();
    selectProviderConfig(id);
    showToast(`${label} added.`, 'success');
}

function saveProviderConfigCredentials(id) {
    const providers = getCustomProviders();
    const idx = providers.findIndex((p) => p.id === id);
    if (idx === -1) return;
    const label = document.getElementById('pc-edit-label').value.trim() || providers[idx].label;
    const newKey = document.getElementById('pc-edit-key').value.trim();
    const baseUrl = document.getElementById('pc-edit-base-url').value.trim();
    providers[idx] = {
        ...providers[idx],
        label,
        base_url: baseUrl,
        api_key: newKey || providers[idx].api_key,
        lastTestStatus: newKey ? null : providers[idx].lastTestStatus,
    };
    saveCustomProviders(providers);
    loadCustomProviders();
    renderProviderConfigList();
    renderProviderConfigDetail();
    showToast('Provider updated.', 'success');
}

function addProviderConfigModel(id) {
    const input = document.getElementById(`pc-add-model-${id}`);
    const model = input ? input.value.trim() : '';
    if (!model) return;
    const providers = getCustomProviders();
    const idx = providers.findIndex((p) => p.id === id);
    if (idx === -1) return;
    if (!providers[idx].models.includes(model)) providers[idx].models.push(model);
    saveCustomProviders(providers);
    loadCustomProviders();
    renderProviderConfigList();
    renderProviderConfigDetail();
}

function removeProviderConfigModel(id, model) {
    const providers = getCustomProviders();
    const idx = providers.findIndex((p) => p.id === id);
    if (idx === -1) return;
    providers[idx].models = providers[idx].models.filter((m) => m !== model);
    saveCustomProviders(providers);
    loadCustomProviders();
    renderProviderConfigList();
    renderProviderConfigDetail();
}

function deleteProviderConfig(id) {
    if (!window.confirm('Delete this provider and all its configured models?')) return;
    const providers = getCustomProviders().filter((p) => p.id !== id);
    saveCustomProviders(providers);
    loadCustomProviders();
    _activeConfigProviderId = providers.length ? providers[0].id : null;
    renderProviderConfigList();
    renderProviderConfigDetail();
}

async function testProviderConfigConnection(id) {
    const p = getCustomProviderById(id);
    if (!p) return;
    const resultEl = document.getElementById(`provider-config-test-result-${id}`);
    const testModel = (p.models && p.models[0]) || 'test';
    if (resultEl) { resultEl.textContent = 'Testing…'; resultEl.className = 'admin-test-result'; }
    let passed = false;
    let message = '';
    try {
        const res = await fetch('/providers/validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: p.label, client: p.client, api_key: p.api_key, base_url: p.base_url, model: testModel }),
        });
        const data = await res.json();
        passed = !!data.valid;
        message = passed ? `OK (${data.latency_ms}ms)` : (data.error || 'Validation failed');
    } catch (err) {
        message = (err && err.message) || 'Validation request failed';
    }
    if (resultEl) { resultEl.textContent = message; resultEl.className = 'admin-test-result ' + (passed ? 'ok' : 'fail'); }
    const providers = getCustomProviders();
    const idx = providers.findIndex((x) => x.id === id);
    if (idx !== -1) {
        providers[idx].lastTestStatus = passed ? 'pass' : 'fail';
        saveCustomProviders(providers);
    }
    loadCustomProviders();
    renderProviderConfigList();
}

/* ── Selection payload (used by app.js's sendMessage) ─────────────────── */

/** Returns the CustomProviderConfig payload for the current provider-select
 * value, or null when a regular (server-registered) provider is selected.
 * Value format is `custom:<providerId>::<modelId>`. */
function getSelectedCustomProviderPayload() {
    const select = document.getElementById('provider-select');
    if (!select || !select.value || !select.value.startsWith('custom:')) return null;
    const rest = select.value.slice('custom:'.length);
    const sep = rest.indexOf('::');
    const id = sep === -1 ? rest : rest.slice(0, sep);
    const model = sep === -1 ? '' : rest.slice(sep + 2);
    const p = getCustomProviderById(id);
    if (!p) return null;
    return { label: p.label, client: p.client, api_key: p.api_key, base_url: p.base_url, model: model || (p.models && p.models[0]) || '' };
}
