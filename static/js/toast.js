/** Always-visible toast notifications (not gated on pipeline logs).
 *
 * Queued, not overwritten. There is one toast element, and a turn regularly
 * has two things to say — a degraded-page warning landed mid-result was
 * replaced by the run's own "Done" a moment later, every time, so the warning
 * was shown to nobody. Replacing meant the *least* important message won,
 * because it happened to be last.
 *
 * A queue keeps every message readable in turn. Urgent types jump the line:
 * a warning or an error is news, "Done" is not.
 */

const _TOAST_JUMPS_QUEUE = new Set(['warning', 'error']);

function showToast(message, type = 'info', durationMs = 4200) {
    const el = document.getElementById('app-toast');
    if (!el || !message) return;
    showToast._queue = showToast._queue || [];

    // Identical back-to-back messages are noise, not emphasis.
    const last = showToast._queue[showToast._queue.length - 1];
    if ((showToast._showing && showToast._showing.message === message)
        || (last && last.message === message)) return;

    const item = { message, type, durationMs };
    if (_TOAST_JUMPS_QUEUE.has(type)) {
        const firstRoutine = showToast._queue.findIndex((q) => !_TOAST_JUMPS_QUEUE.has(q.type));
        if (firstRoutine === -1) showToast._queue.push(item);
        else showToast._queue.splice(firstRoutine, 0, item);
    } else {
        showToast._queue.push(item);
    }
    _drainToasts();
}

function _drainToasts() {
    const el = document.getElementById('app-toast');
    if (!el || showToast._showing) return;
    const next = (showToast._queue || []).shift();
    if (!next) return;
    showToast._showing = next;
    el.hidden = false;
    el.className = `app-toast app-toast-${next.type}`;
    el.textContent = next.message;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
        showToast._showing = null;
        if ((showToast._queue || []).length) {
            _drainToasts();
        } else {
            el.hidden = true;
        }
    }, next.durationMs);
}

/** Drop anything queued — used when a view resets. */
function clearToasts() {
    showToast._queue = [];
    showToast._showing = null;
    clearTimeout(showToast._timer);
    const el = document.getElementById('app-toast');
    if (el) el.hidden = true;
}

function confirmAction(message) {
    return window.confirm(message);
}

function hideValidationBanner() {
    validationPanelActive = false;
    window._activeValidationCardId = null;
}
