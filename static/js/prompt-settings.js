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

function getPromptSettings() {
    return {
        custom_system_message: document.getElementById('custom-system-message').value.slice(0, 5000),
        vlm_validation: false,
        validation_provider: 'auto',
        cost_estimate: document.getElementById('cost-estimate-toggle').checked,
        well_architected: document.getElementById('well-architected-toggle').checked,
        compliance: document.getElementById('compliance-toggle').checked,
        compliance_frameworks: getSelectedComplianceFrameworks(),
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
}
