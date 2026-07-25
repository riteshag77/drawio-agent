/** Prompt settings — diagram mode, cost/WAF/compliance toggles, custom system message. */

function getDiagramMode() {
    const select = document.getElementById('diagram-mode-select');
    const mode = select ? select.value : 'auto';
    if (mode === 'cloud' || mode === 'simple') return mode;
    return 'auto';
}

function saveDiagramMode() {
    localStorage.setItem(DIAGRAM_MODE_STORAGE_KEY, getDiagramMode());
}

function initDiagramMode() {
    const stored = localStorage.getItem(DIAGRAM_MODE_STORAGE_KEY) || 'auto';
    const select = document.getElementById('diagram-mode-select');
    if (select && ['auto', 'cloud', 'simple'].includes(stored)) {
        select.value = stored;
    }
}

function updateValidationProviderVisibility() {
    /* VLM validation UI removed */
}

function getSelectedDiagramViews() {
    const cbs = document.querySelectorAll('.diagram-view-cb');
    const selected = [];
    cbs.forEach(function (cb) {
        if (cb.checked) selected.push(cb.value);
    });
    if (!selected.length) return ['network'];
    const order = ['logical', 'network', 'security', 'data_flow'];
    return order.filter(function (v) { return selected.indexOf(v) !== -1; });
}

function onDiagramViewChange() {
    const cbs = document.querySelectorAll('.diagram-view-cb');
    let any = false;
    cbs.forEach(function (cb) { if (cb.checked) any = true; });
    if (!any) {
        const net = document.querySelector('.diagram-view-cb[value="network"]');
        if (net) net.checked = true;
        if (typeof showToast === 'function') {
            showToast('At least one diagram view is required — Network kept on', 'info');
        }
    }
    savePromptSettings();
}

function getPromptSettings() {
    return {
        custom_system_message: document.getElementById('custom-system-message').value.slice(0, 5000),
        vlm_validation: false,
        validation_provider: 'auto',
        cost_estimate: document.getElementById('cost-estimate-toggle').checked,
        well_architected: document.getElementById('well-architected-toggle').checked,
        compliance: document.getElementById('compliance-toggle').checked,
        compliance_frameworks: getSelectedComplianceFrameworks(),
        libavoid: document.getElementById('libavoid-toggle')
            ? document.getElementById('libavoid-toggle').checked
            : true,
        image_replicate: document.getElementById('image-replicate-toggle')
            ? document.getElementById('image-replicate-toggle').checked
            : false,
        animated_connectors: document.getElementById('animated-connectors-toggle')
            ? document.getElementById('animated-connectors-toggle').checked
            : false,
        show_reasoning: document.getElementById('show-reasoning-toggle')
            ? document.getElementById('show-reasoning-toggle').checked
            : false,
        suggestions: document.getElementById('suggestions-toggle')
            ? document.getElementById('suggestions-toggle').checked
            : false,
        architecture_interview: document.getElementById('architecture-interview-toggle')
            ? document.getElementById('architecture-interview-toggle').checked
            : false,
        diagram_views: getSelectedDiagramViews(),
        adr_notes: document.getElementById('adr-notes-toggle')
            ? document.getElementById('adr-notes-toggle').checked
            : false,
        raid_register: document.getElementById('raid-register-toggle')
            ? document.getElementById('raid-register-toggle').checked
            : false,
        save_captures: document.getElementById('save-captures-toggle')
            ? document.getElementById('save-captures-toggle').checked
            : false,
    };
}

function savePromptSettings() {
    const custom = document.getElementById('custom-system-message').value.slice(0, 5000);
    const cost = document.getElementById('cost-estimate-toggle').checked;
    const waf = document.getElementById('well-architected-toggle').checked;
    const comp = document.getElementById('compliance-toggle').checked;
    localStorage.setItem(CUSTOM_SYSTEM_MESSAGE_KEY, custom);
    localStorage.setItem(COST_ESTIMATE_STORAGE_KEY, cost ? '1' : '0');
    localStorage.setItem(WELL_ARCHITECTED_KEY, waf ? '1' : '0');
    localStorage.setItem(COMPLIANCE_KEY, comp ? '1' : '0');
    const lib = document.getElementById('libavoid-toggle');
    if (lib) localStorage.setItem(LIBAVOID_STORAGE_KEY, lib.checked ? '1' : '0');
    const img = document.getElementById('image-replicate-toggle');
    if (img) localStorage.setItem(IMAGE_REPLICATE_STORAGE_KEY, img.checked ? '1' : '0');
    const anim = document.getElementById('animated-connectors-toggle');
    if (anim) localStorage.setItem(ANIMATED_CONNECTORS_STORAGE_KEY, anim.checked ? '1' : '0');
    const reason = document.getElementById('show-reasoning-toggle');
    if (reason) localStorage.setItem(SHOW_REASONING_STORAGE_KEY, reason.checked ? '1' : '0');
    const sugg = document.getElementById('suggestions-toggle');
    if (sugg) localStorage.setItem(SUGGESTIONS_STORAGE_KEY, sugg.checked ? '1' : '0');
    const interview = document.getElementById('architecture-interview-toggle');
    if (interview) localStorage.setItem(ARCH_INTERVIEW_STORAGE_KEY, interview.checked ? '1' : '0');
    const adr = document.getElementById('adr-notes-toggle');
    if (adr) localStorage.setItem(ADR_NOTES_STORAGE_KEY, adr.checked ? '1' : '0');
    const raid = document.getElementById('raid-register-toggle');
    if (raid) localStorage.setItem(RAID_REGISTER_STORAGE_KEY, raid.checked ? '1' : '0');
    const saveCap = document.getElementById('save-captures-toggle');
    if (saveCap) localStorage.setItem(SAVE_CAPTURES_STORAGE_KEY, saveCap.checked ? '1' : '0');
    localStorage.setItem(DIAGRAM_VIEWS_STORAGE_KEY, JSON.stringify(getSelectedDiagramViews()));
    updateComplianceFrameworksVisibility();
}

