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
