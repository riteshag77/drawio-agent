/** Client-side X-API-Key handling for a deployment with API_KEY set
 * (app/main.py's ApiKeyMiddleware). Invisible when API_KEY is unset — no
 * header is ever sent and no prompt ever shows, since the server never 401s.
 *
 * Loaded first, before every other script, and patches window.fetch globally
 * rather than touching the ~26 existing fetch() call sites across static/js/
 * (including app.js's streaming POST to /chat/stream, which reads the body
 * manually rather than using EventSource — EventSource cannot send custom
 * headers at all, which is why that path is a plain fetch() in the first
 * place). One patch covers every current and future call site.
 *
 * Same key/pattern as admin.js's X-Admin-Token: localStorage, not a session,
 * cleared and re-prompted on 401.
 */

(function () {
    const STORAGE_KEY = 'drawio-agent-api-key';

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

    let pendingPrompt = null;

    /** Concurrent 401s (e.g. a health probe and a chat request landing at
     * once) all await the same modal instead of each opening their own. */
    function requestKeyEntry() {
        if (!pendingPrompt) {
            pendingPrompt = showKeyPrompt().finally(() => {
                pendingPrompt = null;
            });
        }
        return pendingPrompt;
    }

    function showKeyPrompt() {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'api-key-overlay';
            overlay.innerHTML = `
                <div class="api-key-dialog" role="dialog" aria-modal="true" aria-labelledby="api-key-title">
                    <h2 id="api-key-title">API key required</h2>
                    <p>This deployment requires an API key. Enter the value your host's <code>API_KEY</code> is set to — it is saved in this browser only.</p>
                    <input type="password" id="api-key-input" autocomplete="off" placeholder="API key" />
                    <p class="api-key-error" id="api-key-error" hidden></p>
                    <div class="api-key-actions">
                        <button type="button" class="api-key-cancel" id="api-key-cancel">Cancel</button>
                        <button type="button" class="api-key-save" id="api-key-save">Save</button>
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

            function save() {
                const value = input.value.trim();
                if (!value) {
                    errorEl.textContent = 'Enter a key, or Cancel.';
                    errorEl.hidden = false;
                    return;
                }
                setKey(value);
                close(value);
            }

            overlay.querySelector('#api-key-save').addEventListener('click', save);
            overlay.querySelector('#api-key-cancel').addEventListener('click', () => {
                setKey('');
                close(null);
            });
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') save();
                if (e.key === 'Escape') { setKey(''); close(null); }
            });
        });
    }

    const nativeFetch = window.fetch.bind(window);

    window.fetch = async function apiKeyFetch(input, init) {
        const protectedReq = isProtectedRequest(input);
        const key = protectedReq ? getKey() : '';
        let res = await nativeFetch(input, key ? withKeyHeader(input, init, key) : init);

        if (res.status === 401 && protectedReq) {
            const entered = await requestKeyEntry();
            if (entered) {
                res = await nativeFetch(input, withKeyHeader(input, init, entered));
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
})();
