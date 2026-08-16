/**
 * Running one chat turn.
 *
 * sendMessage() is the orchestrator and should stay readable as a sequence of
 * named stages: begin -> build -> post -> apply -> end. Anything that grows a
 * body of its own belongs in one of the chat-*.js siblings, not inline here.
 *
 * beginChatRun/endChatRun are a pair: every exit path runs the second, or a
 * failed turn leaves the composer disabled and strands the user.
 */

/** Lock the UI and put the turn on screen. Pairs with endChatRun().
 *
 * Returns the two voice facts the exit paths need, because both must be read
 * *before* the mic is touched — deciding afterwards reads whatever the flags
 * have since become rather than what was true when the turn started. */
function beginChatRun({ text, options, provider, isVoiceTurn, sendBtn, input }) {
    if (!options.isValidationRetry) {
        hideValidationBanner();
        vlmAutoRetryCount = 0;
    }
    // A voice-started turn should hand control back to the mic once it
    // finishes, or every voice edit needs a manual Space-press to continue the
    // conversation, defeating the point of doing this hands-free.
    //
    // Voice mode never silences the mic: staying open for the whole turn is
    // what lets the user say "stop" mid-generation or interrupt the spoken
    // reply. Its protection against hearing itself is the echo filter in
    // voice.js, not muting.
    const inVoiceMode = (typeof voiceMode !== 'undefined') && voiceMode;
    const wasVoiceListening = isVoiceTurn && isListening && !inVoiceMode;
    if (isListening && !inVoiceMode) {
        voiceManuallyStopping = true;
        try { speechRecognition.stop(); } catch (_) {}
    }
    if (inVoiceMode && typeof setVoiceState === 'function') setVoiceState('thinking');
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

    // A diagram already on screen means this is very likely a surgical edit
    // (rename/add-one-service/etc.), not a fresh build — those two paths now
    // have very different real latency (an edit lands in ~5-15s; a fresh
    // multi-view generation can take 30s+). Saying "Analyzing architecture…"
    // for a one-word rename sets the wrong expectation right when it matters
    // most: a voice command should read back something that matches how
    // fast the change actually lands, not a generic message.
    const hasExistingDiagram = !!(currentXml || currentArchitecture) && !options.platformConvert;
    setProgress(10);
    setTyping(true);
    setComposerRunStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : hasExistingDiagram
                ? 'Applying your edit…'
                : 'Generating diagram…',
        'running'
    );
    setDiagramStatus(
        options.platformConvert
            ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
            : hasExistingDiagram
                ? 'Applying edit — small changes are usually quick…'
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
    return { inVoiceMode, wasVoiceListening };
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

    // Captured once per call so every exit path below (success/stopped/error/
    // interview) attributes correctly to *this* turn, not whatever the flag
    // happens to be by the time a later branch runs.
    const isVoiceTurn = (typeof consumeVoiceInitiatedTurn === 'function') ? consumeVoiceInitiatedTurn() : false;

    const { inVoiceMode, wasVoiceListening } = beginChatRun({
        text, options, provider, isVoiceTurn, sendBtn, input,
    });

    try {
        const promptSettings = getPromptSettings();
        const xmlForRequest = await resolveXmlForRequest();
        previousXml = currentXml || null;

        const { contextText, extraDocuments } = await collectInlineUrlContext(text);

        appendVerboseLog('client', 'POST /chat/stream', 'info', {
            ...promptSettings,
            has_current_xml: !!xmlForRequest,
            xml_chars: (xmlForRequest || '').length,
        });
        const chatBody = buildChatBody({
            options, provider, model, promptSettings,
            xmlForRequest, contextText, extraDocuments,
        });

        applyInterviewAnswers(chatBody, text);

        const data = await postChatWithFallback(chatBody, chatAbortController.signal);

        setProgress(45);
        setDiagramStatus('Building diagram XML...');

        if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

        const ctx = { options, promptSettings, xmlForRequest, isVoiceTurn, wasVoiceListening };

        if (data.interview_required) return await handleInterviewRequired(data, ctx);

        return await applyChatSuccess(data, ctx);
    } catch (err) {
        return await handleChatFailure(err, { options, isVoiceTurn, wasVoiceListening });
    } finally {
        endChatRun({ sendBtn, input, inVoiceMode });
    }
}

/** A stop and a genuine failure both land here, and they are not the same
 *  thing: stopping is the user's own choice and gets no error treatment. Both
 *  pop the user message they were about to answer, so a retry does not
 *  duplicate it in history. */
async function handleChatFailure(err, { options, isVoiceTurn, wasVoiceListening }) {
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
        if (wasVoiceListening && typeof resumeVoiceListening === 'function') resumeVoiceListening();
        return { outcome: 'stopped' };
    }
    if (!options.skipInput || !options.platformConvert) chatMessages.pop();
    setTyping(false);
    appendVerboseLog('client', err.message || 'Request failed', 'error');
    finishChatRun({
        outcome: 'error',
        message: err.message || 'Request failed. Check the server logs.',
    });
    if (isVoiceTurn && typeof speakVoiceReply === 'function') {
        await speakVoiceReply('Edit failed. ' + (err.message || 'Check the log panel for details.'));
    }
    if (wasVoiceListening && typeof resumeVoiceListening === 'function') resumeVoiceListening();
    return { outcome: 'error' };
}

/** Unlock the UI. Runs on every exit path — success, stop, and error alike —
 *  because a composer left disabled by a failed turn strands the user. */
function endChatRun({ sendBtn, input, inVoiceMode }) {
    chatAbortController = null;
    runStoppedByUser = false;
    isSending = false;
    sendBtn.disabled = false;
    input.disabled = false;
    updateSendStopButtons();
    setPlatformTabsBusy(false);
    setSuggestionPanelsBusy(false);
    input.focus();
    if (inVoiceMode) {
        // Anything said while the pipeline was running was buffered rather
        // than dropped — run it now. Talking over a long generation is
        // normal in a conversation.
        if (typeof setVoiceState === 'function') setVoiceState('listening');
        if (typeof flushPendingVoiceTurn === 'function') flushPendingVoiceTurn();
    }
}
