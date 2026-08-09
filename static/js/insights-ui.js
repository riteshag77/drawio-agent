/** Insights tab switching + mobile pane chrome + diagram version strip. */

let sessionVersions = []; // ascending by .version — oldest first, matches "restore version N" numbering
let activeVersionId = null;

function switchInsightsTab(tab) {
    // Back-compat: older call sites used a single "network" tab.
    if (tab === 'network') tab = 'cost';
    document.querySelectorAll('.insights-tab').forEach((btn) => {
        const on = btn.dataset.tab === tab;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.insights-panel').forEach((panel) => {
        const on = panel.dataset.panel === tab;
        panel.classList.toggle('active', on);
        if (on) panel.removeAttribute('hidden');
        else panel.setAttribute('hidden', '');
    });
    if (tab === 'logs') {
        const verbose = document.getElementById('verbose-toggle');
        if (verbose && !verbose.checked) {
            verbose.checked = true;
            if (typeof toggleVerbosePanel === 'function') toggleVerbosePanel();
        }
    }
}

/** Shows/hides the small spinner on a tab button while its own /analyze
 * re-check (rerunCostEstimateCheck/rerunWafComplianceCheck/
 * rerunSuggestionsCheck) is in flight — visible even if the user is looking
 * at a different tab while it loads. */
function setTabLoading(tab, isLoading) {
    const btn = document.querySelector('.insights-tab[data-tab="' + tab + '"]');
    if (btn) btn.classList.toggle('loading', !!isLoading);
}

/** Suggestions / Logs tab buttons only make sense when their own toggle is
 * on — Cost and WAF/Compliance stay always-visible (they're gated by the
 * Network-view checkbox already, via updateNetworkInsightsVisibility). If
 * the currently active tab is one that just got hidden, fall back to Cost
 * (always visible) instead of leaving an invisible tab selected. */
function updateInsightsTabVisibility() {
    var suggTab = document.querySelector('.insights-tab[data-tab="suggestions"]');
    var logsTab = document.querySelector('.insights-tab[data-tab="logs"]');
    var suggOn = !!document.getElementById('suggestions-toggle')?.checked;
    var logsOn = !!document.getElementById('verbose-toggle')?.checked;
    if (suggTab) suggTab.hidden = !suggOn;
    if (logsTab) logsTab.hidden = !logsOn;

    var activeTab = document.querySelector('.insights-tab.active');
    var activeHidden = activeTab && (
        (activeTab.dataset.tab === 'suggestions' && !suggOn)
        || (activeTab.dataset.tab === 'logs' && !logsOn)
    );
    if (activeHidden && typeof switchInsightsTab === 'function') {
        switchInsightsTab('cost');
    }
}

function switchMobilePane(pane) {
    document.body.dataset.mobilePane = pane;
    document.querySelectorAll('.mobile-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.mobile === pane);
    });
    localStorage.setItem(MOBILE_PANE_KEY, pane);
}

/** Jump straight to a specific insights sub-tab on mobile, saving the extra
 * tap of opening Insights then picking a sub-tab. No-op on desktop widths. */
function focusMobileInsightsTab(tab) {
    if (window.innerWidth >= 900) return;
    switchMobilePane('insights');
    if (typeof switchInsightsTab === 'function') switchInsightsTab(tab);
}

/** Re-fetches this session's recent versions from the backend and re-renders
 * the strip. The server already wrote the version during the /chat request
 * that produced it, so this is purely a read — no client-side "push" step. */
async function refreshVersionStrip() {
    if (typeof currentSessionId === 'undefined' || !currentSessionId) {
        renderVersionStrip();
        return;
    }
    try {
        const res = await fetch(`/history/sessions/${currentSessionId}?limit=8`);
        if (!res.ok) return;
        const data = await res.json();
        sessionVersions = (data.versions || []).slice().sort((a, b) => a.version - b.version);
        if (!activeVersionId && sessionVersions.length) {
            activeVersionId = sessionVersions[sessionVersions.length - 1].id;
        }
    } catch (_) {
        return;
    }
    renderVersionStrip();
}

