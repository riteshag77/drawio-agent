/** Insights panels — cost, Well-Architected, compliance, token usage display. */

// ── Cost Panel ────────────────────────────────────────────

// Fixed nominal-categorical order (never cycled) mapping each cost category
// to a --chart-cat-N token defined per-theme in static/themes/*.css. "Other"
// intentionally gets no slot — it reads as a neutral gray, not a generated hue.
var COST_CATEGORY_COLOR_SLOT = {
    'Compute': 1,
    'Networking': 2,
    'Storage': 3,
    'Database': 4,
    'Security': 5,
    'Analytics': 6,
    'Integration': 7,
    'Management': 8,
};

function costCategoryColor(cat) {
    var slot = COST_CATEGORY_COLOR_SLOT[cat];
    return slot ? 'var(--chart-cat-' + slot + ')' : 'var(--text-muted)';
}

function renderCostDonut(catTotals, grandTotal) {
    var wrap = document.getElementById('cost-donut');
    if (!wrap) return;
    var catKeys = Object.keys(catTotals).filter(function (c) { return catTotals[c] > 0; });
    if (!(grandTotal > 0) || catKeys.length === 0) {
        wrap.hidden = true;
        wrap.innerHTML = '';
        return;
    }
    wrap.hidden = false;

    var size = 140;
    var cx = size / 2;
    var cy = size / 2;
    var r = 54;
    var strokeWidth = 20;
    var circumference = 2 * Math.PI * r;
    var GAP_PX = 3;
    var svgNs = 'http://www.w3.org/2000/svg';

    var svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + size + ' ' + size);
    svg.setAttribute('width', size);
    svg.setAttribute('height', size);
    svg.classList.add('cost-donut-svg');

    var group = document.createElementNS(svgNs, 'g');
    group.setAttribute('transform', 'rotate(-90 ' + cx + ' ' + cy + ')');

    var track = document.createElementNS(svgNs, 'circle');
    track.setAttribute('cx', cx);
    track.setAttribute('cy', cy);
    track.setAttribute('r', r);
    track.setAttribute('fill', 'none');
    track.setAttribute('stroke', 'var(--border-subtle)');
    track.setAttribute('stroke-width', strokeWidth);
    group.appendChild(track);

    var offsetSoFar = 0;
    var legendRows = [];
    catKeys.forEach(function (cat) {
        var subTotal = catTotals[cat];
        var pct = subTotal / grandTotal * 100;
        var length = Math.max((pct / 100) * circumference - GAP_PX, 0);
        var color = costCategoryColor(cat);

        var seg = document.createElementNS(svgNs, 'circle');
        seg.setAttribute('cx', cx);
        seg.setAttribute('cy', cy);
        seg.setAttribute('r', r);
        seg.setAttribute('fill', 'none');
        seg.setAttribute('stroke', color);
        seg.setAttribute('stroke-width', strokeWidth);
        seg.setAttribute('stroke-linecap', 'round');
        seg.setAttribute('stroke-dasharray', length + ' ' + circumference);
        seg.setAttribute('stroke-dashoffset', -offsetSoFar);
        var title = document.createElementNS(svgNs, 'title');
        title.textContent = cat + ': A$' + subTotal.toFixed(0) + ' (' + pct.toFixed(0) + '%)';
        seg.appendChild(title);
        group.appendChild(seg);

        offsetSoFar += (pct / 100) * circumference;
        legendRows.push({ cat: cat, subTotal: subTotal, pct: pct, color: color, segEl: seg });
    });

    svg.appendChild(group);

    var centerWrap = document.createElement('div');
    centerWrap.className = 'cost-donut-center';
    var centerAmount = document.createElement('div');
    centerAmount.className = 'cost-donut-center-amount';
    centerAmount.textContent = 'A$' + grandTotal.toFixed(0);
    var centerLabel = document.createElement('div');
    centerLabel.className = 'cost-donut-center-label';
    centerLabel.textContent = '/mo';
    centerWrap.appendChild(centerAmount);
    centerWrap.appendChild(centerLabel);

    var ring = document.createElement('div');
    ring.className = 'cost-donut-ring';
    ring.appendChild(svg);
    ring.appendChild(centerWrap);

    var legend = document.createElement('div');
    legend.className = 'cost-donut-legend';
    legendRows.forEach(function (row) {
        var item = document.createElement('div');
        item.className = 'cost-donut-legend-row';
        item.title = row.cat + ': A$' + row.subTotal.toFixed(0) + ' (' + row.pct.toFixed(0) + '%)';
        var swatch = document.createElement('span');
        swatch.className = 'cost-donut-swatch';
        swatch.style.background = row.color;
        var name = document.createElement('span');
        name.className = 'cost-donut-legend-name';
        name.textContent = row.cat;
        var value = document.createElement('span');
        value.className = 'cost-donut-legend-value';
        value.textContent = 'A$' + row.subTotal.toFixed(0) + ' · ' + row.pct.toFixed(0) + '%';
        item.appendChild(swatch);
        item.appendChild(name);
        item.appendChild(value);
        item.addEventListener('mouseenter', function () {
            row.segEl.setAttribute('stroke-width', strokeWidth + 4);
        });
        item.addEventListener('mouseleave', function () {
            row.segEl.setAttribute('stroke-width', strokeWidth);
        });
        legend.appendChild(item);
    });

    wrap.innerHTML = '';
    wrap.appendChild(ring);
    wrap.appendChild(legend);
}

