/** Insights panels — cost, Well-Architected, compliance, token usage display. */

// ── Cost Panel ────────────────────────────────────────────

function displayCostEstimate(costData) {
    if (typeof switchInsightsTab === 'function') switchInsightsTab('cost');
    if (!costData) return;
    const panel = document.getElementById('cost-panel');
    const body = document.getElementById('cost-panel-body');
    const totalEl = document.getElementById('cost-total-amount');
    if (!panel || !body || !totalEl) return;
    panel.classList.add('active');
    var grandTotal = costData.total || 0;
    totalEl.textContent = 'A$' + grandTotal.toFixed(0);
    body.innerHTML = '';
    var categories = costData.breakdown || {};
    var catKeys = Object.keys(categories);
    if (catKeys.length === 0) {
        body.innerHTML = '<div class="cost-empty">No billable services detected</div>';
        return;
    }
    var catTotals = {};
    catKeys.forEach(function (cat) {
        var svcs = categories[cat] || [];
        var sub = 0;
        svcs.forEach(function (s) { sub += s.monthly || 0; });
        catTotals[cat] = sub;
    });
    catKeys.forEach(function (cat) {
        var svcs = categories[cat] || [];
        var subTotal = catTotals[cat] || 0;
        var pct = grandTotal > 0 ? (subTotal / grandTotal * 100) : 0;
        var card = document.createElement('div');
        card.className = 'cost-category-card';
        var head = document.createElement('div');
        head.className = 'cost-category-head';
        var name = document.createElement('span');
        name.className = 'cost-category-name';
        name.textContent = cat;
        var subEl = document.createElement('span');
        subEl.className = 'cost-category-subtotal';
        subEl.textContent = 'A$' + subTotal.toFixed(0);
        head.appendChild(name);
        head.appendChild(subEl);
        card.appendChild(head);
        var bar = document.createElement('div');
        bar.className = 'cost-category-bar';
        var fill = document.createElement('div');
        fill.className = 'cost-category-bar-fill';
        fill.style.width = Math.max(pct, 2) + '%';
        bar.appendChild(fill);
        card.appendChild(bar);
        svcs.forEach(function (svc) {
            var row = document.createElement('div');
            row.className = 'cost-service-row';
            var nameSpan = document.createElement('span');
            nameSpan.className = 'cost-service-name';
            nameSpan.textContent = svc.name || svc.key;
            var priceSpan = document.createElement('span');
            priceSpan.className = 'cost-service-price';
            priceSpan.textContent = 'A$' + (svc.monthly || 0).toFixed(0);
            row.appendChild(nameSpan);
            row.appendChild(priceSpan);
            card.appendChild(row);
        });
        body.appendChild(card);
    });
    if (costData.services_unmatched > 0) {
        var note = document.createElement('div');
        note.className = 'cost-unmatched-note';
        note.textContent = costData.services_unmatched + ' service(s) without exact pricing — estimated at default cost';
        body.appendChild(note);
    }
}

function displayTokenUsage(usage) {
    var badge = document.getElementById('token-badge');
    if (!badge) return;
    if (!usage || !usage.total_tokens) {
        badge.hidden = true;
        badge.textContent = '';
        return;
    }
    var tokens = usage.total_tokens;
    var tokenText = tokens >= 1000 ? (tokens / 1000).toFixed(1) + 'K tokens' : tokens + ' tokens';
    var text = '~' + tokenText;
    if (typeof usage.estimated_cost_usd === 'number') {
        text += ' · ~$' + usage.estimated_cost_usd.toFixed(usage.estimated_cost_usd < 0.01 ? 4 : 2);
    }
    badge.textContent = text;
    badge.hidden = false;
    badge.title = (usage.calls || []).map(function (c) {
        return (c.model || 'unknown model') + ': ' + (c.total_tokens || 0) + ' tokens'
            + (typeof c.estimated_cost_usd === 'number' ? ' (~$' + c.estimated_cost_usd.toFixed(4) + ')' : '');
    }).join('\n') || 'Estimated LLM tokens/cost for the last generation';
}

