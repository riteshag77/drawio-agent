function formatLogTime(isoOrDate) {
    const d = isoOrDate ? new Date(isoOrDate) : new Date();
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDetails(details) {
    if (!details) return '';
    if (typeof details === 'string') return details;
    return JSON.stringify(details, null, 2);
}

function autoResizeChatInput() {
    const el = document.getElementById('chat-input');
    el.style.height = 'auto';
    el.style.height = Math.min(Math.max(el.scrollHeight, 64), 180) + 'px';
}

function renderExampleChips() {
    const container = document.getElementById('example-chips');
    container.innerHTML = '';
    (EXAMPLE_PROMPTS[selectedPlatform] || EXAMPLE_PROMPTS.aws).forEach(text => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'example-chip';
        btn.textContent = text;
        btn.onclick = () => {
            document.getElementById('chat-input').value = text;
            autoResizeChatInput();
            sendMessage();
        };
        container.appendChild(btn);
    });
}

function renderPatternGallery() {
    const grid = document.getElementById('gallery-grid');
    if (!grid) return;
    grid.innerHTML = '';
    GALLERY_PATTERNS.forEach(p => {
        const card = document.createElement('div');
        card.className = 'gallery-card';
        card.dataset.prompt = p.prompt;
        card.title = `Load: ${p.title}`;
        card.innerHTML = `
            <div class="gallery-card-icon">${GALLERY_ICONS[p.id] || ''}</div>
            <div class="gallery-card-title">${p.title}</div>
            <div class="gallery-card-desc">${p.desc}</div>
            <div class="gallery-card-badges">${p.platforms.map(pl => `<span class="gallery-badge">${pl.toUpperCase()}</span>`).join('')}</div>
        `;
        card.addEventListener('click', () => {
            const input = document.getElementById('chat-input');
            input.value = p.prompt;
            autoResizeChatInput();
            sendMessage();
        });
        grid.appendChild(card);
    });
}

function hideWelcome() {
    const welcome = document.getElementById('chat-welcome');
    if (welcome) welcome.remove();
}

function setTyping(active) {
    document.getElementById('chat-typing').classList.toggle('active', active);
    if (active) {
        const container = document.getElementById('chat-messages');
        container.scrollTop = container.scrollHeight;
    }
}

