/**
 * app.js — thin orchestrator.
 *
 * Responsibilities kept here:
 *   - sendMessage  (main chat dispatch)
 *   - postChatStream  (SSE fetch + frame parse)
 *   - initProviderSelect
 *   - downloadXML / copyXML / openInDrawio / toggleXML  (dedupe guard vs exports.js)
 *   - DOMContentLoaded boot sequence
 *
 * Everything else lives in focused sibling modules loaded before this file:
 *   state → resizer → settings → prompt-settings → diagram → voice → chat → toast →
 *   exports → attachments → platform-ui → run-controls → verbose-logs →
 *   insights-ui → insights-panels → app.js
 */

// ── Chat-API helpers ──────────────────────────────────────

/** FastAPI/Pydantic validation errors return `detail` as an array of
 *  {loc, msg, type} objects, not a plain string — stringifying that
 *  directly produces an unreadable "[object Object]" toast/log message.
 *  Normalize both shapes into one readable string. */
function formatErrorDetail(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map((d) => {
            if (typeof d === 'string') return d;
            const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
            return field ? `${field}: ${d.msg}` : (d.msg || JSON.stringify(d));
        }).join('; ');
    }
    return typeof detail === 'object' ? (detail.msg || JSON.stringify(detail)) : String(detail);
}

/** The chat bubble is for a quick confirmation, not the full explanation —
 *  that detail belongs in the diagram's own Overview Panel. Collapse the
 *  LLM's assistant_message (which can run to several sentences/paragraphs)
 *  down to its first sentence, capped to a short length. */
function summarizeAssistantMessage(text) {
    if (!text) return 'Diagram updated.';
    const firstLine = text.split('\n').find((l) => l.trim()) || text;
    const trimmed = firstLine.trim();
    const sentenceMatch = trimmed.match(/^.{1,140}?[.!?](?=\s|$)/);
    if (sentenceMatch) return sentenceMatch[0].trim();
    return trimmed.length > 140 ? trimmed.slice(0, 137).trimEnd() + '…' : trimmed;
}

async function postChatStream(chatBody, signal) {
    const res = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        signal,
        body: JSON.stringify(chatBody),
    });
    if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
            const errBody = await res.json();
            detail = formatErrorDetail(errBody.detail) || detail;
        } catch (_) {}
        throw new Error(detail);
    }
    if (!res.body || !res.body.getReader) {
        throw new Error('Streaming body unavailable');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult = null;
    let progress = 20;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';
        for (const chunk of chunks) {
            const line = chunk.split('\n').find((l) => l.startsWith('data: '));
            if (!line) continue;
            let evt;
            try {
                evt = JSON.parse(line.slice(6));
            } catch (_) {
                continue;
            }
            if (evt.logs) mergeServerLogs(evt.logs);
            if (evt.type === 'progress') {
                progress = Math.min(85, progress + 8);
                setProgress(progress);
                if (evt.message) {
                    setDiagramStatus(evt.message);
                    setComposerRunStatus(evt.message, 'running');
                }
                appendVerboseLog(evt.step || 'progress', evt.message || 'Progress', 'info', evt.details || null);
            } else if (evt.type === 'result') {
                finalResult = evt;
            } else if (evt.type === 'error') {
                throw new Error(evt.detail || 'Stream error');
            }
        }
    }

    if (!finalResult) throw new Error('Stream ended without a result');
    if (finalResult.logs) mergeServerLogs(finalResult.logs);
    return finalResult;
}

// ── Diagram XML actions (guard against duplicate export stubs) ────────────

