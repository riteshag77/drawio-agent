function hideValidationBanner() {
    validationPanelActive = false;
    const panel = document.getElementById('validation-panel');
    panel.classList.remove('active', 'critical', 'passed');
    document.getElementById('validation-panel-body').innerHTML = '';
    document.getElementById('btn-fix-layout').style.display = 'none';
    lastValidationFeedback = null;
    updateRightPanelVisibility();
}

function showValidationBanner(validation, feedback) {
    const panel = document.getElementById('validation-panel');
    const body = document.getElementById('validation-panel-body');
    const fixBtn = document.getElementById('btn-fix-layout');
    body.innerHTML = '';

    const issues = validation?.issues || [];
    issues.forEach(issue => {
        const item = document.createElement('div');
        item.className = `validation-issue-item ${issue.severity}`;
        item.innerHTML = `
            <div class="validation-issue-type">${issue.type} · ${issue.severity}</div>
            <div>${issue.description}</div>`;
        body.appendChild(item);
    });

    const suggestions = validation?.suggestions || [];
    if (suggestions.length) {
        const title = document.createElement('div');
        title.className = 'validation-suggestions-title';
        title.textContent = 'Suggestions';
        body.appendChild(title);
        suggestions.forEach(text => {
            const item = document.createElement('div');
            item.className = 'validation-suggestion-item';
            item.textContent = text;
            body.appendChild(item);
        });
    }

    const hasCritical = issues.some(i => i.severity === 'critical');
    panel.classList.remove('passed');
    panel.classList.toggle('critical', hasCritical);
    panel.classList.add('active');
    fixBtn.style.display = 'block';

    const badge = document.getElementById('validation-status-badge');
    badge.textContent = hasCritical
        ? `${issues.length} critical`
        : `${issues.length} warning${issues.length === 1 ? '' : 's'}`;

    validationPanelActive = true;
    lastValidationFeedback = feedback || buildClientValidationFeedback(validation);
    updateRightPanelVisibility();
}

function buildClientValidationFeedback(validation) {
    if (!validation?.issues?.length) return null;
    const lines = ['DIAGRAM VALIDATION FAILED', ''];
    validation.issues.forEach(issue => {
        lines.push(`- [${issue.type}] ${issue.description}`);
    });
    (validation.suggestions || []).forEach(s => lines.push(`- ${s}`));
    return lines.join('\n');
}

function showValidationPassed() {
    const panel = document.getElementById('validation-panel');
    const body = document.getElementById('validation-panel-body');
    const badge = document.getElementById('validation-status-badge');
    const fixBtn = document.getElementById('btn-fix-layout');
    if (!panel || !body) return;

    panel.classList.remove('critical');
    panel.classList.add('active', 'passed');
    badge.textContent = 'Passed';
    body.innerHTML = `
        <div class="validation-pass-note">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
            <span>No visual layout issues detected. Spacing, overlaps, and edge routing look good.</span>
        </div>`;
    if (fixBtn) fixBtn.style.display = 'none';
    lastValidationFeedback = null;
    validationPanelActive = true;
    updateRightPanelVisibility();
}

async function runVisualValidation() {
    const settings = getPromptSettings();
    if (!settings.vlm_validation) return;
    if (!currentArchitecture && !(currentXml && currentXml.length > 200)) return;

    try {
        appendVerboseLog('validation', 'Exporting diagram PNG for VLM check', 'info');
        const imageData = await exportDiagramPng();
        const res = await fetch('/validate-diagram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_data: imageData, validation_provider: settings.validation_provider }),
        });
        const data = await res.json();
        mergeServerLogs(data.logs);
        if (!res.ok) return;

        const validation = data.validation;
        appendVerboseLog('validation', 'VLM validation result', validation?.valid ? 'success' : 'warn', validation);

        if (validation?.skipped) {
            appendVerboseLog('validation', 'VLM validation skipped', 'warn', validation);
            return;
        }

        if (validation && !validation.valid) {
            const feedback = buildClientValidationFeedback(validation);
            if (vlmAutoRetryCount < MAX_VLM_CLIENT_RETRIES && feedback) {
                vlmAutoRetryCount += 1;
                appendVerboseLog(
                    'validation',
                    `VLM auto-retry ${vlmAutoRetryCount}/${MAX_VLM_CLIENT_RETRIES}`,
                    'info',
                );
                await autoRetryWithVlm(imageData, feedback);
                return;
            }
            showValidationBanner(validation, feedback);
            setDiagramStatus('Diagram loaded — layout issues detected');
        } else {
            vlmAutoRetryCount = 0;
            showValidationPassed();
        }
    } catch (err) {
        appendVerboseLog('validation', `VLM validation skipped: ${err.message}`, 'warn');
    }
}

