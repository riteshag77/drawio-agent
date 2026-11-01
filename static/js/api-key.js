/** Client-side X-API-Key handling for a deployment with API_KEY set
 * (app/main.py's ApiKeyMiddleware). Invisible when API_KEY is unset — no
 * header is ever sent, the verification check trivially succeeds, and the
 * app reveals immediately with no gate ever shown. That single server-side
 * variable is the whole on/off switch; there is deliberately no separate
 * client flag, so the API being locked and the login screen appearing can
 * never disagree with each other.
 *
 * Two things happen here:
 *
 * 1. A blocking access gate, run immediately. index.html's very first
 *    <style> (before any stylesheet, any script, any real content) hides
 *    `.app-shell` via visibility so nothing about the app — not just data,
 *    the layout, the branding, the nav — paints before this script confirms
 *    access. `runAccessGate()` verifies the stored key (or lack of one, if
 *    unset) against a real protected endpoint and only then removes that
 *    style tag. On failure it shows a full-screen, un-dismissable dialog —
 *    no Cancel, like a login screen — and loops until a working key is
 *    entered or the check itself can't be reached (shown as a distinct
 *    "can't reach the server" state with a retry, since that is not the
 *    same failure as a wrong key).
 *
 * 2. window.fetch stays patched globally for the rest of the session, so a
 *    key that stops working after the gate already passed (rotated by an
 *    admin while a tab is open) still gets caught — same dialog, reused in
 *    its dismissable "reactive" mode since at that point there is a session
 *    to fall back out of rather than nothing to show at all. Patching
 *    fetch once covers every one of the ~26 existing call sites across
 *    static/js/ (including app.js's streaming POST to /chat/stream, which
 *    reads the body manually rather than using EventSource — EventSource
 *    cannot send custom headers at all, which is why that path is a plain
 *    fetch() in the first place) without touching any of them.
 *
 * Same storage key/pattern as admin.js's X-Admin-Token: localStorage, not a
 * session.
 */