function downloadXML() {
    if (!currentXml) return;
    const blob = new Blob([currentXml], { type: 'application/xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `architecture_${selectedPlatform}.drawio`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function copyXML() {
    // Copy XML UI removed — keep stub for any leftover callers.
}

function openInDrawio() {
    if (!currentXml) return;
    const encoded = encodeURIComponent(currentXml);
    window.open(`https://app.diagrams.net/#R${encoded}`, '_blank');
}

function toggleXML() {
    // XML panel UI removed — keep stub for any leftover callers.
}

// ── Main chat dispatch ────────────────────────────────────

/** provider-select's value is either a bare provider key, "custom:<id>", or
 * "<providerKey>::<modelId>" for a multi-model provider (e.g. NVIDIA NIM). */
function parseProviderSelectValue(value) {
    if (!value) return { provider: '', model: null };
    if (value.startsWith('custom:')) return { provider: value, model: null };
    const sep = value.indexOf('::');
    if (sep === -1) return { provider: value, model: null };
    return { provider: value.slice(0, sep), model: value.slice(sep + 2) };
}

async function sendMessage(options = {}) {
    const input = document.getElementById('chat-input');
    const text = (options.skipInput ? options.message : input.value).trim();
    const { provider, model } = parseProviderSelectValue(document.getElementById('provider-select').value);
    const sendBtn = document.getElementById('send-btn');

    if (!text || isSending) {
        if (!text) appendVerboseLog('run', 'Please enter a message.', 'warn');
        return;
    }

    if (!options.isValidationRetry) {
        hideValidationBanner();
        vlmAutoRetryCount = 0;
    }
    if (isListening) {
        voiceManuallyStopping = true;
        try { speechRecognition.stop(); } catch (_) {}
    }
    isSending = true;
    sendBtn.disabled = true;
    input.disabled = true;
    updateSendStopButtons();
    setPlatformTabsBusy(true);
    setSuggestionPanelsBusy(true);

    const parsedApplied = parseImplementedSuggestions(text);
    if (parsedApplied.length) {
        registerAppliedSuggestions(parsedApplied);
        markAllMatchingSuggestionsApplied(parsedApplied);
    }

    if (!options.skipInput) {
        chatMessages.push({ role: 'user', content: text });
        appendChatBubble('user', text);
        input.value = '';
        autoResizeChatInput();
    } else if (text) {
        chatMessages.push({ role: 'user', content: text });
        if (options.showUserBubble !== false) {
            appendChatBubble('user', text);
        }
    }

    setProgress(10);
    setTyping(true);
    setComposerRunStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : 'Generating diagram…',
        'running'
    );
    setDiagramStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : 'Analyzing architecture...'
    );
    document.getElementById('diagram-loading').classList.add('active');

    chatAbortController = new AbortController();
    runStoppedByUser = false;

    if (verboseLogging) {
        appendVerboseLog('client', 'Sending chat message', 'info', {
            platform: selectedPlatform,
            provider,
            turn: chatMessages.length,
            has_existing_diagram: !!currentArchitecture,
        });
    }

    try {
        const promptSettings = getPromptSettings();
        let xmlForRequest = null;
        if (attachmentDiagramXml && attachmentDiagramXml.length > 200) {
            xmlForRequest = attachmentDiagramXml;
        } else if (!currentArchitecture) {
            const liveXml = await exportDiagramXml();
            xmlForRequest = (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);
        }
        previousXml = currentXml || null;

        let contextText = attachmentContext || '';
        let extraDocuments = [];
        const urlMatch = text.match(/https?:\/\/[^\s<>"']+/);
        const urlAlreadyImported = urlMatch && attachments.some((a) => a.kind === 'url' && a.url === urlMatch[0]);
        if (urlMatch && !urlAlreadyImported && !contextText.includes(urlMatch[0])) {
            try {
                const urlDataPromise = fetchUrlData(urlMatch[0]);
                const data = await Promise.race([
                    urlDataPromise,
                    new Promise((_, reject) => setTimeout(() => reject(new Error('URL parse timeout')), 2500)),
                ]);
                if (data.kind === 'image') {
                    // Image URL typed inline in the message — route through vision,
                    // not text context (decoding image bytes as UTF-8 would garble them).
                    extraDocuments.push({ filename: data.filename, mime: data.mime, data: data.data });
                } else {
                    const chunk = `--- URL: ${urlMatch[0]} (${data.title}) ---\n${data.content}`;
                    contextText = (contextText ? contextText + '\n' : '') + chunk;
                }
            } catch (urlErr) {
                appendVerboseLog('client', `URL parse skipped: ${urlErr.message}`, 'warn');
            }
        }

        appendVerboseLog('client', 'POST /chat/stream', 'info', {
            ...promptSettings,
            has_current_xml: !!xmlForRequest,
            xml_chars: (xmlForRequest || '').length,
        });
        const chatBody = {
            messages: chatMessages,
            session_id: ensureSessionId(),
            provider,
            model,
            custom_provider: (typeof getSelectedCustomProviderPayload === 'function')
                ? getSelectedCustomProviderPayload()
                : null,
            platform: selectedPlatform,
            diagram_mode: options.platformConvert ? 'cloud' : getDiagramMode(),
            platform_convert: !!options.platformConvert,
            previous_platform: options.previousPlatform || null,
            diagram_renderer: document.getElementById('diagram-renderer-select')?.value || 'auto',
            current_architecture: currentArchitecture,
            previous_architecture: previousArchitecture,
            current_xml: xmlForRequest,
            previous_xml: previousXml,
            custom_system_message: promptSettings.custom_system_message,
            cost_estimate: promptSettings.cost_estimate,
            well_architected: promptSettings.well_architected,
            compliance: promptSettings.compliance,
            compliance_frameworks: promptSettings.compliance_frameworks,
            context_text: contextText || null,
            attachment_documents: (attachmentDocuments.length || extraDocuments.length)
                ? [...attachmentDocuments, ...extraDocuments]
                : null,
            applied_suggestions: getAppliedSuggestionsPayload(),
            libavoid: promptSettings.libavoid,
            image_replicate: promptSettings.image_replicate,
            image_mode: promptSettings.image_mode,
            animated_connectors: promptSettings.animated_connectors,
            three_d: !!promptSettings.three_d,
            suggestions: !!promptSettings.suggestions,
            architecture_interview: !!promptSettings.architecture_interview,
            diagram_views: promptSettings.diagram_views || ['logical'],
            adr_notes: !!promptSettings.adr_notes,
            raid_register: !!promptSettings.raid_register,
            save_captures: !!promptSettings.save_captures,
            interview_answers: (typeof pendingInterviewAnswers === 'object' && pendingInterviewAnswers)
                ? pendingInterviewAnswers
                : null,
        };

        if (typeof window !== 'undefined' && window._awaitingInterview) {
            chatBody.interview_answers = { answers: text };
            chatBody.architecture_interview = true;
            window._awaitingInterview = false;
            pendingInterviewAnswers = { answers: text };
        } else if (pendingInterviewAnswers) {
            chatBody.interview_answers = pendingInterviewAnswers;
        }

        let data;
        try {
            data = await postChatStream(chatBody, chatAbortController.signal);
        } catch (streamErr) {
            if (streamErr.name === 'AbortError') throw streamErr;
            appendVerboseLog('client', `Stream unavailable, falling back to /chat: ${streamErr.message}`, 'warn');
            const res = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: chatAbortController.signal,
                body: JSON.stringify(chatBody),
            });
            setProgress(45);
            setDiagramStatus('Building diagram XML...');
            data = await res.json();
            mergeServerLogs(data.logs);
            if (!res.ok) throw new Error(formatErrorDetail(data.detail) || 'Server error');
        }

        setProgress(45);
        setDiagramStatus('Building diagram XML...');

        if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

        if (data.interview_required) {
            window._awaitingInterview = true;
            pendingInterviewAnswers = null;
            const assistantText = data.assistant_message || 'Please answer the architecture interview questions.';
            chatMessages.push({ role: 'assistant', content: assistantText });
            appendChatBubble('assistant', assistantText);
            setTyping(false);
            finishChatRun({
                outcome: 'success',
                message: 'Answer the interview questions in chat, then send again.',
            });
            return { outcome: 'success' };
        }

        pendingInterviewAnswers = null;
        const assistantText = data.assistant_message || 'Diagram updated.';
        // Keep the full assistant_message in chatMessages (sent back to the LLM
        // as conversation history on the next turn) but show only a short
        // one-liner in the chat bubble itself — the diagram/Overview Panel is
        // where the detailed explanation belongs, not a wall of text in chat.
        chatMessages.push({ role: 'assistant', content: assistantText });

        if (promptSettings.show_reasoning && Array.isArray(data.logs) && data.logs.length) {
            const steps = data.logs
                .filter((l) => l && (l.step === 'architecture_spec' || l.step === 'surgical_edit'
                    || l.step === 'llm' || l.step === 'prompt' || l.step === 'mcp'))
                .map((l) => `• [${l.step}] ${l.message || ''}`)
                .slice(0, 24);
            if (steps.length) {
                appendChatBubble('assistant', 'AI reasoning / pipeline steps:\n' + steps.join('\n'));
            }
        }

        appendVerboseLog('client', 'Server response received', 'success', {
            suggestions: (data.suggestions || []).length,
            diagram_type: data.diagram_type || 'cloud',
            xml_chars: (data.xml || '').length,
            mermaid_chars: (data.mermaid || '').length,
            edit_mode: !!data.edit_mode,
        });

        // Soft-failed branches (quota, view quality gate, missing secondary tab).
        try {
            if (Array.isArray(data.degraded_features) && data.degraded_features.length) {
                const names = data.degraded_features.map((d) => d.feature).join(', ');
                const quotaOnly = data.degraded_features.every((d) => d.reason === 'quota');
                const msg = quotaOnly
                    ? `Provider quota/rate limit hit — used fallback for: ${names}`
                    : `Some features degraded: ${names}`;
                showToast(msg, 'warning', 6500);
                appendVerboseLog('degraded', msg, 'warn', data.degraded_features);
            }
        } catch (e) { appendVerboseLog('client', 'Degraded-features banner error', 'error', { error: String(e) }); }

        currentXml = data.xml || '';
        previousXml = xmlForRequest;
        previousArchitecture = currentArchitecture;
        if (data.edit_mode) {
            currentArchitecture = null;
        } else {
            currentArchitecture = data.architecture || null;
        }

        clearAttachments();

        // NOTE: a response missing cost_estimate/well_architected/compliance means
        // *this turn* didn't compute it (e.g. a surgical edit, a non-"network" view
        // this request, or a simple/Mermaid diagram) — it does NOT mean the user
        // wants the setting off. Only the explicit hide*Panel()/voice "hide X panel"
        // path may uncheck the toggle and persist that to localStorage; a response
        // handler must re-sync the panel to whatever the toggle already says
        // (toggle*Panel()), never flip the toggle itself, or a single unrelated
        // request permanently disables a setting the user turned on.
        try {
            if (data.cost_estimate) {
                displayCostEstimate(data.cost_estimate);
                updateRightPanelVisibility();
            } else if (typeof toggleCostPanelPreview === 'function') {
                toggleCostPanelPreview();
            }
        } catch (e) { appendVerboseLog('client', 'Cost panel error', 'error', { error: String(e) }); }

        try {
            if (data.well_architected) {
                displayWellArchitected(data.well_architected);
                updateRightPanelVisibility();
                if (typeof requestDrawioFocusOnLoad === 'function') requestDrawioFocusOnLoad('Network', data.xml);
                if (typeof focusMobileInsightsTab === 'function') focusMobileInsightsTab('waf');
            } else if (typeof toggleWellArchitectedPanel === 'function') {
                toggleWellArchitectedPanel();
            }
        } catch (e) { appendVerboseLog('client', 'WAF panel error', 'error', { error: String(e) }); }

        try {
            if (data.compliance) {
                window._lastComplianceData = data.compliance;
                displayCompliance(data.compliance);
                updateRightPanelVisibility();
                if (typeof requestDrawioFocusOnLoad === 'function') requestDrawioFocusOnLoad('Network', data.xml);
            } else {
                window._lastComplianceData = null;
                if (typeof toggleCompliancePanel === 'function') toggleCompliancePanel();
            }
        } catch (e) { appendVerboseLog('client', 'Compliance panel error', 'error', { error: String(e) }); }

        try {
            if (data.llm_usage) {
                displayTokenUsage(data.llm_usage);
            } else {
                hideTokenUsage();
            }
        } catch (e) { appendVerboseLog('client', 'Token usage badge error', 'error', { error: String(e) }); }

        try {
            if (promptSettings.suggestions) {
                displaySuggestions(data.suggestions || []);
            }
        } catch (e) { appendVerboseLog('client', 'Suggestions panel error', 'error', { error: String(e) }); }

        try {
            if (typeof displayGovernance === 'function') {
                displayGovernance(data.governance_findings || []);
                if (typeof updateRightPanelVisibility === 'function') updateRightPanelVisibility();
            }
        } catch (e) { appendVerboseLog('client', 'Governance panel error', 'error', { error: String(e) }); }

        setTyping(false);
        appendChatBubble('assistant', summarizeAssistantMessage(assistantText));
        // Only the IR path ever populates design_tradeoffs (surgical edits/3D
        // use the direct-XML path, which has no IR to pull it from) — showing
        // this section there would always read empty, which is confusing
        // noise, not clarity, so it's scoped to where the feature actually runs.
        if (data.generation_path === 'ir' && typeof appendDesignRationaleCard === 'function') {
            appendDesignRationaleCard(data.design_tradeoffs);
        }

        setProgress(75);
        setDiagramStatus('Loading into Draw.io...');
        loadDiagramFromResponse(data);
        if (promptSettings.vlm_validation && data.diagram_type !== 'mermaid' && data.xml) {
            runVisualValidation(data.xml, !!options.isValidationRetry);
        }
        setActionButtonsEnabled(true);
        setProgress(100);
        finishChatRun({
            outcome: 'success',
            message: 'Done — diagram updated. Pan and zoom in the preview.',
        });
        appendVerboseLog('client', 'Chat turn complete', 'success');
        return { outcome: 'success' };
    } catch (err) {
        if (options.platformConvert && options.previousPlatform) {
            applyPlatformUi(options.previousPlatform);
        }
        if (err.name === 'AbortError' || runStoppedByUser) {
            if (!options.skipInput || !options.platformConvert) chatMessages.pop();
            appendVerboseLog('client', 'Request stopped by user', 'warn');
            finishChatRun({
                outcome: 'stopped',
                message: 'Stopped — edit your message and send again when ready.',
            });
            return { outcome: 'stopped' };
        } else {
            if (!options.skipInput || !options.platformConvert) chatMessages.pop();
            setTyping(false);
            appendVerboseLog('client', err.message || 'Request failed', 'error');
            finishChatRun({
                outcome: 'error',
                message: err.message || 'Request failed. Check the server logs.',
            });
            return { outcome: 'error' };
        }
    } finally {
        chatAbortController = null;
        runStoppedByUser = false;
        isSending = false;
        sendBtn.disabled = false;
        input.disabled = false;
        updateSendStopButtons();
        setPlatformTabsBusy(false);
        setSuggestionPanelsBusy(false);
        input.focus();
    }
}