async function autoRetryWithVlm(imageData, feedback) {
    if (isSending) return;
    const provider = document.getElementById('provider-select').value;
    const sendBtn = document.getElementById('send-btn');
    const input = document.getElementById('chat-input');
    const fixMessage = 'Fix visual layout issues from validation — improve spacing, edge routing, and remove overlaps.';

    isSending = true;
    sendBtn.disabled = true;
    input.disabled = true;
    updateSendStopButtons();
    hideValidationBanner();
    setTyping(true);
    setComposerRunStatus('Auto-fixing layout from validation…', 'running');
    setDiagramStatus('Auto-fixing layout from validation…');
    document.getElementById('diagram-loading').classList.add('active');

    chatMessages.push({ role: 'user', content: fixMessage });
    appendChatBubble('user', fixMessage);

    chatAbortController = new AbortController();
    runStoppedByUser = false;

    try {
        const promptSettings = getPromptSettings();
        const liveXml = await exportDiagramXml();
        const xmlForRequest = (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);

        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: chatAbortController.signal,
            body: JSON.stringify({
                messages: chatMessages,
                provider,
                validation_provider: promptSettings.validation_provider,
                platform: selectedPlatform,
                diagram_mode: getDiagramMode(),
                diagram_renderer: document.getElementById('diagram-renderer-select')?.value || 'auto',
                current_architecture: currentArchitecture,
                previous_architecture: previousArchitecture,
                current_xml: xmlForRequest,
                previous_xml: previousXml,
                minimal_style: promptSettings.minimal_style,
                custom_system_message: promptSettings.custom_system_message,
                validation_feedback: feedback,
                image_data: imageData,
            }),
        });
        const data = await res.json();
        mergeServerLogs(data.logs);
        if (!res.ok) throw new Error(data.detail || 'Server error');
        if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

        const assistantText = data.assistant_message || 'Diagram updated from validation feedback.';
        chatMessages.push({ role: 'assistant', content: assistantText });
        appendChatBubble('assistant', assistantText, data.suggestions || []);

        currentXml = data.xml;
        previousXml = xmlForRequest;
        previousArchitecture = currentArchitecture;
        if (data.edit_mode) {
            currentArchitecture = null;
        } else {
            currentArchitecture = data.architecture || null;
        }
        document.getElementById('xml-output').textContent = data.xml;

        if (data.validation?.feedback && data.validation?.vlm && !data.validation.vlm.valid) {
            showValidationBanner(data.validation.vlm, data.validation.feedback);
        } else if (data.validation?.structural && !data.validation.structural.valid) {
            showValidationBanner(data.validation.structural, data.validation.feedback);
        }

        loadDiagramFromResponse(data);
        setActionButtonsEnabled(true);
        finishChatRun({
            outcome: 'success',
            message: 'Layout fix complete — diagram updated.',
        });
    } catch (err) {
        if (err.name === 'AbortError' || runStoppedByUser) {
            chatMessages.pop();
            appendVerboseLog('validation', 'VLM auto-retry stopped by user', 'warn');
            finishChatRun({
                outcome: 'stopped',
                message: 'Stopped — layout fix cancelled.',
            });
        } else {
            appendVerboseLog('validation', `VLM auto-retry failed: ${err.message}`, 'error');
            chatMessages.pop();
            showError(err.message || 'Auto-fix failed');
            finishChatRun({
                outcome: 'error',
                message: err.message || 'Auto-fix failed.',
            });
        }
    } finally {
        chatAbortController = null;
        runStoppedByUser = false;
        isSending = false;
        sendBtn.disabled = false;
        input.disabled = false;
        updateSendStopButtons();
    }
}

async function fixLayoutIssues() {
    if (!lastValidationFeedback && !currentArchitecture) return;
    const input = document.getElementById('chat-input');
    input.value = 'Fix the layout issues identified in validation — improve spacing, edge routing, and remove overlaps.';
    await sendMessage({ validationFeedback: lastValidationFeedback });
}