function hideTokenUsage() {
    var badge = document.getElementById('token-badge');
    if (badge) { badge.hidden = true; badge.textContent = ''; }
}

function hideCostEstimate() {
    var panel = document.getElementById('cost-panel');
    if (panel) {
        panel.classList.remove('active');
        var toggle = document.getElementById('cost-estimate-toggle');
        if (toggle) toggle.checked = false;
        localStorage.setItem(COST_ESTIMATE_STORAGE_KEY, '0');
        updateRightPanelVisibility();
    }
}

// ── Well-Architected Panel ────────────────────────────────

function toggleWellArchitectedPanel() {
    var panel = document.getElementById('well-architected-panel');
    var body = document.getElementById('waf-panel-body');
    var badge = document.getElementById('waf-overall-badge');
    if (!panel) return;
    if (document.getElementById('well-architected-toggle').checked) {
        panel.classList.add('active');
        if (body) body.innerHTML = '<div class="waf-empty">Generate a diagram to see Well-Architected review</div>';
        if (badge) { badge.textContent = '--'; badge.className = 'waf-overall-badge'; }
        if (typeof switchInsightsTab === 'function') switchInsightsTab('waf');
    } else {
        panel.classList.remove('active');
        if (body) body.innerHTML = '';
        if (badge) { badge.textContent = '--'; badge.className = 'waf-overall-badge'; }
    }
    updateRightPanelVisibility();
}

function hideWellArchitectedPanel() {
    var panel = document.getElementById('well-architected-panel');
    if (panel) {
        panel.classList.remove('active');
        document.getElementById('well-architected-toggle').checked = false;
        localStorage.setItem(WELL_ARCHITECTED_KEY, '0');
        updateRightPanelVisibility();
    }
}

var appliedInsightKeys = new Set();

function isInsightApplied(key) {
    return appliedInsightKeys.has(key);
}

function applyInsightFixes(items) {
    if (!items || items.length === 0) return;
    var text = 'Please update the diagram to address the following:\n' + items.map(function (it, i) {
        var line = (i + 1) + '. ' + it.message;
        if (it.recommendation) line += ' — ' + it.recommendation;
        return line;
    }).join('\n');
    items.forEach(function (it) { appliedInsightKeys.add(it.key); });
    var input = document.getElementById('chat-input');
    if (input) {
        input.value = text;
        if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
    }
    sendMessage();
}

