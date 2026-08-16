/**
 * Populating the provider/model <select>s from /providers.
 *
 * The option-building itself lives in provider-options.js, which admin.html
 * shares; this file is only the fetching and the two call sites.
 */

// ── Provider select ───────────────────────────────────────

// populateProviderOptions() now lives in provider-options.js — admin.html needs
// the same builder and does not load this file.

async function initProviderSelect() {
    const select = document.getElementById('provider-select');
    if (!select) return;
    try {
        const res = await fetch('/providers');
        // res.json() does not throw on a 401 — the API-key middleware's error
        // body ({"status":"error","detail":"Unauthorized"}) is itself valid
        // JSON — so an unauthenticated call silently reached
        // populateProviderOptions with no `providers` field, which wiped the
        // dropdown to empty with no error anywhere. Check status explicitly.
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        populateProviderOptions(select, data);
        if (typeof renderProviderPickerButton === 'function') renderProviderPickerButton();
    } catch (err) {
        appendVerboseLog('client', 'Failed to load providers', 'error', { error: String(err) });
        if (typeof showToast === 'function') {
            showToast(`Couldn't load providers: ${err.message || err}`, 'warning', 6500);
        }
    }
}

/** Same catalog as initProviderSelect(), plus a leading "Same as chat" auto
 * option — so Visual QA's provider picker is never limited to a hardcoded
 * 3-provider list while the composer's picker sees every registered/BYOK
 * provider and model. */
async function initValidationProviderSelect() {
    const select = document.getElementById('validation-provider-select');
    if (!select) return;
    try {
        const res = await fetch('/providers');
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        populateProviderOptions(select, data, { includeAuto: true, autoLabel: 'Same as chat' });
        // initPromptSettings() already ran and tried to restore the saved
        // choice before this select had real options (silent no-op against
        // just the placeholder) — reapply it now that the option exists.
        const saved = localStorage.getItem(VALIDATION_PROVIDER_KEY);
        if (saved && select.querySelector(`option[value="${CSS.escape(saved)}"]`)) {
            select.value = saved;
        }
    } catch (err) {
        appendVerboseLog('client', 'Failed to load validation providers', 'error', { error: String(err) });
    }
}