(function () {
    const STORAGE_KEY = 'drawio-agent-api-key';
    const GATE_STYLE_ID = 'app-shell-gate-style';
    // Any cheap, already-authenticated GET is fine as the access probe —
    // reused rather than adding a dedicated endpoint. Its body is discarded;
    // app.js's own initProviderSelect() re-fetches this moments later to
    // actually populate the dropdown.
    const PROBE_PATH = '/providers';

    function getKey() {
        return localStorage.getItem(STORAGE_KEY) || '';
    }

    function setKey(value) {
        if (value) localStorage.setItem(STORAGE_KEY, value);
        else localStorage.removeItem(STORAGE_KEY);
    }

    /** Only same-origin, root-relative requests carry the key — never a
     * cross-origin URL, which would leak it to whatever that host is. */
    function isProtectedRequest(input) {
        const url = input instanceof Request ? input.url : input;
        return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//');
    }

    function withKeyHeader(input, init, key) {
        const merged = Object.assign({}, init);
        merged.headers = new Headers(init && init.headers ? init.headers : (input instanceof Request ? input.headers : undefined));
        merged.headers.set('X-API-Key', key);
        return merged;
    }

    const nativeFetch = window.fetch.bind(window);

    /** True/false for "this key works", or throws for a network-level
     * failure (server unreachable) — kept distinct from "wrong key" so the
     * gate can tell a typo apart from its own host being down. */
    async function verifyKey(key) {
        const res = await nativeFetch(PROBE_PATH, key ? { headers: { 'X-API-Key': key } } : undefined);
        if (res.status === 401) return false;
        return true; // any non-401 (including a real error unrelated to auth) is not this gate's problem
    }

    /** The no-JS-dependency "Loading…" fallback (see index.html) is only
     * needed until this script has *anything* real to show — the login
     * dialog counts just as much as the revealed app. */
    function hideLoadingFallback() {
        const el = document.getElementById('gate-loading');
        if (el) el.remove();
    }

    // Resolves once the initial gate succeeds. Every OTHER script here
    // (app.js's own initProviderSelect() among them) also fetches protected
    // endpoints on page load through the patched window.fetch below — before
    // the gate succeeds, those would each independently hit a 401 and, left
    // alone, each pop their own reactive dialog on top of the gate's. They
    // await this instead, so every 401 during that window funnels into the
    // one dialog the gate is already showing rather than stacking more.
    let gateResolved = false;
    let resolveGateReady;
    const gateReady = new Promise((resolve) => { resolveGateReady = resolve; });

    function revealApp() {
        hideLoadingFallback();
        const style = document.getElementById(GATE_STYLE_ID);
        if (style) style.remove();
        gateResolved = true;
        resolveGateReady();
    }

    /** Builds the dialog. `blocking: true` (the initial gate) removes Cancel
     * and Escape-to-dismiss and uses an opaque, full-theme background since
     * there is nothing behind it to see — the app was never revealed.
     * `blocking: false` (a later mid-session 401) keeps the dismissable,
     * translucent-scrim form from before, since canceling there just leaves
     * the caller to handle the failed request rather than leaving the user
     * on a permanently blank page. */
    function showKeyDialog({ blocking, errorText }) {
        hideLoadingFallback();
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = blocking ? 'api-key-overlay api-key-overlay-gate' : 'api-key-overlay';
            overlay.innerHTML = `
                <div class="api-key-dialog" role="dialog" aria-modal="true" aria-labelledby="api-key-title">
                    <h2 id="api-key-title">${blocking ? 'Sign in' : 'API key required'}</h2>
                    <p>This deployment requires an API key. Enter the value your host's <code>API_KEY</code> is set to — it is saved in this browser only.</p>
                    <input type="password" id="api-key-input" autocomplete="off" placeholder="API key" />
                    <p class="api-key-error" id="api-key-error" ${errorText ? '' : 'hidden'}>${errorText || ''}</p>
                    <div class="api-key-actions">
                        ${blocking ? '' : '<button type="button" class="api-key-cancel" id="api-key-cancel">Cancel</button>'}
                        <button type="button" class="api-key-save" id="api-key-save">${blocking ? 'Continue' : 'Save'}</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);

            const input = overlay.querySelector('#api-key-input');
            const errorEl = overlay.querySelector('#api-key-error');
            input.focus();

            function close(result) {
                overlay.remove();
                resolve(result);
            }

            function submit() {
                const value = input.value.trim();
                if (!value) {
                    errorEl.textContent = 'Enter a key' + (blocking ? '.' : ', or Cancel.');
                    errorEl.hidden = false;
                    return;
                }
                close(value);
            }

            overlay.querySelector('#api-key-save').addEventListener('click', submit);
            if (!blocking) {
                overlay.querySelector('#api-key-cancel').addEventListener('click', () => {
                    setKey('');
                    close(null);
                });
            }
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') submit();
                if (e.key === 'Escape' && !blocking) { setKey(''); close(null); }
            });
        });
    }

    /** Runs once at load. Blocks the whole app behind `.app-shell`'s hidden
     * style until a working key is confirmed (or confirmed unnecessary). */
    async function runAccessGate() {
        try {
            if (await verifyKey(getKey())) {
                revealApp();
                return;
            }
        } catch (err) {
            // Network failure on the *first* check (server unreachable,
            // DNS, etc.) is not "wrong key" — say so and let them retry
            // rather than asking for a key that was never the problem.
            await showKeyDialog({ blocking: true, errorText: "Couldn't reach the server. Check your connection and click Continue to retry." });
            return runAccessGate();
        }

        // Stored key (if any) didn't work. Loop until one does.
        let errorText = getKey() ? 'That key was rejected.' : undefined;
        for (;;) {
            const entered = await showKeyDialog({ blocking: true, errorText });
            try {
                if (await verifyKey(entered)) {
                    setKey(entered);
                    revealApp();
                    return;
                }
                errorText = 'That key was rejected.';
            } catch (err) {
                errorText = "Couldn't reach the server. Check your connection and try again.";
            }
        }
    }

    let pendingPrompt = null;

    /** Concurrent 401s during the session (e.g. two requests landing at once
     * after a key rotation) all await the same modal instead of each
     * opening their own. */
    function requestKeyEntry() {
        if (!pendingPrompt) {
            pendingPrompt = showKeyDialog({ blocking: false }).finally(() => {
                pendingPrompt = null;
            });
        }
        return pendingPrompt;
    }

    window.fetch = async function apiKeyFetch(input, init) {
        const protectedReq = isProtectedRequest(input);
        const key = protectedReq ? getKey() : '';
        let res = await nativeFetch(input, key ? withKeyHeader(input, init, key) : init);

        if (res.status === 401 && protectedReq) {
            if (!gateResolved) {
                // The blocking gate is already up (or about to be) for this
                // exact reason — wait for the one dialog it's showing rather
                // than opening a second, and retry with whatever key that
                // resolved.
                await gateReady;
                res = await nativeFetch(input, withKeyHeader(input, init, getKey()));
            } else {
                const entered = await requestKeyEntry();
                if (entered) {
                    setKey(entered);
                    res = await nativeFetch(input, withKeyHeader(input, init, entered));
                }
            }
        }
        return res;
    };

    // Exposed for settings.js (or the console) to let a user clear a stored
    // key without hunting through devtools storage.
    window.drawioApiKey = {
        get: getKey,
        clear: function () {
            setKey('');
        },
    };

    runAccessGate();
})();
