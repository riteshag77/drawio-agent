/** Compact chat-feed status line for the visual (VLM) validation pass —
 * capturing → validating → retrying → success/warning/failed. Collapsed to a
 * single status line by default once finished (matches this app's WAF
 * pass/warn/fail icon set, not emoji) so it doesn't compete for weight with
 * the chat bubbles around it; expands on click to show the screenshot-derived
 * issue list. No existing chat bubble supports rich HTML (appendChatBubble
 * only sets textContent), so this is a small standalone renderer that reuses
 * the chat feed's own container/scroll conventions. */

const VALIDATION_ICONS = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    failed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
};

const VALIDATION_CARD_META = {
    capturing: { label: 'Capturing diagram…' },
    validating: { label: 'Checking for visual issues…' },
    retrying: { label: 'Issues found — asking the AI to fix them…' },
    success: { label: 'Visual QA passed' },
    warning: { label: 'Visual QA passed with warnings' },
    failed: { label: 'Visual QA found issues' },
};

function appendValidationCard(state) {
    if (typeof hideWelcome === 'function') hideWelcome();
    const container = document.getElementById('chat-messages');
    if (!container) return null;
    const msg = document.createElement('div');
    msg.className = 'chat-message assistant validation-card-msg';
    msg.id = 'validation-card-' + Date.now();
    msg.innerHTML = validationCardHtml(state, null, false);
    container.appendChild(msg);
    if (typeof _moveTypingIndicatorToEnd === 'function') _moveTypingIndicatorToEnd();
    container.scrollTop = container.scrollHeight;
    window._activeValidationCardId = msg.id;
    validationPanelActive = true;
    return msg;
}

function updateValidationCard(state, data) {
    const el = window._activeValidationCardId ? document.getElementById(window._activeValidationCardId) : null;
    if (!el) return;
    const isBusy = state === 'capturing' || state === 'validating' || state === 'retrying';
    el.innerHTML = validationCardHtml(state, data, !isBusy);
    const container = document.getElementById('chat-messages');
    if (container) container.scrollTop = container.scrollHeight;
}

function toggleValidationCardBody(headerBtn) {
    const card = headerBtn.closest('.validation-card');
    if (card) card.classList.toggle('collapsed');
}

function validationIssueRow(issue) {
    const sevClass = issue.severity === 'critical' ? 'critical' : 'warning';
    const hint = issue.element_hint ? ` — <em>${issue.element_hint}</em>` : '';
    return `<li class="validation-issue validation-issue-${sevClass}">
        <span class="validation-issue-badge">${issue.severity}</span>
        <span class="validation-issue-cat">${issue.category.replace('_', ' ')}</span>
        <span class="validation-issue-desc">${issue.description}${hint}</span>
    </li>`;
}

function validationCardHtml(state, data, collapsedByDefault) {
    const meta = VALIDATION_CARD_META[state] || VALIDATION_CARD_META.validating;
    const issues = (data && data.issues) || [];
    const criticalCount = issues.filter((i) => i.severity === 'critical').length;
    let label = meta.label;
    if (state === 'warning') label = `Visual QA — ${issues.length} warning${issues.length === 1 ? '' : 's'}`;
    if (state === 'failed') label = `Visual QA — ${issues.length} issue${issues.length === 1 ? '' : 's'} found`;

    const isBusy = state === 'capturing' || state === 'validating' || state === 'retrying';
    const iconHtml = isBusy ? '<span class="validation-spinner"></span>' : (VALIDATION_ICONS[state] || VALIDATION_ICONS.warning);

    const bodyParts = [];
    if (data && data.error) bodyParts.push(`<p class="validation-card-error">${data.error}</p>`);
    if (data && data.summary) bodyParts.push(`<p class="validation-card-summary">${data.summary}</p>`);
    if (issues.length) bodyParts.push(`<ul class="validation-issue-list">${issues.map(validationIssueRow).join('')}</ul>`);
    if (!isBusy && criticalCount > 0) {
        bodyParts.push(`<button type="button" class="validation-fix-btn" onclick="retryValidationFix('${window._activeValidationCardId}')">Improve with Suggestions</button>`);
    }
    const hasBody = bodyParts.length > 0;

    return `
        <div class="validation-card validation-card-${state}${isBusy ? ' loading' : ''}${collapsedByDefault && hasBody ? ' collapsed' : ''}${!hasBody ? ' no-body' : ''}">
            <button type="button" class="validation-card-header validation-card-${state}" onclick="toggleValidationCardBody(this)"${hasBody ? '' : ' disabled'}>
                <span class="validation-card-icon">${iconHtml}</span>
                <span class="validation-card-label">${label}</span>
                ${hasBody ? '<span class="validation-card-chevron" aria-hidden="true"></span>' : ''}
            </button>
            ${hasBody ? `<div class="validation-card-body">${bodyParts.join('')}</div>` : ''}
        </div>`;
}
