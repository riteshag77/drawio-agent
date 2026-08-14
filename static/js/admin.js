/** Admin panel — password-gated provider/default-provider management.
 * Auth token is a deterministic HMAC of ADMIN_PASSWORD (see admin_routes.py),
 * not a session — stored in localStorage and sent as X-Admin-Token. */

const ADMIN_TOKEN_KEY = 'drawio-agent-admin-token';

const EYE_OPEN_SVG = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
const EYE_OFF_SVG = '<path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.4 21.4 0 0 1 5.06-6.06M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.4 21.4 0 0 1-2.94 4.06"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';

function adminToken() {
    return localStorage.getItem(ADMIN_TOKEN_KEY) || '';
}

/** Toggles a password-type input to text and swaps the eye icon — shared by
 * the login field and every provider API-key field. */
function adminTogglePasswordVisibility(inputId, btnEl) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    btnEl.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${showing ? EYE_OPEN_SVG : EYE_OFF_SVG}</svg>`;
    btnEl.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    btnEl.title = showing ? 'Show password' : 'Hide password';
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

/** Providers and Governance are mutually-exclusive sections, not one long
 * scroll — switching shows exactly one section's data at a time so a long
 * governance rule list never bleeds into view under Providers (or vice
 * versa). */
function adminSwitchSection(section) {
    ['providers', 'governance'].forEach((s) => {
        const panel = document.getElementById(`admin-section-${s}`);
        const btn = document.getElementById(`admin-subnav-${s}`);
        if (panel) panel.hidden = s !== section;
        if (btn) btn.classList.toggle('active', s === section);
    });
}

function adminLogout() {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
    showAdminLogin();
    const input = document.getElementById('admin-password-input');
    if (input) { input.value = ''; input.focus(); }
}

async function adminLogin() {
    const input = document.getElementById('admin-password-input');
    const password = input.value;
    const errEl = document.getElementById('admin-login-error');
    const btn = document.getElementById('admin-login-btn');
    const btnLabel = document.getElementById('admin-login-btn-label');
    errEl.innerHTML = '';
    if (!password) {
        input.classList.remove('shake'); void input.offsetWidth; input.classList.add('shake');
        return;
    }
    btn.disabled = true;
    btnLabel.innerHTML = '<span class="admin-spinner"></span>';
    try {
        const res = await fetch('/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            errEl.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span>${_escHtml(data.detail || 'Login failed.')}</span>`;
            input.classList.remove('shake'); void input.offsetWidth; input.classList.add('shake');
            return;
        }
        const data = await res.json();
        localStorage.setItem(ADMIN_TOKEN_KEY, data.token);
        await adminLoadMain();
    } catch (err) {
        errEl.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span>Network error.</span>`;
    } finally {
        btn.disabled = false;
        btnLabel.textContent = 'Log in';
    }
}

function adminSourceBadge(source) {
    const label = source === 'admin' ? 'Admin-set' : source === 'env' ? 'From .env' : 'Not set';
    return `<span class="admin-source-badge ${source}">${label}</span>`;
}

function adminProviderCardHtml(p) {
    const avatarLetter = (p.label || p.key || '?').trim().charAt(0).toUpperCase();
    const keyInputId = `admin-key-${p.key}`;
    const keyRow = `
        <div class="admin-field-row">
            <div class="admin-password-field">
                <input type="password" id="${keyInputId}" placeholder="${p.api_key_set ? p.api_key_hint : 'sk-…'}">
                <button type="button" class="admin-password-toggle" onclick="adminTogglePasswordVisibility('${keyInputId}', this)" aria-label="Show key" title="Show key">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${EYE_OPEN_SVG}</svg>
                </button>
            </div>
            <button type="button" class="admin-icon-btn" onclick="adminSaveProvider('${p.key}')" title="Save key" aria-label="Save key">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
            </button>
            ${p.api_key_source === 'admin' ? `
            <button type="button" class="admin-icon-btn danger" onclick="adminClearProviderKey('${p.key}')" title="Revert to .env" aria-label="Revert to .env">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
            </button>` : ''}
        </div>`;
    const baseUrlRow = p.base_url_env ? `
        <div class="admin-field-row">
            <input type="text" id="admin-baseurl-${p.key}" placeholder="${p.base_url_value || 'Base URL (optional)'}">
            <button type="button" class="admin-icon-btn" onclick="adminSaveProviderBaseUrl('${p.key}')" title="Save base URL" aria-label="Save base URL">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
            </button>
        </div>` : '';
    return `
        <div class="admin-card admin-provider-card" data-provider-key="${p.key}">
            <div class="admin-provider-head">
                <span class="admin-provider-avatar">${_escHtml(avatarLetter)}</span>
                <div class="admin-provider-name-col">
                    <span class="admin-provider-label">${_escHtml(p.label)}${p.is_default ? ' <span class="admin-default-badge">Default</span>' : ''}</span>
                    <span class="admin-provider-env">${_escHtml(p.api_key_env)}</span>
                </div>
                ${adminSourceBadge(p.api_key_source)}
            </div>
            ${keyRow}
            ${baseUrlRow}
            <div class="admin-provider-actions">
                <button type="button" class="admin-test-btn" onclick="adminTestProvider('${p.key}')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                    Test connection
                </button>
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
    if (typeof adminGovernanceViewChanged === 'function') adminGovernanceViewChanged();
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
    resultEl.innerHTML = '<span class="admin-spinner dark"></span>';
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

//: FastAPI's default validation-error body is {"detail": [{"msg": "...", "loc": [...]}]} —
//: an array of objects, not a string. Passing that straight to showToast (which sets
//: textContent) renders literal "[object Object]"; this always resolves to plain text.
function _adminErrorMessage(data, fallback) {
    const detail = data && data.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
        return detail.map(e => (e && e.msg) ? e.msg : JSON.stringify(e)).join('; ');
    }
    return fallback;
}