// ── Provider select ───────────────────────────────────────

/** Populates any <select> with the exact same provider/model catalog the
 * main chat composer offers (server providers incl. multi-model optgroups,
 * plus custom BYOK providers) — shared by initProviderSelect() and
 * initValidationProviderSelect() so "pick a model to validate with" is never
 * a second, narrower list than "pick a model to chat with". `includeAuto`
 * prepends an "auto" option (e.g. "Same as chat") for selects where that's
 * a meaningful default. */
function populateProviderOptions(select, data, { includeAuto = false, autoLabel = 'Auto' } = {}) {
    // The static HTML placeholder is a single hardcoded <option> — only treat
    // the current value as a real prior selection worth preserving once the
    // select has actually been populated from /providers at least once.
    const hadRealOptions = select.options.length > 1;
    const previousValue = select.value;
    select.innerHTML = '';
    if (includeAuto) {
        const autoOpt = document.createElement('option');
        autoOpt.value = 'auto';
        autoOpt.textContent = autoLabel;
        select.appendChild(autoOpt);
    }
    let defaultValue = data.default || '';
    for (const p of (data.providers || [])) {
        if (p.models && p.models.length > 1) {
            // Multi-model provider (e.g. NVIDIA NIM) — one grouped option per
            // model, "provider header + model list" style.
            const group = document.createElement('optgroup');
            group.label = p.label;
            for (const modelId of p.models) {
                const opt = document.createElement('option');
                opt.value = p.key + '::' + modelId;
                opt.textContent = modelId;
                group.appendChild(opt);
            }
            select.appendChild(group);
            if (p.key === data.default) defaultValue = p.key + '::' + p.models[0];
        } else {
            const opt = document.createElement('option');
            opt.value = p.key;
            opt.textContent = p.label;
            select.appendChild(opt);
        }
    }
    const customProviders = (typeof getCustomProviders === 'function') ? getCustomProviders() : [];
    if (customProviders.length) {
        const group = document.createElement('optgroup');
        group.label = 'Custom';
        for (const p of customProviders) {
            const models = (p.models && p.models.length) ? p.models : [''];
            for (const modelId of models) {
                const opt = document.createElement('option');
                opt.value = 'custom:' + p.id + '::' + modelId;
                opt.textContent = models.length > 1 ? `${p.label} — ${modelId}` : p.label;
                group.appendChild(opt);
            }
        }
        select.appendChild(group);
    }
    if (defaultValue) {
        const defaultOpt = select.querySelector(`option[value="${CSS.escape(defaultValue)}"]`);
        if (defaultOpt) defaultOpt.setAttribute('data-default', '');
    }
    if (hadRealOptions && previousValue && select.querySelector(`option[value="${CSS.escape(previousValue)}"]`)) {
        select.value = previousValue;
    } else if (includeAuto) {
        select.value = 'auto';
    } else if (defaultValue) {
        select.value = defaultValue;
    }
}