function displayWellArchitected(wafData) {
    if (typeof switchInsightsTab === 'function') switchInsightsTab('waf');
    if (!wafData) return;
    console.log('WAF data received', wafData.overall_status, wafData.pillars?.length, 'pillars');
    var panel = document.getElementById('well-architected-panel');
    var body = document.getElementById('waf-panel-body');
    var badge = document.getElementById('waf-overall-badge');
    if (!panel || !body || !badge) { console.warn('WAF panel elements missing'); return; }
    panel.classList.add('active');
    var toggle = document.getElementById('well-architected-toggle');
    if (toggle) toggle.checked = true;
    localStorage.setItem(WELL_ARCHITECTED_KEY, '1');
    var status = wafData.overall_status || 'pass';
    badge.textContent = status.toUpperCase();
    badge.className = 'waf-overall-badge ' + status;
    body.innerHTML = '';
    appliedInsightKeys = new Set();
    var pillars = wafData.pillars || [];
    if (pillars.length === 0) {
        body.innerHTML = '<div class="waf-empty">No recommendations available</div>';
        return;
    }

    var registry = [];
    var headerRow = document.createElement('div');
    headerRow.className = 'chat-suggestions-header';
    var headerTitle = document.createElement('div');
    headerTitle.className = 'chat-suggestions-title';
    headerTitle.textContent = 'Findings with recommendations';
    headerRow.appendChild(headerTitle);
    var selectAllLabel = document.createElement('label');
    selectAllLabel.className = 'chat-suggestions-select-all';
    var selectAllCb = document.createElement('input');
    selectAllCb.type = 'checkbox';
    selectAllCb.className = 'suggestion-checkbox suggestion-select-all';
    var selectAllText = document.createElement('span');
    selectAllText.textContent = 'Select all';
    selectAllLabel.appendChild(selectAllCb);
    selectAllLabel.appendChild(selectAllText);
    headerRow.appendChild(selectAllLabel);
    body.appendChild(headerRow);

    pillars.forEach(function (p, pillarIdx) {
        var div = document.createElement('div');
        div.className = 'waf-pillar';
        var header = document.createElement('div');
        header.className = 'waf-pillar-header';
        var icon = document.createElement('span');
        icon.className = 'waf-pillar-status ' + (p.status || 'pass');
        icon.innerHTML = p.status === 'pass'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>'
            : p.status === 'warn'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
        header.appendChild(icon);
        var name = document.createElement('span');
        name.className = 'waf-pillar-name';
        name.textContent = p.name || '';
        header.appendChild(name);
        div.appendChild(header);
        if (p.description) {
            var desc = document.createElement('div');
            desc.className = 'waf-pillar-desc';
            desc.textContent = p.description;
            div.appendChild(desc);
        }
        (p.findings || []).forEach(function (f, findingIdx) {
            var finding = document.createElement('div');
            finding.className = 'waf-finding';
            var dot = document.createElement('span');
            dot.className = 'waf-finding-dot ' + (f.severity || 'low');
            finding.appendChild(dot);
            var text = document.createElement('div');
            text.className = 'waf-finding-text';
            text.textContent = f.message || '';
            if (f.recommendation) {
                var rec = document.createElement('span');
                rec.className = 'waf-finding-recommendation';
                rec.textContent = '→ ' + f.recommendation;
                text.appendChild(rec);
                var key = 'waf:' + pillarIdx + ':' + findingIdx;
                var applied = isInsightApplied(key);
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'suggestion-checkbox item-checkbox';
                cb.disabled = applied;
                cb.checked = applied;
                cb.addEventListener('change', function () {
                    var pending = registry.filter(function (r) { return !r.checkbox.disabled; });
                    var checked = pending.filter(function (r) { return r.checkbox.checked; });
                    selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
                    selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
                });
                finding.insertBefore(cb, dot);
                var badge = document.createElement('span');
                badge.className = 'suggestion-applied-badge';
                badge.textContent = 'Applied';
                badge.hidden = !applied;
                text.appendChild(badge);
                registry.push({ key: key, message: f.message || '', recommendation: f.recommendation, checkbox: cb, badge: badge });
            }
            finding.appendChild(text);
            div.appendChild(finding);
        });
        body.appendChild(div);
    });

    function markApplied(items) {
        var keys = {};
        items.forEach(function (it) { keys[it.key] = true; });
        registry.forEach(function (r) {
            if (!keys[r.key]) return;
            r.checkbox.checked = false;
            r.checkbox.disabled = true;
            r.badge.hidden = false;
        });
        var pending = registry.filter(function (r) { return !r.checkbox.disabled; });
        selectAllCb.checked = false;
        selectAllCb.indeterminate = false;
        selectAllCb.disabled = pending.length === 0;
        actionRow.hidden = pending.length === 0;
    }

    selectAllCb.addEventListener('change', function () {
        registry.forEach(function (r) {
            if (!r.checkbox.disabled) r.checkbox.checked = selectAllCb.checked;
        });
        selectAllCb.indeterminate = false;
    });

    var actionRow = document.createElement('div');
    actionRow.className = 'suggestion-actions';
    var selBtn = document.createElement('button');
    selBtn.type = 'button';
    selBtn.className = 'suggestion-btn';
    selBtn.textContent = 'Apply Selected';
    selBtn.onclick = function () {
        var items = registry.filter(function (r) { return !r.checkbox.disabled && r.checkbox.checked; });
        if (items.length === 0) { if (typeof showError === 'function') showError('Select at least one finding to apply.'); return; }
        markApplied(items);
        applyInsightFixes(items);
    };
    var allBtn = document.createElement('button');
    allBtn.type = 'button';
    allBtn.className = 'suggestion-btn suggestion-btn-primary';
    allBtn.textContent = 'Apply All';
    allBtn.onclick = function () {
        var items = registry.filter(function (r) { return !r.checkbox.disabled; });
        if (items.length === 0) { if (typeof showError === 'function') showError('All findings are already applied.'); return; }
        markApplied(items);
        applyInsightFixes(items);
    };
    actionRow.appendChild(selBtn);
    actionRow.appendChild(allBtn);
    body.appendChild(actionRow);
    if (registry.length === 0) {
        headerRow.hidden = true;
        actionRow.hidden = true;
    }

    var summary = wafData.summary || '';
    if (summary) {
        var s = document.createElement('div');
        s.style.cssText = 'margin-top:0.5rem;font-size:0.7rem;color:var(--text-muted);line-height:1.4';
        s.textContent = summary;
        body.appendChild(s);
    }
    updateRightPanelVisibility();
}