/** Same reasoning as rerunWafComplianceCheck/rerunSuggestionsCheck — turning
 * Cost Estimate on only takes effect on the *next* chat turn by default,
 * which reads as "I turned this on and nothing happened" if a diagram
 * already exists. Re-runs against the current diagram via POST /analyze and
 * renders immediately, same as if this turn's response had included it. */
async function rerunCostEstimateCheck() {
    if (typeof currentXml !== 'string' || !currentXml.trim()) return;
    if (!document.getElementById('cost-estimate-toggle')?.checked) return;

    var providerSelect = document.getElementById('provider-select');
    var provider = (providerSelect && typeof parseProviderSelectValue === 'function')
        ? parseProviderSelectValue(providerSelect.value).provider
        : undefined;
    var payload = {
        xml: currentXml,
        platform: (typeof selectedPlatform !== 'undefined' && selectedPlatform) || 'aws',
        cost_estimate: true,
        well_architected: false,
        compliance: false,
    };
    if (provider) payload.provider = provider;

    if (typeof setTabLoading === 'function') setTabLoading('cost', true);
    try {
        var res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var data = await res.json();
        if (!res.ok || data.status !== 'success') {
            if (typeof showToast === 'function') {
                showToast('Could not refresh cost estimate: ' + (data.detail || 'server error'), 'warning');
            }
            return;
        }
        displayCostEstimate(data.cost_estimate);
    } catch (e) {
        console.error('Cost estimate re-check failed', e);
        if (typeof showToast === 'function') showToast('Could not refresh cost estimate — network error.', 'warning');
    } finally {
        if (typeof setTabLoading === 'function') setTabLoading('cost', false);
    }
}