//: Non-null while an existing rule is loaded into the form for editing —
//: adminSubmitGovernanceRule branches POST (create) vs PATCH (update) on this.
let _adminGovernanceEditId = null;

function adminGovernanceMessageInput() {
    const el = document.getElementById('gov-message');
    document.getElementById('gov-message-count').textContent = `${el.value.length} / 300`;
}

const _GOV_ADD_ICON = '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>';
const _GOV_UPDATE_ICON = '<polyline points="20 6 9 17 4 12"/>';

function _adminSetGovernanceSubmitButton(editing) {
    const btn = document.getElementById('admin-add-rule-btn');
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">${editing ? _GOV_UPDATE_ICON : _GOV_ADD_ICON}</svg>${editing ? 'Update rule' : 'Add rule'}`;
    document.getElementById('admin-cancel-edit-btn').hidden = !editing;
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

//: Each overview-field <option> carries data-views (see admin.html) listing
//: exactly which views that field exists on (e.g. threat_model_reference is
//: Security-only, panel_summary is ArchiMate-only). Without this, an admin
//: could pick "Security" as the view and "Panel summary (ArchiMate)" as the
//: field — a combination that would always fail, silently, since that field
//: genuinely doesn't exist on SecurityIR (getattr falls back to None, which
//: reads as "empty" forever). "Any view" shows every field, since an
//: any-scoped rule is evaluated against whichever IR it happens to run
//: against and any of them could be the relevant one.
function adminGovernanceViewChanged() {
    const view = document.getElementById('gov-view').value;
    const select = document.getElementById('gov-overview-field');
    const hint = document.getElementById('gov-overview-field-hint');
    let anyVisible = false;
    let selectedStillVisible = false;
    Array.from(select.options).forEach((opt) => {
        const views = (opt.dataset.views || '').split(',');
        const visible = view === 'any' || views.includes(view);
        opt.hidden = !visible;
        if (visible) {
            anyVisible = true;
            if (opt.value === select.value) selectedStillVisible = true;
        }
    });
    if (!selectedStillVisible) {
        const firstVisible = Array.from(select.options).find((opt) => !opt.hidden);
        if (firstVisible) select.value = firstVisible.value;
    }
    hint.textContent = view === 'any'
        ? ''
        : 'Only fields that exist on this view\'s overview panel are shown.';
    if (!anyVisible) hint.textContent = 'No overview fields exist on this view.';
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
    const editing = r.id === _adminGovernanceEditId;
    return `
        <div class="admin-card admin-governance-card${r.enabled ? '' : ' disabled'}${editing ? ' editing' : ''}">
            <div class="admin-governance-card-head">
                <span class="admin-governance-kind-badge">${GOV_KIND_LABELS[r.kind] || r.kind}</span>
                <span class="admin-governance-scope">${_escHtml(scope)}</span>
            </div>
            <div class="admin-governance-message">${_escHtml(r.message)}</div>
            <div class="admin-help-text">${_escHtml(adminGovernanceRuleDetail(r))}</div>
            <div class="admin-governance-actions">
                <label class="toggle-switch" title="Enable/disable this rule">
                    <input type="checkbox" ${r.enabled ? 'checked' : ''} onchange="adminToggleGovernanceRule('${r.id}', this.checked)">
                    <span class="toggle-slider"></span>
                </label>
                <span class="admin-help-text">${r.enabled ? 'Enabled' : 'Disabled'}</span>
                <button type="button" class="admin-icon-btn" onclick="adminEditGovernanceRule('${r.id}')" title="Edit rule" aria-label="Edit rule" style="margin-left:auto">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                </button>
                <button type="button" class="admin-icon-btn danger" onclick="adminDeleteGovernanceRule('${r.id}')" title="Delete rule" aria-label="Delete rule">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
                </button>
            </div>
        </div>`;
}

//: Populated by every adminLoadGovernanceRules() call — lets Edit look a rule
//: up by id without re-fetching or inlining rule data into onclick HTML
//: (rule messages are free text and could contain quote characters that
//: would break out of an inline attribute).
let _adminGovernanceRulesCache = [];

async function adminLoadGovernanceRules() {
    const res = await adminFetch('/admin/governance-rules');
    if (!res.ok) return;
    const data = await res.json();
    _adminGovernanceRulesCache = data.rules || [];
    const list = document.getElementById('admin-governance-list');
    list.innerHTML = _adminGovernanceRulesCache.length
        ? _adminGovernanceRulesCache.map(adminGovernanceCardHtml).join('')
        : '<div class="admin-governance-empty">No governance rules yet — add one above to start enforcing house style.</div>';
}

function adminResetGovernanceForm() {
    document.getElementById('gov-kind').value = 'required_corridor';
    document.getElementById('gov-view').value = 'any';
    adminGovernanceViewChanged();
    document.getElementById('gov-platform').value = 'any';
    document.getElementById('gov-corridor-id').value = '';
    document.getElementById('gov-shape-type').value = '';
    document.getElementById('gov-overview-field').value = 'business_notes';
    document.getElementById('gov-min-count').value = '2';
    document.getElementById('gov-min-protection').value = 'tls';
    document.getElementById('gov-boundary-only').checked = true;
    document.getElementById('gov-label-pattern').value = '';
    document.getElementById('gov-message').value = '';
    adminGovernanceMessageInput();
    adminGovernanceKindChanged();
}

function adminEditGovernanceRule(id) {
    const r = _adminGovernanceRulesCache.find(x => x.id === id);
    if (!r) return;
    _adminGovernanceEditId = id;
    document.getElementById('gov-kind').value = r.kind;
    document.getElementById('gov-view').value = r.view;
    adminGovernanceViewChanged();
    document.getElementById('gov-platform').value = r.platform;
    document.getElementById('gov-corridor-id').value = r.corridor_id || '';
    document.getElementById('gov-shape-type').value = r.shape_type || '';
    // Set directly (not filtered) so editing a pre-existing rule always shows
    // its real stored value, even for a view/field combination created
    // before this filtering existed — never silently substitute a different
    // field for what the rule actually has configured.
    document.getElementById('gov-overview-field').value = r.overview_field || 'business_notes';
    document.getElementById('gov-min-count').value = r.min_count || 2;
    document.getElementById('gov-min-protection').value = r.min_protection || 'tls';
    document.getElementById('gov-boundary-only').checked = r.boundary_only !== false;
    document.getElementById('gov-label-pattern').value = r.label_pattern || '';
    document.getElementById('gov-message').value = r.message || '';
    adminGovernanceMessageInput();
    adminGovernanceKindChanged();
    _adminSetGovernanceSubmitButton(true);
    adminLoadGovernanceRules(); // re-render so the card being edited gets the .editing highlight
    document.getElementById('admin-section-governance').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function adminCancelGovernanceEdit() {
    _adminGovernanceEditId = null;
    adminResetGovernanceForm();
    _adminSetGovernanceSubmitButton(false);
    adminLoadGovernanceRules();
}

async function adminSubmitGovernanceRule() {
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
    const editing = _adminGovernanceEditId;
    const res = await adminFetch(
        editing ? `/admin/governance-rules/${editing}` : '/admin/governance-rules',
        {
            method: editing ? 'PATCH' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        },
    );
    if (res.ok) {
        showToast(editing ? 'Rule updated.' : 'Rule added.', 'success');
        _adminGovernanceEditId = null;
        adminResetGovernanceForm();
        _adminSetGovernanceSubmitButton(false);
        adminLoadGovernanceRules();
    } else {
        const data = await res.json().catch(() => ({}));
        showToast(_adminErrorMessage(data, editing ? 'Could not update rule.' : 'Could not add rule.'), 'error');
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
        if (_adminGovernanceEditId === id) adminCancelGovernanceEdit();
        else adminLoadGovernanceRules();
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