// ── Compliance Panel ──────────────────────────────────────

function toggleCompliancePanel() {
    var panel = document.getElementById('compliance-panel');
    var body = document.getElementById('compliance-body');
    if (!panel) return;
    if (document.getElementById('compliance-toggle').checked) {
        panel.classList.add('active');
        if (body) body.innerHTML = '<div class="compliance-empty">Generate a diagram to see compliance assessment results</div>';
        if (typeof switchInsightsTab === 'function') switchInsightsTab('compliance');
    } else {
        panel.classList.remove('active');
        if (body) body.innerHTML = '';
    }
    updateRightPanelVisibility();
}

function hideCompliancePanel() {
    var panel = document.getElementById('compliance-panel');
    if (panel) {
        panel.classList.remove('active');
        document.getElementById('compliance-toggle').checked = false;
        localStorage.setItem(COMPLIANCE_KEY, '0');
        updateRightPanelVisibility();
    }
}

function displayCompliance(compData) {
    if (typeof switchInsightsTab === 'function') switchInsightsTab('compliance');
    if (!compData || !compData.frameworks) return;
    var panel = document.getElementById('compliance-panel');
    var tabs = document.getElementById('compliance-tabs');
    var body = document.getElementById('compliance-body');
    if (!panel || !tabs || !body) return;
    panel.classList.add('active');
    var toggle = document.getElementById('compliance-toggle');
    if (toggle) toggle.checked = true;
    localStorage.setItem(COMPLIANCE_KEY, '1');
    appliedInsightKeys = new Set();

    var frameworks = compData.frameworks;
    var fwIds = Object.keys(frameworks);
    if (fwIds.length === 0) {
        body.innerHTML = '<div class="compliance-empty">No compliance frameworks evaluated</div>';
        return;
    }

    tabs.innerHTML = '';
    fwIds.forEach(function (fwId, idx) {
        var fw = frameworks[fwId];
        var tab = document.createElement('button');
        tab.className = 'compliance-tab' + (idx === 0 ? ' active' : '');
        tab.setAttribute('data-framework', fwId);
        var badge = document.createElement('span');
        badge.className = 'compliance-tab-badge ' + (fw.overall_status || 'pass');
        badge.textContent = (fw.overall_status || 'pass').toUpperCase();
        tab.appendChild(document.createTextNode(fw.name || fwId));
        tab.appendChild(badge);
        tab.addEventListener('click', function () {
            switchComplianceTab(fwId);
        });
        tabs.appendChild(tab);
    });

    switchComplianceTab(fwIds[0]);
    updateRightPanelVisibility();
}

