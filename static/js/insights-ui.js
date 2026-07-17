/** Insights tab switching + mobile pane chrome + diagram version strip. */

const DIAGRAM_VERSIONS_MAX = 8;
let diagramVersions = [];
let activeDiagramVersion = -1;

function switchInsightsTab(tab) {
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
    const logPanel = document.getElementById('log-panel');
    if (logPanel) logPanel.classList.add('open');
}

function switchMobilePane(pane) {
    document.body.dataset.mobilePane = pane;
    document.querySelectorAll('.mobile-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.mobile === pane);
    });
}

function pushDiagramVersion(xml, label) {
    if (!xml || xml.length < 50) return;
    diagramVersions.push({
        xml,
        label: label || `v${diagramVersions.length + 1}`,
        at: Date.now(),
    });
    if (diagramVersions.length > DIAGRAM_VERSIONS_MAX) {
        diagramVersions = diagramVersions.slice(-DIAGRAM_VERSIONS_MAX);
    }
    activeDiagramVersion = diagramVersions.length - 1;
    renderVersionStrip();
}

function restoreDiagramVersion(index) {
    const entry = diagramVersions[index];
    if (!entry) return;
    activeDiagramVersion = index;
    currentXml = entry.xml;
    document.getElementById('xml-output').textContent = entry.xml;
    if (typeof loadDiagramIntoEditor === 'function') {
        loadDiagramIntoEditor(entry.xml);
    } else if (typeof ensureDrawioFrame === 'function') {
        // fallback: postMessage load via existing helpers if present
        const frame = document.getElementById('drawio-frame');
        if (frame?.contentWindow) {
            frame.contentWindow.postMessage(JSON.stringify({
                action: 'load',
                xml: entry.xml,
                autosave: 1,
            }), '*');
        }
    }
    renderVersionStrip();
    showToast(`Restored ${entry.label}`, 'success');
}

function renderVersionStrip() {
    const strip = document.getElementById('version-strip');
    if (!strip) return;
    if (!diagramVersions.length) {
        strip.hidden = true;
        strip.innerHTML = '';
        return;
    }
    strip.hidden = false;
    strip.innerHTML = diagramVersions.map((v, i) => {
        const active = i === activeDiagramVersion ? ' active' : '';
        return `<button type="button" class="version-chip${active}" onclick="restoreDiagramVersion(${i})">${v.label}</button>`;
    }).join('');
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
    switchMobilePane('chat');
    updateContextChip();
    const provider = document.getElementById('provider-select');
    if (provider) provider.addEventListener('change', updateContextChip);
});
