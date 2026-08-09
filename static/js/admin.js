/** Admin panel — password-gated provider/default-provider management.
 * Auth token is a deterministic HMAC of ADMIN_PASSWORD (see admin_routes.py),
 * not a session — stored in localStorage and sent as X-Admin-Token. */

const ADMIN_TOKEN_KEY = 'drawio-agent-admin-token';

function adminToken() {
    return localStorage.getItem(ADMIN_TOKEN_KEY) || '';
}

async function adminFetch(path, options) {
    const opts = options || {};
    opts.headers = Object.assign({ 'X-Admin-Token': adminToken() }, opts.headers || {});
    const res = await fetch(path, opts);
    if (res.status === 401) {
        localStorage.removeItem(ADMIN_TOKEN_KEY);
        showAdminLogin();
    }
    return res;
}

function showAdminLogin() {
    document.getElementById('admin-not-configured').hidden = true;
    document.getElementById('admin-login').hidden = false;
    document.getElementById('admin-main').hidden = true;
}

async function adminLogin() {
    const password = document.getElementById('admin-password-input').value;
    const errEl = document.getElementById('admin-login-error');
    errEl.textContent = '';
    try {
        const res = await fetch('/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            errEl.textContent = data.detail || 'Login failed.';
            return;
        }
        const data = await res.json();
        localStorage.setItem(ADMIN_TOKEN_KEY, data.token);
        await adminLoadMain();
    } catch (err) {
        errEl.textContent = 'Network error.';
    }
}

function adminSourceBadge(source) {
    const label = source === 'admin' ? 'Admin-set' : source === 'env' ? 'From .env' : 'Not set';
    return `<span class="admin-source-badge ${source}">${label}</span>`;
}

function adminProviderCardHtml(p) {
    const keyRow = `
        <div class="admin-field-row">
            <input type="password" id="admin-key-${p.key}" placeholder="${p.api_key_set ? p.api_key_hint : 'sk-…'}">
            <button type="button" class="btn-chat-clear" onclick="adminSaveProvider('${p.key}')">Save</button>
            ${p.api_key_source === 'admin' ? `<button type="button" class="btn-chat-clear" onclick="adminClearProviderKey('${p.key}')">Revert</button>` : ''}
        </div>`;
    const baseUrlRow = p.base_url_env ? `
        <div class="admin-field-row">
            <input type="text" id="admin-baseurl-${p.key}" placeholder="${p.base_url_value || 'Base URL (optional)'}">
            <button type="button" class="btn-chat-clear" onclick="adminSaveProviderBaseUrl('${p.key}')">Save</button>
        </div>` : '';
    return `
        <div class="admin-provider-card" data-provider-key="${p.key}">
            <div class="admin-provider-head">
                <span class="admin-provider-label">${p.label}${p.is_default ? ' (default)' : ''}</span>
                <span class="admin-provider-env">${p.api_key_env}</span>
                ${adminSourceBadge(p.api_key_source)}
            </div>
            ${keyRow}
            ${baseUrlRow}
            <div class="admin-provider-actions">
                <button type="button" class="btn-chat-clear" onclick="adminTestProvider('${p.key}')">Test connection</button>
                <span id="admin-test-result-${p.key}" class="admin-test-result"></span>
            </div>
        </div>`;
}

async function adminLoadMain() {
    const res = await adminFetch('/admin/providers');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('admin-login').hidden = true;
    document.getElementById('admin-not-configured').hidden = true;
    document.getElementById('admin-main').hidden = false;

    const select = document.getElementById('admin-default-provider-select');
    select.innerHTML = data.providers.map((p) => `<option value="${p.key}"${p.key === data.default_provider ? ' selected' : ''}>${p.label}</option>`).join('');

    document.getElementById('admin-provider-list').innerHTML = data.providers.map(adminProviderCardHtml).join('');

    if (typeof adminGovernanceKindChanged === 'function') adminGovernanceKindChanged();
    if (typeof adminLoadGovernanceRules === 'function') adminLoadGovernanceRules();
}

