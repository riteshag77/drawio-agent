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

async function sendMessage(options = {}) {
    const input = document.getElementById('chat-input');
    const text = (options.skipInput ? options.message : input.value).trim();
    const provider = document.getElementById('provider-select').value;
    const sendBtn = document.getElementById('send-btn');
    const validationFeedback = options.validationFeedback || null;

    if (!text || isSending) {
        if (!text && !validationFeedback) appendVerboseLog('run', 'Please enter a message.', 'warn');
        return;
    }

    hideValidationBanner();
    vlmAutoRetryCount = 0;
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
    } else if (options.platformConvert && text) {
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
        const urlMatch = text.match(/https?:\/\/[^\s<>"']+/);
        if (urlMatch && !contextText.includes(urlMatch[0])) {
            try {
                const urlCtxPromise = parseUrlContext(urlMatch[0]);
                const timed = await Promise.race([
                    urlCtxPromise,
                    new Promise((_, reject) => setTimeout(() => reject(new Error('URL parse timeout')), 2500)),
                ]);
                contextText = (contextText ? contextText + '\n' : '') + timed;
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
            provider,
            custom_provider: (typeof getSelectedCustomProviderPayload === 'function')
                ? getSelectedCustomProviderPayload()
                : null,
            validation_provider: promptSettings.validation_provider,
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
            validation_feedback: validationFeedback,
            context_text: contextText || null,
            attachment_documents: attachmentDocuments.length ? attachmentDocuments : null,
            applied_suggestions: getAppliedSuggestionsPayload(),
            libavoid: promptSettings.libavoid,
            image_replicate: promptSettings.image_replicate,
            image_mode: promptSettings.image_mode,
            animated_connectors: promptSettings.animated_connectors,
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
            return;
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
        } catch (e) { console.error('Degraded-features banner error', e); }

        currentXml = data.xml || '';
        previousXml = xmlForRequest;
        previousArchitecture = currentArchitecture;
        if (data.edit_mode) {
            currentArchitecture = null;
        } else {
            currentArchitecture = data.architecture || null;
        }

        clearAttachments();

        try {
            if (data.cost_estimate) {
                displayCostEstimate(data.cost_estimate);
                updateRightPanelVisibility();
            } else {
                hideCostEstimate();
            }
        } catch (e) { console.error('Cost panel error', e); }

        try {
            if (data.well_architected) {
                displayWellArchitected(data.well_architected);
                updateRightPanelVisibility();
            }
        } catch (e) { console.error('WAF panel error', e); }

        try {
            if (data.compliance) {
                window._lastComplianceData = data.compliance;
                displayCompliance(data.compliance);
                updateRightPanelVisibility();
            }
        } catch (e) { console.error('Compliance panel error', e); }

        try {
            if (data.llm_usage) {
                displayTokenUsage(data.llm_usage);
            } else {
                hideTokenUsage();
            }
        } catch (e) { console.error('Token usage badge error', e); }

        try {
            if (promptSettings.suggestions) {
                displaySuggestions(data.suggestions || []);
            }
        } catch (e) { console.error('Suggestions panel error', e); }

        setTyping(false);
        appendChatBubble('assistant', summarizeAssistantMessage(assistantText));

        setProgress(75);
        setDiagramStatus('Loading into Draw.io...');
        loadDiagramFromResponse(data);
        setActionButtonsEnabled(true);
        setProgress(100);
        finishChatRun({
            outcome: 'success',
            message: 'Done — diagram updated. Pan and zoom in the preview.',
        });
        appendVerboseLog('client', 'Chat turn complete', 'success');
    } catch (err) {
        console.error(err);
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
        } else {
            if (!options.skipInput || !options.platformConvert) chatMessages.pop();
            setTyping(false);
            appendVerboseLog('client', err.message || 'Request failed', 'error');
            finishChatRun({
                outcome: 'error',
                message: err.message || 'Request failed. Check the server logs.',
            });
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

async function initProviderSelect() {
    const select = document.getElementById('provider-select');
    if (!select) return;
    const previousValue = select.value;
    try {
        const res = await fetch('/providers');
        const data = await res.json();
        select.innerHTML = '';
        for (const p of (data.providers || [])) {
            const opt = document.createElement('option');
            opt.value = p.key;
            opt.textContent = p.label;
            select.appendChild(opt);
        }
        const customProviders = (typeof getCustomProviders === 'function') ? getCustomProviders() : [];
        if (customProviders.length) {
            const group = document.createElement('optgroup');
            group.label = 'Custom';
            for (const p of customProviders) {
                const opt = document.createElement('option');
                opt.value = 'custom:' + p.id;
                opt.textContent = p.label;
                group.appendChild(opt);
            }
            select.appendChild(group);
        }
        if (previousValue && select.querySelector(`option[value="${CSS.escape(previousValue)}"]`)) {
            select.value = previousValue;
        } else if (data.default) {
            select.value = data.default;
        }
    } catch (err) {
        console.error('Failed to load providers', err);
    }
}

// ── Boot sequence ─────────────────────────────────────────
initAppTheme();
initInsightSelection();
initSuggestionsPanel();
initVerboseLogging();
initPromptSettings();
initDiagramMode();
initProviderSelect();
hideValidationBanner();
updateSendStopButtons();
loadTemplates();
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
