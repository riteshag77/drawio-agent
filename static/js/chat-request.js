/**
 * Assembling one /chat request.
 *
 * Everything that has to be resolved before a turn can be sent: which XML the
 * backend edits against, any URL the user typed inline, the request body
 * itself, and the architecture-interview answers carried across rounds.
 */

/** provider-select's value is either a bare provider key, "custom:<id>", or
 * "<providerKey>::<modelId>" for a multi-model provider (e.g. NVIDIA NIM). */
function parseProviderSelectValue(value) {
    if (!value) return { provider: '', model: null };
    if (value.startsWith('custom:')) return { provider: value, model: null };
    const sep = value.indexOf('::');
    if (sep === -1) return { provider: value, model: null };
    return { provider: value.slice(0, sep), model: value.slice(sep + 2) };
}

/** The XML the backend should edit against.
 *
 * Prefers an attached .drawio over the canvas, and reads the *live* canvas
 * rather than `currentXml` when there is no architecture object — the user may
 * have moved things by hand since the last generation, and those edits are
 * only in the iframe. */
async function resolveXmlForRequest() {
    if (attachmentDiagramXml && attachmentDiagramXml.length > 200) {
        return attachmentDiagramXml;
    }
    if (!currentArchitecture) {
        const liveXml = await exportDiagramXml();
        return (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);
    }
    return null;
}

/** Pulls in a URL the user typed inline, on a short leash.
 *
 * Time-boxed because this sits between pressing send and anything happening:
 * an unreachable page must cost a couple of seconds, not the turn. A failure
 * is logged and skipped, never surfaced — the message still has its own text. */
async function collectInlineUrlContext(text) {
    let contextText = attachmentContext || '';
    const extraDocuments = [];
    const urlMatch = text.match(/https?:\/\/[^\s<>"']+/);
    const urlAlreadyImported = urlMatch && attachments.some((a) => a.kind === 'url' && a.url === urlMatch[0]);
    if (urlMatch && !urlAlreadyImported && !contextText.includes(urlMatch[0])) {
        try {
            const data = await Promise.race([
                fetchUrlData(urlMatch[0]),
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
    return { contextText, extraDocuments };
}

/** The /chat request payload. Pure assembly from already-resolved inputs —
 *  every await happens before this is called, so the whole shape of a request
 *  is readable in one place. */
function buildChatBody({ options, provider, model, promptSettings, xmlForRequest, contextText, extraDocuments }) {
    return {
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
        // Tells the backend which kind of diagram it is about to edit. A
        // follow-up like "change Q3 to 200" has no vocabulary of its own,
        // so without this the classifier's tie-break sent chart edits down
        // the cloud surgical-edit path, which relabels a bar without
        // re-plotting it. Only meaningful alongside current_xml.
        current_diagram_type: xmlForRequest ? (isSimpleDiagram ? 'simple' : 'cloud') : null,
        previous_xml: previousXml,
        custom_system_message: promptSettings.custom_system_message,
        cost_estimate: promptSettings.cost_estimate,
        well_architected: promptSettings.well_architected,
        compliance: promptSettings.compliance,
        governance: !!promptSettings.governance,
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
}

/** Carries architecture-interview answers onto the request.
 *
 * Merge, never replace. A follow-up round used to overwrite this object, so
 * answering a second question silently threw away everything said in the
 * first round — the diagram was then drawn from the follow-up alone.
 * `_rounds` is what the server counts to stop after one follow-up. */
function applyInterviewAnswers(chatBody, text) {
    if (typeof window !== 'undefined' && window._awaitingInterview) {
        const prior = (typeof window._interviewAnswersSoFar === 'object' && window._interviewAnswersSoFar)
            ? window._interviewAnswersSoFar
            : {};
        const round = Number(prior._rounds || 0) + 1;
        const merged = Object.assign({}, prior, { _rounds: String(round) });
        merged['answers' + (round > 1 ? '_round' + round : '')] = text;
        chatBody.interview_answers = merged;
        chatBody.architecture_interview = true;
        window._awaitingInterview = false;
        pendingInterviewAnswers = merged;
    } else if (pendingInterviewAnswers) {
        chatBody.interview_answers = pendingInterviewAnswers;
    }
}
