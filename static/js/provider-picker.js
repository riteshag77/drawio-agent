/** Custom BYOK-aware provider/model picker — a popover replacing the plain
 * <select> visually, grouped into "Server Models" (built-in providers, incl.
 * multi-model optgroups) and "Your Providers" (custom/BYOK). The underlying
 * #provider-select
 * stays the real data source (kept in the DOM, hidden) so every existing
 * call site that reads/writes its .value or listens for its 'change' event
 * keeps working unmodified. */

/** Custom option values are `custom:<providerId>::<modelId>` — one row per
 * model, matching how multi-model server providers already work. */
function _parseCustomOptionValue(value) {
    const rest = value.slice('custom:'.length);
    const sep = rest.indexOf('::');
    return sep === -1 ? { id: rest, model: '' } : { id: rest.slice(0, sep), model: rest.slice(sep + 2) };
}

function providerPickerBadgeHtml(optionEl) {
    const value = optionEl.value;
    if (value.startsWith('custom:')) {
        const { id } = _parseCustomOptionValue(value);
        const p = (typeof getCustomProviderById === 'function') ? getCustomProviderById(id) : null;
        const client = p ? p.client : 'openai';
        const icon = (typeof CUSTOM_PROVIDER_ICONS !== 'undefined' && CUSTOM_PROVIDER_ICONS[client]) || '';
        return `<span class="provider-picker-badge provider-icon-${client}" style="background:var(--surface-hover);color:inherit">${icon}</span>`;
    }
    const letter = (optionEl.textContent || '?').trim().charAt(0).toUpperCase();
    return `<span class="provider-picker-badge" style="background:var(--accent)">${letter}</span>`;
}

function _providerPickerOptions() {
    const select = document.getElementById('provider-select');
    if (!select) return [];
    const groups = [];
    let current = { label: '', items: [] };
    Array.from(select.children).forEach((child) => {
        if (child.tagName === 'OPTGROUP') {
            groups.push({ label: child.label, items: Array.from(child.children) });
        } else if (child.tagName === 'OPTION') {
            current.items.push(child);
        }
    });
    if (current.items.length) groups.unshift(current);
    return groups;
}

function renderProviderPickerButton() {
    const select = document.getElementById('provider-select');
    const iconSlot = document.getElementById('provider-picker-icon');
    const labelSlot = document.getElementById('provider-picker-label');
    if (!select || !iconSlot || !labelSlot) return;
    const opt = select.options[select.selectedIndex];
    if (!opt) return;
    iconSlot.innerHTML = providerPickerBadgeHtml(opt);
    labelSlot.textContent = opt.textContent;
    labelSlot.title = opt.textContent;
}

function renderProviderPickerList(filterText) {
    const list = document.getElementById('provider-picker-list');
    const select = document.getElementById('provider-select');
    if (!list || !select) return;
    const q = (filterText || '').trim().toLowerCase();
    const groups = _providerPickerOptions();
    let html = '';
    groups.forEach((group) => {
        const isCustomGroup = group.label === 'Custom';
        const items = group.items.filter((opt) => !q || opt.textContent.toLowerCase().includes(q) || opt.value.toLowerCase().includes(q));
        if (!items.length) return;
        html += `<div class="provider-picker-group-label">${isCustomGroup ? 'Your Providers' : (group.label || 'Server Models')}</div>`;
        items.forEach((opt) => {
            const selected = opt.value === select.value;
            const isDefaultOpt = opt.hasAttribute('data-default');
            let untested = false;
            if (opt.value.startsWith('custom:') && typeof getCustomProviderById === 'function') {
                const p = getCustomProviderById(_parseCustomOptionValue(opt.value).id);
                untested = !p || p.lastTestStatus !== 'pass';
            }
            html += `<button type="button" class="provider-picker-item${selected ? ' selected' : ''}" role="option" aria-selected="${selected}" onclick="selectProviderPickerOption('${opt.value.replace(/'/g, "\\'")}')">
                <span class="provider-picker-item-check">${selected ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>' : ''}</span>
                ${providerPickerBadgeHtml(opt)}
                <span class="provider-picker-item-label">${opt.textContent}</span>
                ${isDefaultOpt ? '<span class="provider-picker-item-default">Default</span>' : ''}
                ${untested ? '<span class="provider-picker-item-untested" title="Not tested yet — open Settings → Custom providers to test">untested</span>' : ''}
            </button>`;
        });
    });
    list.innerHTML = html || '<div class="provider-picker-empty">No providers match.</div>';
}

/** Centered command-palette modal ("Search models…" dialog) — not anchored
 * to the trigger button, so no positioning-from-
 * button-rect logic is needed; CSS centers it directly. */
function toggleProviderPicker() {
    const popover = document.getElementById('provider-picker-popover');
    const backdrop = document.getElementById('provider-picker-backdrop');
    const btn = document.getElementById('provider-picker-btn');
    if (!popover || !btn) return;
    const open = popover.hidden;
    popover.hidden = !open;
    if (backdrop) backdrop.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
        renderProviderPickerList('');
        const search = document.getElementById('provider-picker-search');
        if (search) { search.value = ''; search.focus(); }
    }
}

function closeProviderPicker() {
    const popover = document.getElementById('provider-picker-popover');
    const backdrop = document.getElementById('provider-picker-backdrop');
    const btn = document.getElementById('provider-picker-btn');
    if (popover) popover.hidden = true;
    if (backdrop) backdrop.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
}

function filterProviderPicker() {
    const search = document.getElementById('provider-picker-search');
    renderProviderPickerList(search ? search.value : '');
}

function selectProviderPickerOption(value) {
    const select = document.getElementById('provider-select');
    if (!select) return;
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    renderProviderPickerButton();
    closeProviderPicker();
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeProviderPicker();
});
