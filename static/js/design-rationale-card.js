/** Chat-feed card surfacing the model's own design tradeoffs — "I chose X
 * over Y because Z" bullets emitted in the same structured-output call that
 * produced the IR (see NetworkIR.design_tradeoffs / CorridorIR.design_tradeoffs),
 * not a separate LLM round-trip. Modeled on validation-card.js: the chat feed
 * has no built-in support for rich cards (appendChatBubble only sets
 * textContent), so this is a small standalone renderer reusing its
 * container/scroll conventions. Collapsed by default past the first few
 * bullets so it doesn't compete for weight with the diagram itself. */

function appendDesignRationaleCard(tradeoffs) {
    if (!Array.isArray(tradeoffs) || !tradeoffs.length) return;
    if (typeof hideWelcome === 'function') hideWelcome();
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const msg = document.createElement('div');
    msg.className = 'chat-message assistant design-rationale-msg';
    msg.innerHTML = designRationaleCardHtml(tradeoffs);
    container.appendChild(msg);
    if (typeof _moveTypingIndicatorToEnd === 'function') _moveTypingIndicatorToEnd();
    container.scrollTop = container.scrollHeight;
}

function designRationaleCardHtml(tradeoffs) {
    const esc = typeof escapeHtml === 'function' ? escapeHtml : (s) => s;
    const items = tradeoffs.map((t) => `<li>${esc(t)}</li>`).join('');
    return `
        <div class="design-rationale-card">
            <button type="button" class="design-rationale-header" onclick="toggleDesignRationaleCard(this)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="design-rationale-icon" aria-hidden="true">
                    <path d="M9.663 17h4.674M12 3a6 6 0 0 0-4 10.472V15a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-1.528A6 6 0 0 0 12 3Z"/>
                </svg>
                <span class="design-rationale-title">Why this design</span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="design-rationale-chevron" aria-hidden="true">
                    <path d="M6 9l6 6 6-6"/>
                </svg>
            </button>
            <ul class="design-rationale-list">${items}</ul>
        </div>`;
}

function toggleDesignRationaleCard(headerBtn) {
    const card = headerBtn.closest('.design-rationale-card');
    if (card) card.classList.toggle('collapsed');
}