async function adminSaveProvider(key) {
    const input = document.getElementById(`admin-key-${key}`);
    const value = input.value.trim();
    if (!value) return;
    const res = await adminFetch(`/admin/providers/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: value }),
    });
    if (res.ok) {
        showToast(`${key}: API key saved.`, 'success');
        adminLoadMain();
    } else {
        showToast(`${key}: could not save.`, 'error');
    }
}

async function adminSaveProviderBaseUrl(key) {
    const input = document.getElementById(`admin-baseurl-${key}`);
    const value = input.value.trim();
    const res = await adminFetch(`/admin/providers/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: value }),
    });
    if (res.ok) {
        showToast(`${key}: base URL saved.`, 'success');
        adminLoadMain();
    } else {
        showToast(`${key}: could not save.`, 'error');
    }
}

async function adminClearProviderKey(key) {
    const res = await adminFetch(`/admin/providers/${key}/api-key`, { method: 'DELETE' });
    if (res.ok) {
        showToast(`${key}: reverted to .env.`, 'success');
        adminLoadMain();
    }
}

async function adminTestProvider(key) {
    const resultEl = document.getElementById(`admin-test-result-${key}`);
    resultEl.textContent = 'Testing…';
    resultEl.className = 'admin-test-result';
    const res = await adminFetch(`/admin/providers/${key}/test`, { method: 'POST' });
    const data = await res.json();
    if (data.valid) {
        resultEl.textContent = `OK (${data.latency_ms}ms)`;
        resultEl.className = 'admin-test-result ok';
    } else {
        resultEl.textContent = data.error || 'Failed';
        resultEl.className = 'admin-test-result fail';
    }
}

async function adminSaveDefaultProvider() {
    const provider = document.getElementById('admin-default-provider-select').value;
    const res = await adminFetch('/admin/default-provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
    });
    if (res.ok) {
        showToast('Default provider saved.', 'success');
        adminLoadMain();
    } else {
        showToast('Could not save default provider.', 'error');
    }
}

// ── Governance / house-style rules ───────────────────────────────────────

function _escHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str == null ? '' : String(str)));
    return div.innerHTML;
}

//: Which param fields each rule kind shows, and a plain-language explanation
//: of exactly what it checks — shown under the kind picker so an admin never
//: has to guess what a rule kind means before writing one.
const GOV_KIND_INFO = {
    required_corridor: { fields: ['corridor'], help: 'Fails if the named zone/corridor is missing from the diagram entirely.' },
    forbidden_corridor: { fields: ['corridor'], help: 'Fails if the named zone/corridor is present anywhere in the diagram.' },
    required_leaf_shape: { fields: ['shape'], help: 'Fails if no service in the diagram has a name matching the text given (e.g. every diagram must have a WAF).' },
    forbidden_shape_type: { fields: ['shape'], help: 'Fails if any service in the diagram has a name matching the text given (e.g. ban a deprecated service).' },
    required_overview_field: { fields: ['overview'], help: 'Fails if the chosen summary field (shown in the diagram’s Overview panel) was left blank.' },
    min_narrative_bullets: { fields: ['overview', 'mincount'], help: 'Fails if the chosen narrative field has fewer bullet points than the minimum required.' },
    required_flow_protection: { fields: ['protection'], help: 'Fails if a connection carrying PII/PCI data, or crossing a public/partner boundary, is encrypted below the minimum level.' },
    forbidden_label_pattern: { fields: ['pattern'], help: 'Fails if any service or zone name matches the forbidden text pattern (e.g. ban raw database table names from appearing).' },
};

const GOV_KIND_LABELS = {
    required_corridor: 'Requires zone', forbidden_corridor: 'Forbids zone',
    required_leaf_shape: 'Requires service', forbidden_shape_type: 'Forbids service',
    required_overview_field: 'Requires field', min_narrative_bullets: 'Min. bullet points',
    required_flow_protection: 'Min. encryption', forbidden_label_pattern: 'Forbidden pattern',
};

function adminGovernanceKindChanged() {
    const kind = document.getElementById('gov-kind').value;
    const info = GOV_KIND_INFO[kind] || { fields: [], help: '' };
    document.getElementById('gov-kind-help').textContent = info.help;
    const groups = {
        corridor: 'gov-param-corridor', shape: 'gov-param-shape', overview: 'gov-param-overview',
        mincount: 'gov-param-mincount', protection: 'gov-param-protection', pattern: 'gov-param-pattern',
    };
    Object.entries(groups).forEach(([key, id]) => {
        document.getElementById(id).hidden = !info.fields.includes(key);
    });
}

