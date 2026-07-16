/** Visual validation UI stubs — server /validate-diagram was removed with the XML pipeline. */

function hideValidationBanner() {
    validationPanelActive = false;
    const panel = document.getElementById('validation-panel');
    if (panel) panel.classList.remove('active', 'critical', 'passed');
    const body = document.getElementById('validation-panel-body');
    if (body) body.innerHTML = '';
    const fixBtn = document.getElementById('btn-fix-layout');
    if (fixBtn) fixBtn.style.display = 'none';
    lastValidationFeedback = null;
    if (typeof updateRightPanelVisibility === 'function') updateRightPanelVisibility();
}

function showValidationBanner() {
    /* no-op: VLM validation endpoint removed */
}

function showValidationPassed() {
    /* no-op */
}

async function runVisualValidation() {
    appendVerboseLog('validation', 'VLM validation skipped — /validate-diagram is not available', 'info');
}

async function fixLayoutIssues() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    input.value = 'Fix the layout issues — improve spacing, edge routing, and remove overlaps.';
    await sendMessage({});
}