function normalizeSuggestion(text) {
    return (text || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function registerAppliedSuggestions(texts) {
    (texts || []).forEach(text => {
        const normalized = normalizeSuggestion(text);
        const exists = appliedSuggestionTexts.some(item => normalizeSuggestion(item) === normalized);
        if (!exists) appliedSuggestionTexts.push(text);
    });
}

function isSuggestionApplied(text) {
    const normalized = normalizeSuggestion(text);
    return appliedSuggestionTexts.some(item => normalizeSuggestion(item) === normalized);
}

function parseImplementedSuggestions(text) {
    const trimmed = (text || '').trim();
    if (!/^Implement these suggestions:/i.test(trimmed)) return [];
    return trimmed.split('\n')
        .slice(1)
        .map(line => line.replace(/^\d+\.\s*/, '').trim())
        .filter(Boolean);
}

function getAppliedSuggestionsPayload() {
    return appliedSuggestionTexts.slice();
}

function updateSuggestionWrapActions(suggWrap) {
    if (!suggWrap) suggWrap = document.getElementById('suggestions-wrap');
    if (!suggWrap) return;
    const items = suggWrap.querySelectorAll('.item-checkbox');
    const pending = suggWrap.querySelectorAll('.item-checkbox:not(:disabled)');
    const actionRow = document.getElementById('suggestions-action-row')
        || suggWrap.querySelector('.suggestion-actions');
    if (actionRow) actionRow.hidden = pending.length === 0;
    const selectAll = document.getElementById('suggestions-select-all')
        || suggWrap.querySelector('.suggestion-select-all');
    const selectAllRow = document.getElementById('suggestions-select-all-row');
    if (selectAllRow) selectAllRow.hidden = items.length === 0;
    if (selectAll) {
        selectAll.disabled = pending.length === 0;
        if (pending.length === 0) {
            selectAll.checked = false;
            selectAll.indeterminate = false;
        }
    }
}

function markSuggestionWrapApplied(suggWrap, texts) {
    if (!suggWrap) suggWrap = document.getElementById('suggestions-wrap');
    if (!suggWrap || !texts?.length) return;
    const appliedSet = new Set(texts.map(normalizeSuggestion));
    suggWrap.querySelectorAll('.chat-suggestion-item').forEach(label => {
        const text = label.dataset.suggestionText || '';
        if (!appliedSet.has(normalizeSuggestion(text))) return;
        label.classList.add('is-applied');
        const cb = label.querySelector('.item-checkbox');
        if (cb) { cb.checked = false; cb.disabled = true; }
        const badge = label.querySelector('.suggestion-applied-badge');
        if (badge) badge.hidden = false;
    });
    updateSuggestionWrapActions(suggWrap);
}

function markAllMatchingSuggestionsApplied(texts) {
    if (!texts?.length) return;
    markSuggestionWrapApplied(document.getElementById('suggestions-wrap'), texts);
}

function setSuggestionPanelsBusy(busy) {
    const wrap = document.getElementById('suggestions-wrap');
    if (!wrap) return;
    wrap.querySelectorAll('.suggestion-actions button, .suggestion-checkbox').forEach(el => {
        if (el.closest('.chat-suggestion-item.is-applied')) return;
        el.disabled = busy;
    });
}

function appendChatBubble(role, content) {
    hideWelcome();
    const container = document.getElementById('chat-messages');
    const msg = document.createElement('div');
    msg.className = `chat-message ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = content;
    msg.appendChild(bubble);

    const meta = document.createElement('div');
    meta.className = 'chat-meta';
    meta.textContent = role === 'user' ? 'You' : 'Assistant · ' + formatLogTime();
    msg.appendChild(meta);

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

function implementSuggestions(suggestions, suggWrap) {
    if (!suggestions || suggestions.length === 0) return;
    registerAppliedSuggestions(suggestions);
    markSuggestionWrapApplied(suggWrap || document.getElementById('suggestions-wrap'), suggestions);
    const msg = 'Implement these suggestions:\n' + suggestions.map((s, i) => `${i + 1}. ${s}`).join('\n');
    document.getElementById('chat-input').value = msg;
    autoResizeChatInput();
    sendMessage();
}

function clearChat() {
    if (isSending) return;
    if (typeof confirmAction === 'function' && !confirmAction('Clear chat and discard the current diagram session?')) {
        return;
    }
    chatMessages = [];
    appliedSuggestionTexts = [];
    if (typeof clearSuggestionsPanel === 'function') clearSuggestionsPanel({ keepPlaceholder: true });
    currentArchitecture = null;
    previousArchitecture = null;
    currentXml = '';
    previousXml = null;
    clearAttachments();
    if (typeof diagramVersions !== 'undefined') {
        diagramVersions = [];
        activeDiagramVersion = -1;
        if (typeof renderVersionStrip === 'function') renderVersionStrip();
    }
    if (typeof hideValidationBanner === 'function') hideValidationBanner();
    setActionButtonsEnabled(false);
    document.getElementById('xml-output').textContent = '';
    const xmlPanel = document.getElementById('xml-panel');
    const xmlBtn = document.getElementById('btn-xml');
    if (xmlPanel) xmlPanel.classList.remove('open');
    if (xmlBtn) {
        xmlBtn.setAttribute('aria-pressed', 'false');
        xmlBtn.title = 'View Draw.io XML source';
        xmlBtn.setAttribute('aria-label', xmlBtn.title);
    }
    setDiagramStatus('Waiting for generation');
    document.getElementById('diagram-placeholder').classList.remove('hidden');

    const container = document.getElementById('chat-messages');
    container.innerHTML = `
        <div id="chat-welcome" class="chat-welcome">
            <div class="patterns-gallery" id="patterns-gallery">
                <div class="gallery-header">
                    <h3>Architecture Patterns</h3>
                    <p>Choose a foundation for your diagram</p>
                </div>
                <div class="gallery-grid" id="gallery-grid"></div>
            </div>
            <div class="example-chips" id="example-chips"></div>
        </div>
        <div id="chat-typing" class="chat-typing">
            <span></span><span></span><span></span>
        </div>`;
    renderExampleChips();
    renderPatternGallery();
    appendVerboseLog('ui', 'Chat cleared', 'info');
}
