/**
 * Applying one /chat response to the UI.
 *
 * The success path and the panels it drives. Split from chat-send.js because
 * this is where a response is *read*, not where a turn is *run* — the two
 * change for different reasons.
 */

// ── Response panels ───────────────────────────────────────
//
// Every block below is individually try/caught on purpose: these are six
// independent side panels, and one of them throwing must not cost the user the
// diagram that has already been generated and paid for.

/** Soft-failed branches (quota, view quality gate, missing secondary tab). */
function reportDegradedFeatures(data) {
    try {
        if (!Array.isArray(data.degraded_features) || !data.degraded_features.length) return;
        const names = data.degraded_features.map((d) => d.feature).join(', ');
        const quotaOnly = data.degraded_features.every((d) => d.reason === 'quota');
        const msg = quotaOnly
            ? `Provider quota/rate limit hit — used fallback for: ${names}`
            : `Some features degraded: ${names}`;
        showToast(msg, 'warning', 6500);
        appendVerboseLog('degraded', msg, 'warn', data.degraded_features);
    } catch (e) {
        appendVerboseLog('client', 'Degraded-features banner error', 'error', { error: String(e) });
    }
}

/** Sync every insight panel to one response.
 *
 * NOTE: a response missing cost_estimate/well_architected/compliance means
 * *this turn* didn't compute it (e.g. a surgical edit, a non-"network" view
 * this request, or a simple/non-cloud diagram) — it does NOT mean the user
 * wants the setting off. Only the explicit hide*Panel()/voice "hide X panel"
 * path may uncheck the toggle and persist that to localStorage; a response
 * handler must re-sync the panel to whatever the toggle already says
 * (toggle*Panel()), never flip the toggle itself, or a single unrelated
 * request permanently disables a setting the user turned on.
 */
function renderResponsePanels(data, promptSettings) {
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
        // Gated on the toggle, matching Suggestions above — was
        // unconditional before, the one report panel whose toggle state
        // was tracked (savePromptSettings persists it) but never actually
        // wired into the display path. The server now mirrors this: the
        // findings themselves aren't computed when the request didn't
        // ask for them, so an off response has nothing to show anyway.
        if (promptSettings.governance && typeof displayGovernance === 'function') {
            displayGovernance(data.governance_findings || []);
            // A failure is worth surfacing even if the panel is currently
            // closed — same "auto-open for something you need to see"
            // rule as turning a toggle on, just triggered by new results
            // instead of a click. A passing report stays quiet.
            const govOn = document.getElementById('governance-toggle')?.checked;
            if (govOn && typeof lastGovernanceFailedCount !== 'undefined' && lastGovernanceFailedCount > 0
                    && typeof openRightPanel === 'function') {
                openRightPanel();
            }
            if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
        }
    } catch (e) { appendVerboseLog('client', 'Governance panel error', 'error', { error: String(e) }); }
}

/** The server wants more information before it will draw anything. Nothing is
 *  applied to the canvas — this turn only asks a question. */
async function handleInterviewRequired(data, ctx) {
    window._awaitingInterview = true;
    // Carry what has already been answered into the next send. Setting
    // pendingInterviewAnswers to null here is what dropped round one.
    window._interviewAnswersSoFar = data.interview_answers_so_far || {};
    pendingInterviewAnswers = null;
    const assistantText = data.assistant_message || 'Please answer the architecture interview questions.';
    chatMessages.push({ role: 'assistant', content: assistantText });
    appendChatBubble('assistant', assistantText);
    setTyping(false);
    finishChatRun({
        outcome: 'success',
        message: 'Answer the interview questions in chat, then send again.',
    });
    if (ctx.isVoiceTurn && typeof speakVoiceReply === 'function') {
        await speakVoiceReply(assistantText);
    }
    if (ctx.wasVoiceListening && typeof resumeVoiceListening === 'function') resumeVoiceListening();
    return { outcome: 'success' };
}

/** Apply a successful response: chat history, panels, and the diagram itself. */
async function applyChatSuccess(data, ctx) {
    const { options, promptSettings, xmlForRequest, isVoiceTurn, wasVoiceListening } = ctx;

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
        edit_mode: !!data.edit_mode,
    });

    reportDegradedFeatures(data);

    currentXml = data.xml || '';
    previousXml = xmlForRequest;
    previousArchitecture = currentArchitecture;
    if (data.edit_mode) {
        currentArchitecture = null;
    } else {
        currentArchitecture = data.architecture || null;
    }

    clearAttachments();

    renderResponsePanels(data, promptSettings);

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
    if (promptSettings.vlm_validation && data.diagram_type !== 'simple' && data.xml) {
        runVisualValidation(data.xml, !!options.isValidationRetry);
    }
    setActionButtonsEnabled(true);
    setProgress(100);
    finishChatRun({
        outcome: 'success',
        message: 'Done — diagram updated. Pan and zoom in the preview.',
    });
    if (isVoiceTurn && typeof speakVoiceReply === 'function') {
        await speakVoiceReply(summarizeAssistantMessage(assistantText));
    }
    if (wasVoiceListening && typeof resumeVoiceListening === 'function') resumeVoiceListening();
    appendVerboseLog('client', 'Chat turn complete', 'success');
    return { outcome: 'success' };
}