async function initProviderSelect() {
    const select = document.getElementById('provider-select');
    if (!select) return;
    try {
        const res = await fetch('/providers');
        const data = await res.json();
        populateProviderOptions(select, data);
        if (typeof renderProviderPickerButton === 'function') renderProviderPickerButton();
    } catch (err) {
        appendVerboseLog('client', 'Failed to load providers', 'error', { error: String(err) });
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

function buildFixPrompt(issues) {
    return 'Fix these diagram issues found by visual QA:\n' + issues.map((it, i) => {
        const hint = it.element_hint ? ` (${it.element_hint})` : '';
        return `${i + 1}. [${it.category}] ${it.description}${hint}`;
    }).join('\n');
}

async function runVisualValidation(xml, isRetry) {
    if (isRetry) {
        updateValidationCard('capturing');
    } else {
        appendValidationCard('capturing');
    }
    let png;
    try {
        png = await exportDiagramPng();
    } catch (err) {
        updateValidationCard('failed', { error: 'Could not capture the diagram: ' + err.message });
        return;
    }
    updateValidationCard('validating');
    const base64 = png.includes(',') ? png.split(',')[1] : png;
    const providerSelect = document.getElementById('validation-provider-select');
    const useOwnChoice = providerSelect && providerSelect.value && providerSelect.value !== 'auto';
    // "Same as chat" (the default) reads the composer's own picker, value
    // format and all — the validation picker offers the exact same catalog
    // (server providers, multi-model optgroups, custom BYOK), so both paths
    // decode through the same parseProviderSelectValue()/custom-payload logic.
    const rawValue = useOwnChoice ? providerSelect.value : document.getElementById('provider-select').value;
    const { provider, model } = parseProviderSelectValue(rawValue);
    const customProvider = (typeof getSelectedCustomProviderPayload === 'function')
        ? getSelectedCustomProviderPayload(useOwnChoice ? 'validation-provider-select' : 'provider-select')
        : null;

    let result;
    try {
        const res = await fetch('/diagram/validate-visual', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_base64: base64, xml, provider, model, custom_provider: customProvider }),
        });
        if (!res.ok) throw new Error('Validation request failed.');
        result = await res.json();
        if (result.status !== 'success') throw new Error(result.detail || 'Validation failed.');
    } catch (err) {
        updateValidationCard('failed', { error: err.message || 'Validation request failed.' });
        return;
    }

    lastValidationFeedback = result;
    const criticalIssues = (result.issues || []).filter((i) => i.severity === 'critical');
    if (criticalIssues.length && vlmAutoRetryCount < MAX_VLM_AUTO_RETRIES) {
        vlmAutoRetryCount += 1;
        updateValidationCard('retrying', result);
        await sendMessage({
            skipInput: true,
            message: buildFixPrompt(criticalIssues),
            showUserBubble: false,
            isValidationRetry: true,
        });
        return;
    }

    const finalState = criticalIssues.length ? 'failed' : ((result.issues || []).length ? 'warning' : 'success');
    updateValidationCard(finalState, result);
}