function adminGovernanceRuleDetail(r) {
    switch (r.kind) {
        case 'required_corridor': case 'forbidden_corridor': return r.corridor_id || '';
        case 'required_leaf_shape': case 'forbidden_shape_type': return r.shape_type || '';
        case 'required_overview_field': return r.overview_field || '';
        case 'min_narrative_bullets': return `${r.overview_field || ''} ≥ ${r.min_count}`;
        case 'required_flow_protection': return `${r.min_protection}${r.boundary_only ? ' — boundary-crossing flows' : ' — PII/PCI flows only'}`;
        case 'forbidden_label_pattern': return r.label_pattern || '';
        default: return '';
    }
}

function adminGovernanceCardHtml(r) {
    const scope = `${r.view === 'any' ? 'Any view' : r.view} · ${r.platform === 'any' ? 'any platform' : r.platform.toUpperCase()}`;
    return `
        <div class="admin-governance-card${r.enabled ? '' : ' disabled'}">
            <div class="admin-governance-card-head">
                <span class="admin-governance-kind-badge">${GOV_KIND_LABELS[r.kind] || r.kind}</span>
                <span class="admin-governance-scope">${scope}</span>
            </div>
            <div class="admin-governance-message">${_escHtml(r.message)}</div>
            <div class="admin-help-text">${_escHtml(adminGovernanceRuleDetail(r))}</div>
            <div class="admin-governance-actions">
                <label class="admin-checkbox-label"><input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="adminToggleGovernanceRule('${r.id}', this.checked)"> Enabled</label>
                <button type="button" class="btn-chat-clear" onclick="adminDeleteGovernanceRule('${r.id}')">Delete</button>
            </div>
        </div>`;
}

async function adminLoadGovernanceRules() {
    const res = await adminFetch('/admin/governance-rules');
    if (!res.ok) return;
    const data = await res.json();
    const list = document.getElementById('admin-governance-list');
    list.innerHTML = (data.rules || []).length
        ? data.rules.map(adminGovernanceCardHtml).join('')
        : '<div class="admin-governance-empty">No governance rules yet — add one above to start enforcing house style.</div>';
}

async function adminCreateGovernanceRule() {
    const message = document.getElementById('gov-message').value.trim();
    if (!message) {
        showToast('A message explaining the rule is required.', 'error');
        return;
    }
    const body = {
        kind: document.getElementById('gov-kind').value,
        view: document.getElementById('gov-view').value,
        platform: document.getElementById('gov-platform').value,
        message,
        corridor_id: document.getElementById('gov-corridor-id').value.trim() || null,
        shape_type: document.getElementById('gov-shape-type').value.trim() || null,
        overview_field: document.getElementById('gov-overview-field').value,
        min_count: parseInt(document.getElementById('gov-min-count').value, 10) || 1,
        min_protection: document.getElementById('gov-min-protection').value,
        boundary_only: document.getElementById('gov-boundary-only').checked,
        label_pattern: document.getElementById('gov-label-pattern').value.trim() || null,
    };
    const res = await adminFetch('/admin/governance-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (res.ok) {
        showToast('Rule added.', 'success');
        document.getElementById('gov-message').value = '';
        document.getElementById('gov-corridor-id').value = '';
        document.getElementById('gov-shape-type').value = '';
        document.getElementById('gov-label-pattern').value = '';
        adminLoadGovernanceRules();
    } else {
        const data = await res.json().catch(() => ({}));
        showToast(data.detail || 'Could not add rule.', 'error');
    }
}

async function adminToggleGovernanceRule(id, enabled) {
    const res = await adminFetch(`/admin/governance-rules/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
    });
    showToast(res.ok ? (enabled ? 'Rule enabled.' : 'Rule disabled.') : 'Could not update rule.', res.ok ? 'success' : 'error');
    adminLoadGovernanceRules();
}

async function adminDeleteGovernanceRule(id) {
    if (!window.confirm('Delete this governance rule? This cannot be undone.')) return;
    const res = await adminFetch(`/admin/governance-rules/${id}`, { method: 'DELETE' });
    if (res.ok) {
        showToast('Rule deleted.', 'success');
        adminLoadGovernanceRules();
    }
}

(async function initAdminPage() {
    const statusRes = await fetch('/admin/status');
    const status = await statusRes.json();
    if (!status.configured) {
        document.getElementById('admin-not-configured').hidden = false;
        return;
    }
    if (adminToken()) {
        await adminLoadMain();
    } else {
        showAdminLogin();
    }
})();
