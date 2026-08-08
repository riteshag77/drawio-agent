/** Always-visible toast notifications (not gated on pipeline logs). */

function showToast(message, type = 'info', durationMs = 4200) {
    const el = document.getElementById('app-toast');
    if (!el || !message) return;
    el.hidden = false;
    el.className = `app-toast app-toast-${type}`;
    el.textContent = message;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
        el.hidden = true;
    }, durationMs);
}

function confirmAction(message) {
    return window.confirm(message);
}

function hideValidationBanner() {
    validationPanelActive = false;
    window._activeValidationCardId = null;
}