async function restoreDiagramVersion(versionId) {
    let res;
    try {
        res = await fetch(`/history/versions/${versionId}`);
    } catch (_) {
        showToast('Could not load that version.', 'error');
        return;
    }
    if (!res.ok) {
        showToast('Could not load that version.', 'error');
        return;
    }
    const v = await res.json();
    activeVersionId = versionId;
    const isMermaid = v.meta && v.meta.diagram_type === 'mermaid';
    if (isMermaid) {
        currentMermaid = v.xml;
        currentXml = '';
        document.getElementById('xml-output').textContent = v.xml;
        if (typeof loadMermaidIntoEditor === 'function') loadMermaidIntoEditor(v.xml);
    } else {
        currentXml = v.xml;
        currentMermaid = '';
        document.getElementById('xml-output').textContent = v.xml;
        if (typeof loadDiagramIntoEditor === 'function') loadDiagramIntoEditor(v.xml);
    }
    if (Array.isArray(v.messages) && v.messages.length && typeof _restoreChatTranscript === 'function') {
        _restoreChatTranscript(v.messages);
    }
    renderVersionStrip();
    showToast(`Restored v${v.version}`, 'success');
    if (!isMermaid) reanalyzeRestoredVersion(v.xml);
}

/** Restoring a version only swaps the canvas XML — without this, cost/WAF/
 * compliance/suggestions panels would keep showing results for whichever
 * version was on screen before the restore. Reuses /analyze (the same
 * re-check endpoint the Cost/WAF/Compliance/Suggestions toggles already use
 * when turned on after a diagram exists) instead of a full regenerate. */
async function reanalyzeRestoredVersion(xml) {
    if (typeof getPromptSettings !== 'function') return;
    const promptSettings = getPromptSettings();
    if (!(promptSettings.cost_estimate || promptSettings.well_architected || promptSettings.compliance || promptSettings.suggestions)) {
        return;
    }
    const providerSelect = document.getElementById('provider-select');
    const provider = providerSelect && typeof parseProviderSelectValue === 'function'
        ? parseProviderSelectValue(providerSelect.value).provider
        : 'claude';
    try {
        const res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                xml,
                platform: typeof selectedPlatform !== 'undefined' ? selectedPlatform : 'aws',
                provider,
                cost_estimate: promptSettings.cost_estimate,
                well_architected: promptSettings.well_architected,
                compliance: promptSettings.compliance,
                compliance_frameworks: promptSettings.compliance_frameworks,
                suggestions: promptSettings.suggestions,
                applied_suggestions: typeof getAppliedSuggestionsPayload === 'function' ? getAppliedSuggestionsPayload() : [],
            }),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.cost_estimate && typeof displayCostEstimate === 'function') displayCostEstimate(data.cost_estimate);
        if (data.well_architected && typeof displayWellArchitected === 'function') displayWellArchitected(data.well_architected);
        if (data.compliance && typeof displayCompliance === 'function') displayCompliance(data.compliance);
        if (data.suggestions && typeof displaySuggestions === 'function') displaySuggestions(data.suggestions);
        if (typeof updateRightPanelVisibility === 'function') updateRightPanelVisibility();
    } catch (_) {
        // Best-effort re-analysis — a restored diagram is still usable if this fails.
    }
}

function renderVersionStrip() {
    const strip = document.getElementById('version-strip');
    if (!strip) return;
    if (!sessionVersions.length) {
        strip.hidden = true;
        strip.innerHTML = '';
        return;
    }
    strip.hidden = false;
    strip.innerHTML = sessionVersions.map((v) => {
        const active = v.id === activeVersionId ? ' active' : '';
        const label = `v${v.version}`;
        return `<button type="button" class="version-chip${active}" onclick="restoreDiagramVersion('${v.id}')" title="${(v.title || v.prompt || label).replace(/"/g, '&quot;')}">${label}</button>`;
    }).join('') + `<button type="button" class="version-chip version-chip-more" onclick="openHistoryDialog()" title="Full history">…</button>`;
}

function updateContextChip() {
    const chip = document.getElementById('context-chip');
    if (!chip) return;
    const provider = document.getElementById('provider-select')?.value || 'ai';
    const mode = typeof getDiagramMode === 'function' ? getDiagramMode() : 'auto';
    const platform = typeof selectedPlatform !== 'undefined' ? selectedPlatform : 'aws';
    chip.textContent = `${platform.toUpperCase()} · ${provider} · ${mode}`;
}

document.addEventListener('DOMContentLoaded', () => {
    switchInsightsTab('cost');
    switchMobilePane(localStorage.getItem(MOBILE_PANE_KEY) || 'chat');
    updateContextChip();
    const provider = document.getElementById('provider-select');
    if (provider) provider.addEventListener('change', updateContextChip);
});
