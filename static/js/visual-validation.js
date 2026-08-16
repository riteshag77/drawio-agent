/**
 * Client-triggered screenshot QA.
 *
 * Distinct from the always-on DIAGRAM_VLM_QA geometry linter: this captures
 * what was actually rendered and asks a vision model to review it.
 */

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