function getSelectedComplianceFrameworks() {
    const cbs = document.querySelectorAll('.compliance-fw-cb');
    const selected = [];
    cbs.forEach(function (cb) {
        if (cb.checked) selected.push(cb.value);
    });
    return selected;
}

function updateComplianceFrameworksVisibility() {
    const row = document.getElementById('compliance-frameworks-row');
    if (row) row.hidden = !document.getElementById('compliance-toggle').checked;
}

function saveComplianceFrameworkSelection() {
    const cbs = document.querySelectorAll('.compliance-fw-cb');
    const selected = [];
    cbs.forEach(function (cb) {
        if (cb.checked) selected.push(cb.value);
    });
    localStorage.setItem(COMPLIANCE_FRAMEWORKS_KEY, JSON.stringify(selected));
}

function toggleCostPanelPreview() {
    var panel = document.getElementById('cost-panel');
    var body = document.getElementById('cost-panel-body');
    var total = document.getElementById('cost-total-amount');
    if (!panel || !body) return;
    if (document.getElementById('cost-estimate-toggle').checked) {
        panel.classList.add('active');
        body.innerHTML = '<div class="cost-empty">Generate a diagram to see cost estimates</div>';
        if (total) total.textContent = 'A$--';
        if (typeof switchInsightsTab === 'function') switchInsightsTab('cost');
    } else {
        panel.classList.remove('active');
        body.innerHTML = '';
        if (total) total.textContent = 'A$0';
    }
    updateRightPanelVisibility();
}

function initPromptSettings() {
    document.getElementById('custom-system-message').value =
        localStorage.getItem(CUSTOM_SYSTEM_MESSAGE_KEY) || '';
    const costStored = localStorage.getItem(COST_ESTIMATE_STORAGE_KEY);
    document.getElementById('cost-estimate-toggle').checked = costStored === '1';
    const wafStored = localStorage.getItem(WELL_ARCHITECTED_KEY);
    document.getElementById('well-architected-toggle').checked = wafStored === '1';
    const compStored = localStorage.getItem(COMPLIANCE_KEY);
    document.getElementById('compliance-toggle').checked = compStored === '1';
    const libEl = document.getElementById('libavoid-toggle');
    if (libEl) {
        const libStored = localStorage.getItem(LIBAVOID_STORAGE_KEY);
        libEl.checked = libStored === null ? true : libStored === '1';
    }
    const imgEl = document.getElementById('image-replicate-toggle');
    if (imgEl) imgEl.checked = localStorage.getItem(IMAGE_REPLICATE_STORAGE_KEY) === '1';
    const animEl = document.getElementById('animated-connectors-toggle');
    if (animEl) animEl.checked = localStorage.getItem(ANIMATED_CONNECTORS_STORAGE_KEY) === '1';
    const reasonEl = document.getElementById('show-reasoning-toggle');
    if (reasonEl) reasonEl.checked = localStorage.getItem(SHOW_REASONING_STORAGE_KEY) === '1';
    const suggEl = document.getElementById('suggestions-toggle');
    if (suggEl) suggEl.checked = localStorage.getItem(SUGGESTIONS_STORAGE_KEY) === '1';
    const interviewEl = document.getElementById('architecture-interview-toggle');
    if (interviewEl) interviewEl.checked = localStorage.getItem(ARCH_INTERVIEW_STORAGE_KEY) === '1';
    const adrEl = document.getElementById('adr-notes-toggle');
    if (adrEl) adrEl.checked = localStorage.getItem(ADR_NOTES_STORAGE_KEY) === '1';
    const raidEl = document.getElementById('raid-register-toggle');
    if (raidEl) raidEl.checked = localStorage.getItem(RAID_REGISTER_STORAGE_KEY) === '1';
    const saveCapEl = document.getElementById('save-captures-toggle');
    if (saveCapEl) saveCapEl.checked = localStorage.getItem(SAVE_CAPTURES_STORAGE_KEY) === '1';
    const viewsStored = localStorage.getItem(DIAGRAM_VIEWS_STORAGE_KEY);
    if (viewsStored) {
        try {
            const selected = JSON.parse(viewsStored);
            document.querySelectorAll('.diagram-view-cb').forEach(function (cb) {
                cb.checked = selected.indexOf(cb.value) !== -1;
            });
            if (!getSelectedDiagramViews().length) {
                const net = document.querySelector('.diagram-view-cb[value="network"]');
                if (net) net.checked = true;
            }
        } catch (_) {}
    }
    document.querySelectorAll('.diagram-view-cb').forEach(function (cb) {
        cb.addEventListener('change', onDiagramViewChange);
    });
    const fwStored = localStorage.getItem(COMPLIANCE_FRAMEWORKS_KEY);
    if (fwStored) {
        try {
            const selected = JSON.parse(fwStored);
            document.querySelectorAll('.compliance-fw-cb').forEach(function (cb) {
                cb.checked = selected.indexOf(cb.value) !== -1;
            });
        } catch (_) {}
    }
    document.querySelectorAll('.compliance-fw-cb').forEach(function (cb) {
        cb.addEventListener('change', saveComplianceFrameworkSelection);
    });
    updateValidationProviderVisibility();
    updateComplianceFrameworksVisibility();
    toggleCostPanelPreview();
    toggleWellArchitectedPanel();
    toggleCompliancePanel();
    toggleSuggestionsPanel();
}