function displayCostEstimate(costData) {
    if (!costData) return;
    const panel = document.getElementById('cost-panel');
    const body = document.getElementById('cost-panel-body');
    const totalEl = document.getElementById('cost-total-amount');
    if (!panel || !body || !totalEl) return;
    panel.classList.add('active');
    var grandTotal = costData.total || 0;
    var currency = costData.currency || 'AUD';
    var symbol = (typeof _moneySymbol === 'function') ? _moneySymbol(currency) : 'A$';
    totalEl.textContent = symbol + grandTotal.toFixed(0);
    body.innerHTML = '';
    var categories = costData.breakdown || {};
    var catKeys = Object.keys(categories);
    if (catKeys.length === 0) {
        body.innerHTML = '<div class="cost-empty">No billable services detected</div>';
        renderCostDonut({}, 0);
        return;
    }
    var catTotals = {};
    catKeys.forEach(function (cat) {
        var svcs = categories[cat] || [];
        var sub = 0;
        svcs.forEach(function (s) { sub += s.monthly || 0; });
        catTotals[cat] = sub;
    });
    renderCostDonut(catTotals, grandTotal);
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
        subEl.textContent = symbol + subTotal.toFixed(0);
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
            priceSpan.textContent = symbol + (svc.monthly || 0).toFixed(0);
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

function _formatTokenCount(n) {
    n = Number(n) || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
    return String(n);
}

function _moneySymbol(currency) {
    var c = (currency || 'AUD').toUpperCase();
    if (c === 'AUD') return 'A$';
    if (c === 'USD') return 'US$';
    return c + ' ';
}

function _formatMoney(amount, currency, digits) {
    var n = Number(amount);
    if (!Number.isFinite(n)) return '';
    var d = (typeof digits === 'number') ? digits : (Math.abs(n) < 0.01 ? 4 : 2);
    return '~' + _moneySymbol(currency) + n.toFixed(d);
}

function _usageCostAmount(usage) {
    if (!usage) return NaN;
    var costRaw = usage.estimated_cost != null ? usage.estimated_cost : usage.estimated_cost_usd;
    var costNum = (typeof costRaw === 'number') ? costRaw : parseFloat(costRaw);
    if (Number.isFinite(costNum)) return costNum;
    if (!Array.isArray(usage.calls)) return NaN;
    var summed = 0;
    var any = false;
    usage.calls.forEach(function (call) {
        if (!call) return;
        var c = call.estimated_cost != null ? call.estimated_cost : call.estimated_cost_usd;
        var n = (typeof c === 'number') ? c : parseFloat(c);
        if (Number.isFinite(n)) {
            summed += n;
            any = true;
        }
    });
    return any ? summed : NaN;
}

function displayTokenUsage(usage) {
    var badge = document.getElementById('token-badge');
    if (!badge) return;
    if (!usage || !usage.total_tokens) {
        hideTokenUsage();
        return;
    }

    var total = usage.total_tokens || 0;
    var input = usage.total_input_tokens || 0;
    var output = usage.total_output_tokens || 0;
    if (!input && !output && total) {
        input = total;
    }
    var denom = Math.max(input + output, 1);
    var inPct = Math.round((input / denom) * 100);
    var outPct = Math.max(0, 100 - inPct);

    var costNum = _usageCostAmount(usage);
    var hasCost = Number.isFinite(costNum);
    var currency = usage.currency || 'AUD';
    var costLabel = hasCost ? _formatMoney(costNum, currency) : '';

    var summary = '~' + _formatTokenCount(total) + ' tok';
    if (hasCost) {
        summary += ' · ' + costLabel;
    }

    var summaryEl = document.getElementById('token-meter-summary');
    var inSeg = document.getElementById('token-meter-in');
    var outSeg = document.getElementById('token-meter-out');
    var inLabel = document.getElementById('token-meter-in-label');
    var outLabel = document.getElementById('token-meter-out-label');

    if (summaryEl) summaryEl.textContent = summary;
    if (inSeg) inSeg.style.flexGrow = String(Math.max(inPct, input ? 2 : 0));
    if (outSeg) outSeg.style.flexGrow = String(Math.max(outPct, output ? 2 : 0));
    if (inLabel) inLabel.textContent = 'in ' + _formatTokenCount(input);
    if (outLabel) outLabel.textContent = 'out ' + _formatTokenCount(output);

    badge.hidden = false;
    badge.removeAttribute('title');
    badge.setAttribute(
        'aria-label',
        'LLM usage: ' + summary + ', input ' + input + ', output ' + output
    );
}

function hideTokenUsage() {
    var badge = document.getElementById('token-badge');
    if (!badge) return;
    badge.hidden = true;
    var summaryEl = document.getElementById('token-meter-summary');
    var inSeg = document.getElementById('token-meter-in');
    var outSeg = document.getElementById('token-meter-out');
    var inLabel = document.getElementById('token-meter-in-label');
    var outLabel = document.getElementById('token-meter-out-label');
    if (summaryEl) summaryEl.textContent = '';
    if (inSeg) inSeg.style.flexGrow = '0';
    if (outSeg) outSeg.style.flexGrow = '0';
    if (inLabel) inLabel.textContent = 'in';
    if (outLabel) outLabel.textContent = 'out';
    badge.removeAttribute('aria-label');
    badge.removeAttribute('title');
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

// ── Suggestions Panel ─────────────────────────────────────

/** Same reasoning as rerunWafComplianceCheck() below — turning Suggestions on
 * only takes effect on the *next* chat turn by default, which reads as
 * "I turned this on and nothing happened" if a diagram already exists.
 * Re-runs against the current diagram via POST /analyze and renders
 * immediately, same as if this turn's response had included them. */
async function rerunSuggestionsCheck() {
    if (typeof currentXml !== 'string' || !currentXml.trim()) return;
    if (!document.getElementById('suggestions-toggle')?.checked) return;

    var providerSelect = document.getElementById('provider-select');
    var provider = (providerSelect && typeof parseProviderSelectValue === 'function')
        ? parseProviderSelectValue(providerSelect.value).provider
        : undefined;
    var views = typeof getSelectedDiagramViews === 'function' ? getSelectedDiagramViews() : [];
    var lastUserMessage = '';
    if (typeof chatMessages !== 'undefined') {
        for (var i = chatMessages.length - 1; i >= 0; i--) {
            if (chatMessages[i].role === 'user') { lastUserMessage = chatMessages[i].content || ''; break; }
        }
    }
    var payload = {
        xml: currentXml,
        platform: (typeof selectedPlatform !== 'undefined' && selectedPlatform) || 'aws',
        well_architected: false,
        compliance: false,
        suggestions: true,
        view: views[0] || 'network',
        human_prompt: lastUserMessage,
        applied_suggestions: typeof getAppliedSuggestionsPayload === 'function' ? getAppliedSuggestionsPayload() : [],
    };
    if (provider) payload.provider = provider;

    if (typeof setTabLoading === 'function') setTabLoading('suggestions', true);
    try {
        var res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var data = await res.json();
        if (!res.ok || data.status !== 'success') {
            if (typeof showToast === 'function') {
                showToast('Could not refresh suggestions: ' + (data.detail || 'server error'), 'warning');
            }
            return;
        }
        displaySuggestions(data.suggestions || []);
    } catch (e) {
        console.error('Suggestions re-check failed', e);
        if (typeof showToast === 'function') showToast('Could not refresh suggestions — network error.', 'warning');
    } finally {
        if (typeof setTabLoading === 'function') setTabLoading('suggestions', false);
    }
}

function toggleSuggestionsPanel() {
    var panel = document.getElementById('suggestions-panel');
    var body = document.getElementById('suggestions-panel-body');
    if (!panel) return;
    if (typeof updateInsightsTabVisibility === 'function') updateInsightsTabVisibility();
    if (document.getElementById('suggestions-toggle')?.checked) {
        panel.classList.add('active');
        if (body && !body.querySelector('.chat-suggestion-item')) {
            body.innerHTML = '<div class="suggestions-empty">Generate a diagram to see follow-up suggestions</div>';
        }
        if (typeof switchInsightsTab === 'function') switchInsightsTab('suggestions');
        rerunSuggestionsCheck();
    } else {
        panel.classList.remove('active');
        clearSuggestionsPanel({ keepPlaceholder: true });
    }
    updateRightPanelVisibility();
}

function hideSuggestionsPanel() {
    var panel = document.getElementById('suggestions-panel');
    if (panel) {
        panel.classList.remove('active');
        var toggle = document.getElementById('suggestions-toggle');
        if (toggle) toggle.checked = false;
        localStorage.setItem(SUGGESTIONS_STORAGE_KEY, '0');
        clearSuggestionsPanel({ keepPlaceholder: true });
        updateRightPanelVisibility();
    }
}

function clearSuggestionsPanel(opts) {
    var wrap = document.getElementById('suggestions-wrap');
    var body = document.getElementById('suggestions-panel-body');
    var selectAllRow = document.getElementById('suggestions-select-all-row');
    var actionRow = document.getElementById('suggestions-action-row');
    var selectAll = document.getElementById('suggestions-select-all');
    if (wrap) wrap._suggestions = [];
    if (body) {
        body.innerHTML = (opts && opts.keepPlaceholder)
            ? '<div class="suggestions-empty">Enable Follow-up suggestions in Settings, then generate a diagram</div>'
            : '';
    }
    if (selectAllRow) selectAllRow.hidden = true;
    if (actionRow) actionRow.hidden = true;
    if (selectAll) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
        selectAll.disabled = true;
    }
}

function displaySuggestions(suggestions) {
    var panel = document.getElementById('suggestions-panel');
    var wrap = document.getElementById('suggestions-wrap');
    var body = document.getElementById('suggestions-panel-body');
    var selectAllRow = document.getElementById('suggestions-select-all-row');
    var actionRow = document.getElementById('suggestions-action-row');
    var selectAllCb = document.getElementById('suggestions-select-all');
    if (!panel || !wrap || !body) return;

    function coerceItem(s) {
        if (typeof normalizeSuggestionItem === 'function') {
            return normalizeSuggestionItem(s);
        }
        if (s == null) return null;
        if (typeof s === 'string') {
            var t = s.trim();
            return t ? { text: t, category: 'general' } : null;
        }
        if (typeof s === 'object') {
            var text = String(s.text || s.suggestion || '').trim();
            if (!text) return null;
            var cat = String(s.category || 'general').toLowerCase();
            return { text: text, category: cat };
        }
        return null;
    }

    panel.classList.add('active');
    var raw = Array.isArray(suggestions) ? suggestions : [];
    var list = raw.map(coerceItem).filter(Boolean);
    wrap._suggestions = list.slice();

    if (!list.length) {
        body.innerHTML = '<div class="suggestions-empty">No suggestions returned for this diagram</div>';
        if (selectAllRow) selectAllRow.hidden = true;
        if (actionRow) actionRow.hidden = true;
        if (selectAllCb) {
            selectAllCb.checked = false;
            selectAllCb.disabled = true;
        }
        updateRightPanelVisibility();
        return;
    }

    var categoryOrder = ['network', 'logical', 'security', 'data_flow', 'general'];
    var categoryLabels = {
        network: 'Applies to Network page',
        logical: 'Applies to Logical page',
        security: 'Applies to Security page',
        data_flow: 'Applies to Data flow page',
        general: 'Applies generally',
    };
    var byCat = {};
    list.forEach(function (item, idx) {
        var cat = (item.category || 'general').toLowerCase();
        if (categoryOrder.indexOf(cat) < 0) cat = 'general';
        if (!byCat[cat]) byCat[cat] = [];
        byCat[cat].push({ item: item, idx: idx });
    });

    body.innerHTML = '';
    categoryOrder.forEach(function (cat) {
        var group = byCat[cat];
        if (!group || !group.length) return;
        var section = document.createElement('div');
        section.className = 'suggestions-group';
        section.dataset.category = cat;

        var header = document.createElement('div');
        header.className = 'suggestions-group-header';
        header.textContent = categoryLabels[cat] || cat;
        section.appendChild(header);

        group.forEach(function (entry) {
            var s = entry.item;
            var idx = entry.idx;
            var alreadyApplied = typeof isSuggestionApplied === 'function' && isSuggestionApplied(s);
            var label = document.createElement('label');
            label.className = 'chat-suggestion-item' + (alreadyApplied ? ' is-applied' : '');
            label.dataset.suggestionText = s.text;
            label.dataset.category = cat;

            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'suggestion-checkbox item-checkbox';
            cb.dataset.index = String(idx);
            cb.disabled = alreadyApplied;

            var main = document.createElement('span');
            main.className = 'suggestion-main';

            var chip = document.createElement('span');
            chip.className = 'suggestion-category-chip';
            chip.textContent = cat === 'data_flow' ? 'Data flow' : (cat.charAt(0).toUpperCase() + cat.slice(1));

            var textSpan = document.createElement('span');
            textSpan.className = 'suggestion-text';
            textSpan.textContent = s.text;

            main.appendChild(chip);
            main.appendChild(textSpan);

            var badge = document.createElement('span');
            badge.className = 'suggestion-applied-badge';
            badge.textContent = 'Applied';
            badge.hidden = !alreadyApplied;

            cb.addEventListener('change', function () {
                var pending = body.querySelectorAll('.item-checkbox:not(:disabled)');
                var checked = body.querySelectorAll('.item-checkbox:not(:disabled):checked');
                if (selectAllCb) {
                    selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
                    selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
                }
            });

            label.appendChild(cb);
            label.appendChild(main);
            label.appendChild(badge);
            section.appendChild(label);
        });
        body.appendChild(section);
    });

    if (selectAllRow) selectAllRow.hidden = false;
    if (selectAllCb) {
        selectAllCb.checked = false;
        selectAllCb.indeterminate = false;
        selectAllCb.disabled = false;
        selectAllCb.onchange = function () {
            body.querySelectorAll('.item-checkbox:not(:disabled)').forEach(function (cb) {
                cb.checked = selectAllCb.checked;
            });
            selectAllCb.indeterminate = false;
        };
    }
    if (typeof updateSuggestionWrapActions === 'function') updateSuggestionWrapActions(wrap);
    updateRightPanelVisibility();
}

function initSuggestionsPanel() {
    var selBtn = document.getElementById('suggestions-apply-selected');
    var allBtn = document.getElementById('suggestions-apply-all');
    if (selBtn) {
        selBtn.onclick = function () {
            var wrap = document.getElementById('suggestions-wrap');
            if (!wrap || !wrap._suggestions) return;
            var checked = wrap.querySelectorAll('.item-checkbox:checked:not(:disabled)');
            if (!checked.length) {
                if (typeof showError === 'function') showError('Select at least one suggestion to apply.');
                return;
            }
            var texts = Array.from(checked).map(function (cb) {
                return wrap._suggestions[Number(cb.dataset.index)];
            }).filter(Boolean);
            if (typeof implementSuggestions === 'function') implementSuggestions(texts, wrap);
        };
    }
    if (allBtn) {
        allBtn.onclick = function () {
            var wrap = document.getElementById('suggestions-wrap');
            if (!wrap || !wrap._suggestions) return;
            var pending = wrap._suggestions.filter(function (item) {
                return typeof isSuggestionApplied !== 'function' || !isSuggestionApplied(item);
            });
            if (!pending.length) {
                if (typeof showError === 'function') showError('All suggestions are already applied.');
                return;
            }
            if (typeof implementSuggestions === 'function') implementSuggestions(pending, wrap);
        };
    }
}

// ── Well-Architected Panel ────────────────────────────────

/** Turning WAF/Compliance on only takes effect on the *next* chat turn by
 * default — if a diagram already exists, that reads as "I turned this on
 * and nothing happened." Re-runs the check against the current diagram via
 * POST /analyze (split out from the full /chat pipeline) and renders results
 * immediately, same as if this turn's response had included them. */
async function rerunWafComplianceCheck() {
    if (typeof currentXml !== 'string' || !currentXml.trim()) return;
    var wafOn = !!document.getElementById('well-architected-toggle')?.checked;
    var compOn = !!document.getElementById('compliance-toggle')?.checked;
    if (!wafOn && !compOn) return;

    var providerSelect = document.getElementById('provider-select');
    var provider = (providerSelect && typeof parseProviderSelectValue === 'function')
        ? parseProviderSelectValue(providerSelect.value).provider
        : undefined;
    var payload = {
        xml: currentXml,
        platform: (typeof selectedPlatform !== 'undefined' && selectedPlatform) || 'aws',
        well_architected: wafOn,
        compliance: compOn,
    };
    if (provider) payload.provider = provider;
    if (compOn && typeof getSelectedComplianceFrameworks === 'function') {
        payload.compliance_frameworks = getSelectedComplianceFrameworks();
    }

    if (typeof setTabLoading === 'function') setTabLoading('waf', true);
    try {
        var res = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var data = await res.json();
        if (!res.ok || data.status !== 'success') {
            if (typeof showToast === 'function') {
                showToast('Could not refresh the check: ' + (data.detail || 'server error'), 'warning');
            }
            return;
        }
        if (wafOn) displayWellArchitected(data.well_architected);
        if (compOn) {
            window._lastComplianceData = data.compliance;
            displayCompliance(data.compliance);
        }
    } catch (e) {
        console.error('WAF/compliance re-check failed', e);
        if (typeof showToast === 'function') showToast('Could not refresh the check — network error.', 'warning');
    } finally {
        if (typeof setTabLoading === 'function') setTabLoading('waf', false);
    }
}

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
        rerunWafComplianceCheck();
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

// Shared select-all/apply controls spanning both the WAF and Compliance
// sections of the merged "WAF & Compliance" tab — each section registers its
// own current item list here instead of building its own select-all/apply UI.
var insightRegistryBySource = { waf: [], compliance: [] };

function allInsightItems() {
    return insightRegistryBySource.waf.concat(insightRegistryBySource.compliance);
}

function registerInsightItems(source, items) {
    insightRegistryBySource[source] = items;
    refreshUnifiedInsightControls();
}

function refreshUnifiedInsightControls() {
    var selectAllCb = document.getElementById('insight-select-all');
    var headerRow = document.getElementById('insight-select-all-row');
    var actionRow = document.getElementById('insight-action-row');
    var allItems = allInsightItems();
    var pending = allItems.filter(function (r) { return !r.checkbox.disabled; });
    var checked = pending.filter(function (r) { return r.checkbox.checked; });
    if (headerRow) headerRow.hidden = allItems.length === 0;
    if (actionRow) actionRow.hidden = pending.length === 0;
    if (selectAllCb) {
        selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
        selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
        selectAllCb.disabled = pending.length === 0;
    }
}

function _setInsightApplyButtonsDisabled(disabled) {
    var selBtn = document.getElementById('insight-apply-selected');
    var allBtn = document.getElementById('insight-apply-all');
    if (selBtn) selBtn.disabled = disabled;
    if (allBtn) allBtn.disabled = disabled;
}

function initInsightSelection() {
    var selectAllCb = document.getElementById('insight-select-all');
    var selBtn = document.getElementById('insight-apply-selected');
    var allBtn = document.getElementById('insight-apply-all');
    if (selectAllCb) {
        selectAllCb.addEventListener('change', function () {
            allInsightItems().forEach(function (r) {
                if (!r.checkbox.disabled) r.checkbox.checked = selectAllCb.checked;
            });
            selectAllCb.indeterminate = false;
        });
    }
    if (selBtn) {
        selBtn.onclick = function () {
            var items = allInsightItems().filter(function (r) { return !r.checkbox.disabled && r.checkbox.checked; });
            if (items.length === 0) { if (typeof showError === 'function') showError('Select at least one finding to apply.'); return; }
            applyInsightFixes(items);
        };
    }
    if (allBtn) {
        allBtn.onclick = function () {
            var items = allInsightItems().filter(function (r) { return !r.checkbox.disabled; });
            if (items.length === 0) { if (typeof showError === 'function') showError('All findings are already applied.'); return; }
            applyInsightFixes(items);
        };
    }
}

async function applyInsightFixes(items) {
    if (!items || items.length === 0) return;
    var text = 'Please update the diagram to address the following:\n' + items.map(function (it, i) {
        var line = (i + 1) + '. ' + it.message;
        if (it.missing_services && it.missing_services.length) {
            line += ' — Add one of: ' + it.missing_services.join(', ') + '.';
            if (it.recommendation) line += ' (' + it.recommendation + ')';
        } else if (it.recommendation) {
            line += ' — ' + it.recommendation;
        }
        return line;
    }).join('\n');
    var input = document.getElementById('chat-input');
    if (input) {
        input.value = text;
        if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
    }

    var xmlBefore = currentXml;
    _setInsightApplyButtonsDisabled(true);
    try {
        var result = await sendMessage();
        if (!result || result.outcome !== 'success') return; // error/stopped toast already shown by finishChatRun

        // No client-side "applied" bookkeeping: the WAF/compliance panels
        // above already re-rendered from this response's fresh analysis, so
        // any finding that's actually resolved is simply gone from the list;
        // anything still shown is still failing and stays selectable to retry.
        if (currentXml === xmlBefore && typeof showToast === 'function') {
            showToast('The diagram did not change — try rephrasing or applying findings individually.', 'warning');
        }
    } finally {
        _setInsightApplyButtonsDisabled(false);
    }
}

function displayWellArchitected(wafData) {
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
    var summaryText = document.querySelector('.waf-summary-text');
    if (summaryText) summaryText.textContent = wafData.summary || 'Assessed against Well-Architected pillars';
    body.innerHTML = '';
    var pillars = wafData.pillars || [];
    if (pillars.length === 0) {
        body.innerHTML = '<div class="waf-empty">No recommendations available</div>';
        registerInsightItems('waf', []);
        return;
    }

    var wafItems = [];

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
        (p.findings || []).forEach(function (f) {
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
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'suggestion-checkbox item-checkbox';
                cb.addEventListener('change', refreshUnifiedInsightControls);
                finding.insertBefore(cb, dot);
                wafItems.push({ message: f.message || '', recommendation: f.recommendation, missing_services: f.missing_services || [], checkbox: cb });
            }
            finding.appendChild(text);
            div.appendChild(finding);
        });
        body.appendChild(div);
    });

    registerInsightItems('waf', wafItems);
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
        if (typeof switchInsightsTab === 'function') switchInsightsTab('waf');
        rerunWafComplianceCheck();
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
    if (!compData || !compData.frameworks) return;
    var panel = document.getElementById('compliance-panel');
    var tabs = document.getElementById('compliance-tabs');
    var body = document.getElementById('compliance-body');
    if (!panel || !tabs || !body) return;
    panel.classList.add('active');
    var toggle = document.getElementById('compliance-toggle');
    if (toggle) toggle.checked = true;
    localStorage.setItem(COMPLIANCE_KEY, '1');

    var frameworks = compData.frameworks;
    var fwIds = Object.keys(frameworks);
    if (fwIds.length === 0) {
        body.innerHTML = '<div class="compliance-empty">No compliance frameworks evaluated</div>';
        registerInsightItems('compliance', []);
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
                    html += '<input type="checkbox" class="suggestion-checkbox item-checkbox" data-ctrl-idx="' + ctrlIdx
                        + '" data-finding-idx="' + findingIdx + '">';
                }
                html += '<span class="compliance-finding-dot compliance-severity-' + sev + '">' + sevIcon + '</span>';
                html += '<div class="compliance-finding-text">';
                html += '<span>' + escapeHtml(f.message || '') + '</span>';
                if (f.affected_services && f.affected_services.length > 0) {
                    html += '<span class="compliance-finding-services">Affects: ' + f.affected_services.join(', ') + '</span>';
                }
                if (f.recommendation) {
                    html += '<span class="compliance-finding-rec">&#x2192; ' + escapeHtml(f.recommendation) + '</span>';
                }
                html += '</div>';
                html += '</div>';
            });
            html += '</div>';
        });
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

    var complianceItems = [];
    body.querySelectorAll('.item-checkbox[data-ctrl-idx]').forEach(function (cb) {
        var ctrlIdx = parseInt(cb.getAttribute('data-ctrl-idx'), 10);
        var findingIdx = parseInt(cb.getAttribute('data-finding-idx'), 10);
        var finding = (controls[ctrlIdx] && controls[ctrlIdx].findings || [])[findingIdx];
        if (!finding) return;
        complianceItems.push({ message: finding.message || '', recommendation: finding.recommendation, missing_services: finding.missing_services || [], checkbox: cb });
        cb.addEventListener('change', refreshUnifiedInsightControls);
    });

    // Only the currently-selected framework tab's findings are registered —
    // switching frameworks naturally replaces this source's contribution to
    // the shared select-all/apply controls.
    registerInsightItems('compliance', complianceItems);
}

function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}
