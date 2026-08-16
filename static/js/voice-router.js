/** Client half of the voice-mode intent router.
 *
 * Reached only when voice-commands.js found no exact phrase match. The registry
 * stays the single source of truth for what the app can be told to do — this
 * ships its ids to the server with each call rather than the server keeping a
 * second copy that could drift.
 */

/** The registry as the router needs to see it: id, what it does, and one
 *  example of how a user says it. */
function voiceCommandCatalog() {
    if (typeof VOICE_COMMANDS === 'undefined') return [];
    const seen = new Set();
    const out = [];
    for (const cmd of VOICE_COMMANDS) {
        if (seen.has(cmd.id)) continue;
        seen.add(cmd.id);
        out.push({
            id: cmd.id,
            label: cmd.label || cmd.id,
            sample: (cmd.phrases && cmd.phrases[0]) || '',
        });
    }
    return out;
}

/** Route one utterance. Resolves to a route object and never rejects — a
 *  hands-free user has no way to act on a thrown error, so the failure path
 *  has to produce something usable. The server fails open too; this covers the
 *  cases that never reach it (offline, timeout, abort). */
async function routeVoiceUtterance(transcript) {
    const text = String(transcript || '').trim();
    if (!text) return { kind: 'clarify', speak: "I didn't hear anything." };

    const hasDiagram = !!(typeof currentXml !== 'undefined' && currentXml);
    const rawValue = (document.getElementById('provider-select') || {}).value || '';
    const parsed = (typeof parseProviderSelectValue === 'function')
        ? parseProviderSelectValue(rawValue)
        : { provider: rawValue, model: null };
    const customProvider = (typeof getSelectedCustomProviderPayload === 'function')
        ? getSelectedCustomProviderPayload('provider-select')
        : null;

    // A router that hangs is worse than one that guesses: the user is standing
    // there having said something, so cap the wait and fall back locally.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), VOICE_TUNING.routerTimeoutMs);
    try {
        const res = await fetch('/voice/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({
                transcript: text,
                commands: voiceCommandCatalog(),
                has_diagram: hasDiagram,
                platform: (typeof selectedPlatform !== 'undefined' && selectedPlatform) || '',
                // Same expression buildChatBody() uses — `isSimpleDiagram` is
                // the real flag (state.js). Naming a global that does not exist
                // silently sent a blank type on every turn, so the router never
                // learned whether it was looking at a bar chart or a VPC, and
                // "change Q3 to 200" was routed with no idea a chart was on
                // screen.
                diagram_type: hasDiagram
                    ? ((typeof isSimpleDiagram !== 'undefined' && isSimpleDiagram) ? 'simple' : 'cloud')
                    : '',
                provider: parsed.provider,
                model: parsed.model,
                custom_provider: customProvider,
            }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.logs && typeof mergeServerLogs === 'function') mergeServerLogs(data.logs);
        return data;
    } catch (err) {
        if (typeof appendVerboseLog === 'function') {
            appendVerboseLog('voice', 'Voice routing failed locally', 'warn', {
                error: String((err && err.message) || err),
            });
        }
        return _localVoiceFallback(text, hasDiagram);
    } finally {
        clearTimeout(timer);
    }
}

/** Mirrors the server's fail-open rule, for the failures that never reach it.
 *  With a diagram on screen an edit is overwhelmingly the likeliest intent;
 *  with an empty canvas there is nothing to edit against, and firing a fresh
 *  generation off an unrouted transcript is the expensive way to be wrong. */
function _localVoiceFallback(text, hasDiagram) {
    if (hasDiagram) {
        return { kind: 'diagram_turn', prompt: text, speak: 'Applying that change.' };
    }
    return {
        kind: 'clarify',
        speak: "I couldn't reach the router — I've put that in the box so you can check it.",
        prompt: text,
    };
}