function switchComplianceTab(fwId) {
    var body = document.getElementById('compliance-body');
    var tabs = document.getElementById('compliance-tabs');
    if (!body || !tabs) return;

    var compData = window._lastComplianceData;
    if (!compData || !compData.frameworks) return;
    var fw = compData.frameworks[fwId];
    if (!fw) return;

    tabs.querySelectorAll('.compliance-tab').forEach(function (t) {
        t.classList.toggle('active', t.getAttribute('data-framework') === fwId);
    });

    var inScope = fw.services_in_scope || [];
    var outScope = fw.services_out_of_scope || [];

    var html = '';
    html += '<div class="compliance-fw-summary">';
    html += '<span class="compliance-fw-status ' + (fw.overall_status || 'pass') + '">' + (fw.overall_status || '').toUpperCase() + '</span>';
    html += '<span class="compliance-fw-summary-text">' + (fw.summary || '') + '</span>';
    html += '</div>';

    var controls = fw.controls || [];
    var hasFindingsWithRec = controls.some(function (ctrl) {
        return (ctrl.findings || []).some(function (f) { return !!f.recommendation; });
    });
    if (hasFindingsWithRec) {
        html += '<div class="chat-suggestions-header">';
        html += '<div class="chat-suggestions-title">Findings with recommendations</div>';
        html += '<label class="chat-suggestions-select-all"><input type="checkbox" class="suggestion-checkbox suggestion-select-all"><span>Select all</span></label>';
        html += '</div>';
    }
    if (controls.length === 0) {
        html += '<div class="compliance-empty">No applicable controls for this diagram</div>';
    } else {
        controls.forEach(function (ctrl, ctrlIdx) {
            var statusClass = ctrl.status || 'pass';
            var icon = statusClass === 'pass' ? '&#x2713;' : statusClass === 'warn' ? '&#x26A0;' : '&#x2717;';
            html += '<div class="compliance-control-card ' + statusClass + '">';
            html += '<div class="compliance-control-header">';
            html += '<span class="compliance-control-status compliance-status-' + statusClass + '">' + icon + '</span>';
            html += '<span class="compliance-control-name">' + escapeHtml(ctrl.name || ctrl.id || '') + '</span>';
            html += '</div>';
            (ctrl.findings || []).forEach(function (f, findingIdx) {
                var sev = f.severity || 'low';
                var sevIcon = sev === 'high' ? '&#x2717;' : sev === 'medium' ? '&#x26A0;' : '&#x2713;';
                html += '<div class="compliance-finding ' + sev + '">';
                if (f.recommendation) {
                    var applied = isInsightApplied('compliance:' + fwId + ':' + ctrlIdx + ':' + findingIdx);
                    html += '<input type="checkbox" class="suggestion-checkbox item-checkbox" data-ctrl-idx="' + ctrlIdx
                        + '" data-finding-idx="' + findingIdx + '"' + (applied ? ' checked disabled' : '') + '>';
                }
                html += '<span class="compliance-finding-dot compliance-severity-' + sev + '">' + sevIcon + '</span>';
                html += '<div class="compliance-finding-text">';
                html += '<span>' + escapeHtml(f.message || '') + '</span>';
                if (f.affected_services && f.affected_services.length > 0) {
                    html += '<span class="compliance-finding-services">Affects: ' + f.affected_services.join(', ') + '</span>';
                }
                if (f.recommendation) {
                    html += '<span class="compliance-finding-rec">&#x2192; ' + escapeHtml(f.recommendation) + '</span>';
                    html += '<span class="suggestion-applied-badge"' + (isInsightApplied('compliance:' + fwId + ':' + ctrlIdx + ':' + findingIdx) ? '' : ' hidden') + '>Applied</span>';
                }
                html += '</div>';
                html += '</div>';
            });
            html += '</div>';
        });
    }
    if (hasFindingsWithRec) {
        html += '<div class="suggestion-actions">';
        html += '<button type="button" class="suggestion-btn compliance-apply-selected">Apply Selected</button>';
        html += '<button type="button" class="suggestion-btn suggestion-btn-primary compliance-apply-all">Apply All</button>';
        html += '</div>';
    }

    if (inScope.length > 0 || outScope.length > 0) {
        html += '<div class="compliance-scope-section">';
        html += '<div class="compliance-scope-title">Service Scope</div>';
        if (inScope.length > 0) {
            html += '<div class="compliance-scope-list"><span class="scope-label-in">In scope:</span> ' + inScope.join(', ') + '</div>';
        }
        if (outScope.length > 0) {
            html += '<div class="compliance-scope-list"><span class="scope-label-out">Out of scope:</span> ' + outScope.join(', ') + '</div>';
        }
        html += '</div>';
    }

    if (fw.llm_findings && fw.llm_findings.length > 0) {
        html += '<div class="compliance-llm-note">Enhanced with LLM analysis</div>';
    }

    body.innerHTML = html;

    var registry = [];
    body.querySelectorAll('.item-checkbox[data-ctrl-idx]').forEach(function (cb) {
        var ctrlIdx = parseInt(cb.getAttribute('data-ctrl-idx'), 10);
        var findingIdx = parseInt(cb.getAttribute('data-finding-idx'), 10);
        var finding = (controls[ctrlIdx] && controls[ctrlIdx].findings || [])[findingIdx];
        if (!finding) return;
        var badge = cb.parentElement.querySelector('.suggestion-applied-badge');
        var key = 'compliance:' + fwId + ':' + ctrlIdx + ':' + findingIdx;
        registry.push({ key: key, message: finding.message || '', recommendation: finding.recommendation, checkbox: cb, badge: badge });
        cb.addEventListener('change', function () {
            var pending = registry.filter(function (r) { return !r.checkbox.disabled; });
            var checked = pending.filter(function (r) { return r.checkbox.checked; });
            if (selectAllCb) {
                selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
                selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
            }
        });
    });

    var selectAllCb = body.querySelector('.suggestion-select-all');
    var actionRow = body.querySelector('.suggestion-actions');

    function markApplied(items) {
        var keys = {};
        items.forEach(function (it) { keys[it.key] = true; });
        registry.forEach(function (r) {
            if (!keys[r.key]) return;
            r.checkbox.checked = false;
            r.checkbox.disabled = true;
            if (r.badge) r.badge.hidden = false;
        });
        var pending = registry.filter(function (r) { return !r.checkbox.disabled; });
        if (selectAllCb) {
            selectAllCb.checked = false;
            selectAllCb.indeterminate = false;
            selectAllCb.disabled = pending.length === 0;
        }
        if (actionRow) actionRow.hidden = pending.length === 0;
    }

    if (selectAllCb) {
        selectAllCb.addEventListener('change', function () {
            registry.forEach(function (r) {
                if (!r.checkbox.disabled) r.checkbox.checked = selectAllCb.checked;
            });
            selectAllCb.indeterminate = false;
        });
    }
    var selBtn = body.querySelector('.compliance-apply-selected');
    if (selBtn) {
        selBtn.onclick = function () {
            var items = registry.filter(function (r) { return !r.checkbox.disabled && r.checkbox.checked; });
            if (items.length === 0) { if (typeof showError === 'function') showError('Select at least one finding to apply.'); return; }
            markApplied(items);
            applyInsightFixes(items);
        };
    }
    var allBtn = body.querySelector('.compliance-apply-all');
    if (allBtn) {
        allBtn.onclick = function () {
            var items = registry.filter(function (r) { return !r.checkbox.disabled; });
            if (items.length === 0) { if (typeof showError === 'function') showError('All findings in this tab are already applied.'); return; }
            markApplied(items);
            applyInsightFixes(items);
        };
    }
}

function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}
