/** Shared provider/model <select> builder.
 *
 * Lives in its own file because both pages need it and they load disjoint
 * script sets: index.html builds the composer and Visual-QA pickers from it,
 * admin.html builds the validation picker. Before this was shared, admin
 * hand-rolled a flat one-option-per-*provider* list while every picker in the
 * app offered one option per *model* — so admin could choose a vendor but not
 * which of its models actually ran the validation pass.
 */

/** Populates any <select> with the exact same provider/model catalog the
 * main chat composer offers (server providers incl. multi-model optgroups,
 * plus custom BYOK providers) — so "pick a model to validate with" is never
 * a second, narrower list than "pick a model to chat with".
 *
 * @param {HTMLSelectElement} select
 * @param {{providers: Array, default?: string}} data  /providers-shaped payload.
 *        A provider may also carry `configured: false`, which labels it as
 *        having no API key rather than hiding it — the choice is still valid,
 *        it just cannot run until a key is set.
 * @param {object} [opts]
 * @param {boolean} [opts.includeAuto]   prepend an "auto" option.
 * @param {string}  [opts.autoLabel]     its label, e.g. "Same as chat".
 * @param {string}  [opts.autoValue]     its value; '' where blank is the
 *        meaningful "inherit" value, 'auto' where it is a sentinel.
 * @param {boolean} [opts.includeCustom] include BYOK providers (default true).
 *        Server-side consumers pass false: custom providers live in this
 *        browser's localStorage, so a setting naming one could never be
 *        honoured by the server that has to run it.
 * @param {boolean} [opts.qualifyModelLabels] prefix each grouped model with its
 *        provider ("NVIDIA NIM — z-ai/glm-5.2"). Off by default because a
 *        grouped popover already prints the provider as a header, and
 *        repeating it on every row is noise. On for a *plain* <select>, whose
 *        closed state shows only the chosen option — without this it reads as
 *        a bare model id with nothing saying whose it is.
 */
function populateProviderOptions(select, data, {
    includeAuto = false,
    autoLabel = 'Auto',
    autoValue = 'auto',
    includeCustom = true,
    qualifyModelLabels = false,
} = {}) {
    // The static HTML placeholder is a single hardcoded <option> — only treat
    // the current value as a real prior selection worth preserving once the
    // select has actually been populated from /providers at least once.
    const hadRealOptions = select.options.length > 1;
    const previousValue = select.value;
    select.innerHTML = '';
    if (includeAuto) {
        const autoOpt = document.createElement('option');
        autoOpt.value = autoValue;
        autoOpt.textContent = autoLabel;
        select.appendChild(autoOpt);
    }
    let defaultValue = data.default || '';
    for (const p of (data.providers || [])) {
        // Only meaningful for payloads that report it (the admin one does);
        // /providers omits `configured`, and an absent flag is not "unconfigured".
        const suffix = p.configured === false ? ' — no API key set' : '';
        if (p.models && p.models.length > 1) {
            // Multi-model provider (e.g. NVIDIA NIM) — one grouped option per
            // model, "provider header + model list" style.
            const group = document.createElement('optgroup');
            group.label = p.label + suffix;
            for (const modelId of p.models) {
                const opt = document.createElement('option');
                opt.value = p.key + '::' + modelId;
                opt.textContent = (qualifyModelLabels ? `${p.label} — ${modelId}` : modelId) + suffix;
                group.appendChild(opt);
            }
            select.appendChild(group);
            if (p.key === data.default) defaultValue = p.key + '::' + p.models[0];
        } else {
            const opt = document.createElement('option');
            opt.value = p.key;
            opt.textContent = p.label + suffix;
            select.appendChild(opt);
        }
    }
    const customProviders = (includeCustom && typeof getCustomProviders === 'function')
        ? getCustomProviders()
        : [];
    if (customProviders.length) {
        const group = document.createElement('optgroup');
        group.label = 'Custom';
        for (const p of customProviders) {
            const models = (p.models && p.models.length) ? p.models : [''];
            for (const modelId of models) {
                const opt = document.createElement('option');
                opt.value = 'custom:' + p.id + '::' + modelId;
                opt.textContent = models.length > 1 ? `${p.label} — ${modelId}` : p.label;
                group.appendChild(opt);
            }
        }
        select.appendChild(group);
    }
    if (defaultValue) {
        const defaultOpt = select.querySelector(`option[value="${CSS.escape(defaultValue)}"]`);
        if (defaultOpt) defaultOpt.setAttribute('data-default', '');
    }
    if (hadRealOptions && previousValue && select.querySelector(`option[value="${CSS.escape(previousValue)}"]`)) {
        select.value = previousValue;
    } else if (includeAuto) {
        select.value = autoValue;
    } else if (defaultValue) {
        select.value = defaultValue;
    }
}
