function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

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
    el.style.height = Math.min(el.scrollHeight, 180) + 'px';
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
    const pending = suggWrap.querySelectorAll('.item-checkbox:not(:disabled)');
    const actionRow = suggWrap.querySelector('.suggestion-actions');
    if (actionRow) actionRow.hidden = pending.length === 0;
    const selectAll = suggWrap.querySelector('.suggestion-select-all');
    if (selectAll) {
        selectAll.disabled = pending.length === 0;
        if (pending.length === 0) selectAll.checked = false;
    }
}

function markSuggestionWrapApplied(suggWrap, texts) {
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
    document.querySelectorAll('.chat-suggestions').forEach(wrap => markSuggestionWrapApplied(wrap, texts));
}

function setSuggestionPanelsBusy(busy) {
    document.querySelectorAll('.chat-suggestions').forEach(wrap => {
        wrap.querySelectorAll('.suggestion-actions button, .suggestion-checkbox').forEach(el => {
            if (el.closest('.chat-suggestion-item.is-applied')) return;
            el.disabled = busy;
        });
    });
}

function appendChatBubble(role, content, suggestions = null) {
    hideWelcome();
    const container = document.getElementById('chat-messages');
    const msg = document.createElement('div');
    msg.className = `chat-message ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.textContent = content;
    msg.appendChild(bubble);

    if (role === 'assistant' && suggestions && suggestions.length) {
        const suggWrap = document.createElement('div');
        suggWrap.className = 'chat-suggestions';
        suggWrap._suggestions = suggestions.slice();

        const header = document.createElement('div');
        header.className = 'chat-suggestions-header';
        const title = document.createElement('div');
        title.className = 'chat-suggestions-title';
        title.textContent = 'Suggestions';
        header.appendChild(title);

        const selectAllLabel = document.createElement('label');
        selectAllLabel.className = 'chat-suggestions-select-all';
        const selectAllCb = document.createElement('input');
        selectAllCb.type = 'checkbox';
        selectAllCb.className = 'suggestion-checkbox suggestion-select-all';
        selectAllCb.title = 'Select all pending suggestions';
        const selectAllText = document.createElement('span');
        selectAllText.textContent = 'Select all';
        selectAllLabel.appendChild(selectAllCb);
        selectAllLabel.appendChild(selectAllText);
        header.appendChild(selectAllLabel);
        suggWrap.appendChild(header);

        const suggList = document.createElement('div');
        suggList.className = 'chat-suggestions-list';
        suggestions.forEach((s, idx) => {
            const alreadyApplied = isSuggestionApplied(s);
            const label = document.createElement('label');
            label.className = 'chat-suggestion-item' + (alreadyApplied ? ' is-applied' : '');
            label.dataset.suggestionText = s;

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'suggestion-checkbox item-checkbox';
            cb.dataset.index = String(idx);
            cb.disabled = alreadyApplied;

            const span = document.createElement('span');
            span.textContent = s;

            const badge = document.createElement('span');
            badge.className = 'suggestion-applied-badge';
            badge.textContent = 'Applied';
            badge.hidden = !alreadyApplied;

            cb.addEventListener('change', () => {
                const pending = suggList.querySelectorAll('.item-checkbox:not(:disabled)');
                const checked = suggList.querySelectorAll('.item-checkbox:not(:disabled):checked');
                selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
                selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
            });

            label.appendChild(cb);
            label.appendChild(span);
            label.appendChild(badge);
            suggList.appendChild(label);
        });
        suggWrap.appendChild(suggList);

        selectAllCb.addEventListener('change', () => {
            suggList.querySelectorAll('.item-checkbox:not(:disabled)').forEach(cb => {
                cb.checked = selectAllCb.checked;
            });
            selectAllCb.indeterminate = false;
        });

        const actionRow = document.createElement('div');
        actionRow.className = 'suggestion-actions';
        const selBtn = document.createElement('button');
        selBtn.type = 'button';
        selBtn.className = 'suggestion-btn';
        selBtn.textContent = 'Apply Selected';
        selBtn.onclick = () => {
            const checked = suggWrap.querySelectorAll('.item-checkbox:checked:not(:disabled)');
            if (checked.length === 0) { showError('Select at least one suggestion to apply.'); return; }
            const texts = Array.from(checked).map(cb => suggWrap._suggestions[Number(cb.dataset.index)]);
            implementSuggestions(texts, suggWrap);
        };
        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.className = 'suggestion-btn suggestion-btn-primary';
        allBtn.textContent = 'Apply All';
        allBtn.onclick = () => {
            const pending = suggWrap._suggestions.filter(item => !isSuggestionApplied(item));
            if (pending.length === 0) { showError('All suggestions are already applied.'); return; }
            implementSuggestions(pending, suggWrap);
        };
        actionRow.appendChild(selBtn);
        actionRow.appendChild(allBtn);
        suggWrap.appendChild(actionRow);
        updateSuggestionWrapActions(suggWrap);
        msg.appendChild(suggWrap);
    }

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
    markSuggestionWrapApplied(suggWrap, suggestions);
    const msg = 'Implement these suggestions:\n' + suggestions.map((s, i) => `${i + 1}. ${s}`).join('\n');
    document.getElementById('chat-input').value = msg;
    autoResizeChatInput();
    sendMessage();
}

function clearChat() {
    if (isSending) return;
    chatMessages = [];
    appliedSuggestionTexts = [];
    currentArchitecture = null;
    previousArchitecture = null;
    currentXml = '';
    previousXml = null;
    attachmentContext = '';
    attachmentImageData = null;
    hasImageAttachment = false;
    const preview = document.getElementById('attachment-preview');
    if (preview) { preview.hidden = true; preview.textContent = ''; }
    hideValidationBanner();
    setActionButtonsEnabled(false);
    document.getElementById('xml-output').textContent = '';
    document.getElementById('xml-toggle').classList.remove('active');
    document.getElementById('xml-panel').classList.remove('open');
    hideError();
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
