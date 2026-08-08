function openSettings() {
    document.getElementById('settings-backdrop').classList.add('open');
    document.getElementById('settings-drawer').classList.add('open');
    if (typeof loadCapturesList === 'function') loadCapturesList();
    if (typeof loadTemplates === 'function') loadTemplates();
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

