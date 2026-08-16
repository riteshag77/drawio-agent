/**
 * Chat transport: talking to /chat and /chat/stream, and turning what
 * comes back into something the rest of the app can read.
 *
 * No UI, no state mutation — the two error shapes and the SSE frame format are
 * the only things this file knows about.
 */

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
                    // A fresh generation can run 30s+; silence that long is
                    // indistinguishable from the app having died when the
                    // screen is not what you are attending to.
                    if (typeof speakVoiceProgress === 'function') speakVoiceProgress(evt.message);
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

/** SSE first, plain POST second. The fallback exists because a proxy that
 *  buffers text/event-stream breaks streaming without breaking the API — the
 *  turn should still land, just without progress events. An abort is the
 *  user's own doing and must propagate rather than trigger a retry. */
async function postChatWithFallback(chatBody, signal) {
    try {
        return await postChatStream(chatBody, signal);
    } catch (streamErr) {
        if (streamErr.name === 'AbortError') throw streamErr;
        appendVerboseLog('client', `Stream unavailable, falling back to /chat: ${streamErr.message}`, 'warn');
        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal,
            body: JSON.stringify(chatBody),
        });
        setProgress(45);
        setDiagramStatus('Building diagram XML...');
        const data = await res.json();
        mergeServerLogs(data.logs);
        if (!res.ok) throw new Error(formatErrorDetail(data.detail) || 'Server error');
        return data;
    }
}