async function retryValidationFix(cardId) {
    if (cardId !== window._activeValidationCardId) return;
    if (!lastValidationFeedback || !lastValidationFeedback.issues) return;
    const criticalIssues = lastValidationFeedback.issues.filter((i) => i.severity === 'critical');
    if (!criticalIssues.length) return;
    await sendMessage({ skipInput: true, message: buildFixPrompt(criticalIssues), showUserBubble: false });
}

// ── Boot sequence ─────────────────────────────────────────
initAppTheme();
initInsightSelection();
initSuggestionsPanel();
initVerboseLogging();
initPromptSettings();
initDiagramMode();
initProviderSelect();
initValidationProviderSelect();
hideValidationBanner();
updateSendStopButtons();
loadTemplates();
if (typeof ensureSessionId === 'function') ensureSessionId();
const _urlSessionId = new URLSearchParams(window.location.search).get('session');
if (_urlSessionId && typeof openSession === 'function') {
    openSession(_urlSessionId);
} else if (typeof refreshVersionStrip === 'function') {
    refreshVersionStrip();
}
if (typeof loadCustomProviders === 'function') loadCustomProviders();
if (typeof initVoiceCommands === 'function') initVoiceCommands();
initVoiceInput();
ensureDrawioFrame();
const bootTheme = localStorage.getItem(THEME_STORAGE_KEY) || 'kennedy';
appendVerboseLog('drawio', 'Embed iframe created', 'info', { url: getDrawioUrl(bootTheme) });
applyPlatformUi('aws');
renderExampleChips();
renderPatternGallery();

const chatInput = document.getElementById('chat-input');
chatInput.addEventListener('input', autoResizeChatInput);
chatInput.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' && !e.shiftKey) || ((e.ctrlKey || e.metaKey) && e.key === 'Enter')) {
        e.preventDefault();
        sendMessage();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeSettings();
});
