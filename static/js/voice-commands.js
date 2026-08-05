/** Client-side voice command registry — exact/pattern match on final STT transcripts. */

const VOICE_COMMANDS = [];

function normalizeVoiceText(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/[^\w\s-]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function registerVoiceCommand(cmd) {
    if (!cmd || !cmd.id || typeof cmd.run !== 'function') return;
    const phrases = (cmd.phrases || []).map(normalizeVoiceText).filter(Boolean);
    phrases.sort((a, b) => b.length - a.length);
    VOICE_COMMANDS.push({
        id: cmd.id,
        label: cmd.label || cmd.id,
        phrases,
        pattern: cmd.pattern || null,
        when: typeof cmd.when === 'function' ? cmd.when : null,
        run: cmd.run,
    });
}

function voiceHasDiagram() {
    return !!(typeof currentXml !== 'undefined' && currentXml)
        || !!(typeof currentMermaid !== 'undefined' && currentMermaid);
}

function voiceRequireDiagram() {
    return voiceHasDiagram() ? null : 'No diagram loaded yet.';
}

function voiceSetSelect(id, value) {
    const el = document.getElementById(id);
    if (!el) return false;
    el.value = value;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
}

function voiceSetToggle(id, on) {
    const el = document.getElementById(id);
    if (!el) return false;
    if (el.checked === on) return true;
    el.checked = on;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
}

function voiceGetTemplates() {
    try {
        return JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
    } catch (_) {
        return [];
    }
}

function voiceFindTemplate(query) {
    const templates = voiceGetTemplates();
    if (!templates.length) return { error: 'No saved templates.' };
    const q = normalizeVoiceText(query);
    const byNum = q.match(/^(?:number\s+)?(\d+)$/);
    if (byNum) {
        const idx = parseInt(byNum[1], 10) - 1;
        if (idx < 0 || idx >= templates.length) {
            return { error: 'Template ' + byNum[1] + ' not found.' };
        }
        return { template: templates[idx], index: idx };
    }
    const found = templates.findIndex((t) => normalizeVoiceText(t.title || '').includes(q)
        || normalizeVoiceText(t.prompt || '').startsWith(q));
    if (found < 0) return { error: 'No template matching “' + query + '”.' };
    return { template: templates[found], index: found };
}

function voiceSelectProvider(name) {
    const select = document.getElementById('provider-select');
    if (!select) return 'Provider list not ready.';
    const q = normalizeVoiceText(name);
    const options = Array.from(select.options);
    let match = options.find((o) => normalizeVoiceText(o.value) === q
        || normalizeVoiceText(o.textContent) === q);
    if (!match) {
        match = options.find((o) => normalizeVoiceText(o.value).includes(q)
            || normalizeVoiceText(o.textContent).includes(q));
    }
    if (!match) return 'No provider matching “' + name + '”.';
    select.value = match.value;
    if (typeof updateContextChip === 'function') updateContextChip();
    return null;
}

function voiceSelectCapture(query) {
    const select = document.getElementById('captures-select');
    if (!select || !select.options.length) return 'No captured runs loaded. Say “refresh captures” first.';
    const q = normalizeVoiceText(query);
    const byNum = q.match(/^(?:number\s+)?(\d+)$/);
    const options = Array.from(select.options);
    if (byNum) {
        const idx = parseInt(byNum[1], 10) - 1;
        if (idx < 0 || idx >= options.length) return 'Capture ' + byNum[1] + ' not found.';
        select.selectedIndex = idx;
        return null;
    }
    const idx = options.findIndex((o) => normalizeVoiceText(o.textContent).includes(q)
        || normalizeVoiceText(o.value).includes(q));
    if (idx < 0) return 'No capture matching “' + query + '”.';
    select.selectedIndex = idx;
    return null;
}

function voiceComplianceFrameworkId(name) {
    const q = normalizeVoiceText(name).replace(/-/g, ' ').replace(/_/g, ' ');
    const map = {
        hipaa: 'hipaa',
        'pci dss': 'pci_dss',
        pci: 'pci_dss',
        pcidss: 'pci_dss',
        gdpr: 'gdpr',
        'soc 2': 'soc2',
        soc2: 'soc2',
        soc: 'soc2',
        naic: 'naic',
    };
    if (map[q]) return map[q];
    const key = Object.keys(map).find((k) => q.includes(k) || k.includes(q));
    return key ? map[key] : null;
}

function voiceSetComplianceFramework(name, enabled) {
    const id = voiceComplianceFrameworkId(name);
    if (!id) return 'Unknown framework “' + name + '”. Try HIPAA, PCI-DSS, GDPR, SOC 2, or NAIC.';
    const cb = document.querySelector('.compliance-fw-cb[value="' + id + '"]');
    if (!cb) return 'Framework control not found.';
    cb.checked = enabled;
    if (typeof saveComplianceFrameworkSelection === 'function') saveComplianceFrameworkSelection();
    return null;
}

function voiceInsightPool(source) {
    if (source === 'waf' || source === 'compliance') {
        if (typeof insightRegistryBySource === 'undefined') return [];
        return insightRegistryBySource[source] || [];
    }
    if (typeof allInsightItems !== 'function') return [];
    return allInsightItems();
}

function voiceApplyInsightAction(mode, source) {
    if (typeof allInsightItems !== 'function' && typeof insightRegistryBySource === 'undefined') {
        return 'Insights not ready.';
    }
    const label = source === 'waf' ? 'WAF' : source === 'compliance' ? 'compliance' : 'insight';
    const pending = voiceInsightPool(source).filter((r) => r.checkbox && !r.checkbox.disabled);
    if (!pending.length) {
        return 'No ' + label + ' findings to apply. Generate a diagram with that review enabled first.';
    }
    if (mode === 'select-all') {
        pending.forEach((r) => { r.checkbox.checked = true; });
        if (!source) {
            const selectAllCb = document.getElementById('insight-select-all');
            if (selectAllCb) {
                selectAllCb.checked = true;
                selectAllCb.indeterminate = false;
            }
        }
        if (typeof refreshUnifiedInsightControls === 'function') refreshUnifiedInsightControls();
        if (typeof switchInsightsTab === 'function') switchInsightsTab('waf');
        return null;
    }
    let items = pending;
    if (mode === 'selected') {
        items = pending.filter((r) => r.checkbox.checked);
        if (!items.length) {
            return 'Select at least one ' + label + ' finding first (say “select all ' + label.toLowerCase() + '”).';
        }
    }
    if (typeof switchInsightsTab === 'function') switchInsightsTab('waf');
    if (typeof applyInsightFixes === 'function') applyInsightFixes(items);
    return null;
}

function voiceListPatterns() {
    if (typeof GALLERY_PATTERNS === 'undefined' || !GALLERY_PATTERNS.length) {
        return { error: 'Pattern gallery is not available.' };
    }
    const lines = [
        'Architecture patterns — say “use pattern 1” or “use pattern serverless”:',
        '',
    ];
    GALLERY_PATTERNS.forEach((p, i) => {
        lines.push((i + 1) + '. ' + p.title + ' (' + p.id.replace(/_/g, ' ') + ') — ' + p.desc);
    });
    if (typeof appendChatBubble === 'function') {
        appendChatBubble('assistant', lines.join('\n'));
    }
    return 'Listed ' + GALLERY_PATTERNS.length + ' patterns';
}

function voiceFindPattern(query) {
    if (typeof GALLERY_PATTERNS === 'undefined' || !GALLERY_PATTERNS.length) {
        return { error: 'Pattern gallery is not available.' };
    }
    const q = normalizeVoiceText(query);
    const byNum = q.match(/^(?:number\s+|pattern\s+)?(\d+)$/);
    if (byNum) {
        const idx = parseInt(byNum[1], 10) - 1;
        if (idx < 0 || idx >= GALLERY_PATTERNS.length) {
            return { error: 'Pattern ' + byNum[1] + ' not found. Say “list patterns”.' };
        }
        return { pattern: GALLERY_PATTERNS[idx], index: idx };
    }
    const aliases = {
        '3 tier': 'three_tier',
        '3 tier web': 'three_tier',
        'three tier': 'three_tier',
        'three tier web app': 'three_tier',
        'web app': 'three_tier',
        serverless: 'serverless',
        'serverless api': 'serverless',
        'event driven': 'event_driven',
        events: 'event_driven',
        analytics: 'data_analytics',
        'data analytics': 'data_analytics',
        microservices: 'k8s_microservices',
        kubernetes: 'k8s_microservices',
        'static site': 'static_site',
        cdn: 'static_site',
        rag: 'rag_chat',
        'rag chat': 'rag_chat',
        auth: 'aws_auth',
        'aws auth': 'aws_auth',
        cognito: 'aws_auth',
    };
    const aliasId = aliases[q];
    let idx = -1;
    if (aliasId) {
        idx = GALLERY_PATTERNS.findIndex((p) => p.id === aliasId);
    }
    if (idx < 0) {
        idx = GALLERY_PATTERNS.findIndex((p) => {
            const title = normalizeVoiceText(p.title);
            const id = normalizeVoiceText(p.id.replace(/_/g, ' '));
            return title === q || id === q || title.includes(q) || id.includes(q) || q.includes(id);
        });
    }
    if (idx < 0) return { error: 'No pattern matching “' + query + '”. Say “list patterns”.' };
    return { pattern: GALLERY_PATTERNS[idx], index: idx };
}

function voiceUsePattern(query, send) {
    const found = voiceFindPattern(query);
    if (found.error) return found;
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = found.pattern.prompt;
        if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
    }
    if (send) {
        if (typeof isSending !== 'undefined' && isSending) {
            return { error: 'Already generating — wait or say “stop”.' };
        }
        if (typeof sendMessage === 'function') sendMessage();
        return 'Generating pattern ' + (found.index + 1) + ': ' + found.pattern.title;
    }
    return 'Loaded pattern ' + (found.index + 1) + ': ' + found.pattern.title + ' — say “send message” to generate';
}

function voiceStopListening() {
    if (typeof isListening !== 'undefined' && isListening && typeof speechRecognition !== 'undefined' && speechRecognition) {
        if (typeof voiceManuallyStopping !== 'undefined') voiceManuallyStopping = true;
        try { speechRecognition.stop(); } catch (_) {}
    }
}

function listVoiceCommandHelp() {
    const lines = [
        'Voice commands (say exactly while the mic is on):',
        '',
    ];
    const seen = new Set();
    VOICE_COMMANDS.forEach((cmd) => {
        if (seen.has(cmd.id)) return;
        seen.add(cmd.id);
        const sample = cmd.phrases[0] || (cmd.pattern ? cmd.label : cmd.id);
        lines.push('• ' + cmd.label + ' — “' + sample + '”');
    });
    lines.push('');
    lines.push('Limits: attach opens the file picker only; draw.io canvas editing is mouse/keyboard only; Chrome/Edge + localhost/https.');
    return lines.join('\n');
}

function showVoiceCommandHelp() {
    const text = listVoiceCommandHelp();
    if (typeof appendChatBubble === 'function') {
        appendChatBubble('assistant', text);
    } else if (typeof showToast === 'function') {
        showToast('Say “help” with the mic on for the command list', 'info');
    }
    return 'Showing voice command help';
}

/**
 * Try to run a voice command from a final STT transcript.
 * @returns {{ handled: boolean, label?: string, error?: string }}
 */
function tryRunVoiceCommand(transcript) {
    const normalized = normalizeVoiceText(transcript);
    if (!normalized) return { handled: false };

    let matched = null;
    let matchArgs = null;

    for (let i = 0; i < VOICE_COMMANDS.length; i++) {
        const cmd = VOICE_COMMANDS[i];
        if (cmd.pattern) {
            const m = normalized.match(cmd.pattern);
            if (m) {
                matched = cmd;
                matchArgs = m;
                break;
            }
        }
    }
    if (!matched) {
        // Longest exact phrase wins (phrases pre-sorted per command; scan all).
        let bestLen = -1;
        for (let i = 0; i < VOICE_COMMANDS.length; i++) {
            const cmd = VOICE_COMMANDS[i];
            for (let j = 0; j < cmd.phrases.length; j++) {
                const phrase = cmd.phrases[j];
                if (normalized === phrase && phrase.length > bestLen) {
                    matched = cmd;
                    bestLen = phrase.length;
                    matchArgs = null;
                }
            }
        }
    }

    if (!matched) return { handled: false };

    if (matched.when) {
        const guard = matched.when(matchArgs);
        if (guard) {
            if (typeof showError === 'function') showError(guard);
            if (typeof setVoiceStatus === 'function') setVoiceStatus(true, guard);
            return { handled: true, label: matched.label, error: guard };
        }
    }

    try {
        const result = matched.run(matchArgs);
        const msg = (result && typeof result === 'string') ? result : ('Done: ' + matched.label);
        if (result && typeof result === 'object' && result.error) {
            if (typeof showError === 'function') showError(result.error);
            if (typeof setVoiceStatus === 'function') setVoiceStatus(true, result.error);
            return { handled: true, label: matched.label, error: result.error };
        }
        if (typeof setVoiceStatus === 'function') setVoiceStatus(true, msg);
        if (typeof showToast === 'function') showToast(msg, 'success', 2200);
        if (typeof appendVerboseLog === 'function') {
            appendVerboseLog('voice', 'Command: ' + matched.id, 'info', { transcript: normalized });
        }
        return { handled: true, label: matched.label };
    } catch (err) {
        const errMsg = (err && err.message) ? err.message : 'Voice command failed';
        if (typeof showError === 'function') showError(errMsg);
        return { handled: true, label: matched.label, error: errMsg };
    }
}

function initVoiceCommands() {
    VOICE_COMMANDS.length = 0;

    registerVoiceCommand({
        id: 'help',
        label: 'Show voice help',
        phrases: ['help', 'what can i say', 'voice help', 'list commands', 'show commands'],
        run: () => showVoiceCommandHelp(),
    });

    registerVoiceCommand({
        id: 'send',
        label: 'Send message',
        phrases: ['send', 'send message', 'send that', 'generate diagram'],
        when: () => {
            if (typeof isSending !== 'undefined' && isSending) return 'Already generating — say “stop” to cancel.';
            const input = document.getElementById('chat-input');
            if (!input || !input.value.trim()) return 'Composer is empty — dictate a prompt first.';
            return null;
        },
        run: () => {
            sendMessage();
            return 'Sending message';
        },
    });

    registerVoiceCommand({
        id: 'stop-generation',
        label: 'Stop generation',
        phrases: ['stop', 'cancel', 'stop generation', 'cancel generation'],
        when: () => (typeof isSending !== 'undefined' && isSending ? null : 'Nothing is generating right now.'),
        run: () => {
            stopActiveRun();
            return 'Stopping generation';
        },
    });

    registerVoiceCommand({
        id: 'stop-listening',
        label: 'Stop listening',
        phrases: ['stop listening', 'stop voice', 'mic off', 'microphone off'],
        run: () => {
            voiceStopListening();
            return 'Microphone off';
        },
    });

    registerVoiceCommand({
        id: 'clear-chat',
        label: 'Clear chat',
        phrases: ['clear chat', 'new chat', 'reset chat'],
        run: () => {
            clearChat();
            return 'Chat cleared';
        },
    });

    registerVoiceCommand({
        id: 'clear-attachments',
        label: 'Clear attachments',
        phrases: ['clear attachments', 'remove attachments'],
        run: () => {
            clearAttachments();
            return 'Attachments cleared';
        },
    });

    registerVoiceCommand({
        id: 'attach-file',
        label: 'Attach file',
        phrases: ['attach file', 'attach files', 'open attachments', 'add attachment', 'upload file'],
        run: () => {
            const input = document.getElementById('file-input');
            if (input) input.click();
            return 'Opened file picker — choose a file';
        },
    });

    ['aws', 'gcp', 'azure', 'multi'].forEach((p) => {
        const labels = {
            aws: ['switch to aws', 'platform aws', 'select aws', 'use aws'],
            gcp: ['switch to gcp', 'platform gcp', 'select gcp', 'use gcp', 'switch to google cloud'],
            azure: ['switch to azure', 'platform azure', 'select azure', 'use azure'],
            multi: ['switch to multi', 'platform multi', 'select multi', 'use multi cloud', 'switch to multi cloud'],
        };
        registerVoiceCommand({
            id: 'platform-' + p,
            label: 'Platform ' + p.toUpperCase(),
            phrases: labels[p],
            run: () => {
                selectPlatform(p);
                return 'Platform set to ' + p.toUpperCase();
            },
        });
    });

    registerVoiceCommand({
        id: 'open-settings',
        label: 'Open settings',
        phrases: ['open settings', 'show settings'],
        run: () => {
            openSettings();
            return 'Settings open';
        },
    });

    registerVoiceCommand({
        id: 'close-settings',
        label: 'Close settings',
        phrases: ['close settings', 'hide settings'],
        run: () => {
            closeSettings();
            return 'Settings closed';
        },
    });

    [
        ['auto', ['diagram type auto', 'set diagram auto', 'auto diagram']],
        ['cloud', ['diagram type cloud', 'set diagram cloud', 'cloud diagram', 'cloud architecture']],
        ['simple', ['diagram type simple', 'set diagram simple', 'simple diagram', 'mermaid diagram']],
    ].forEach(([value, phrases]) => {
        registerVoiceCommand({
            id: 'diagram-mode-' + value,
            label: 'Diagram type ' + value,
            phrases,
            run: () => {
                voiceSetSelect('diagram-mode-select', value);
                if (typeof saveDiagramMode === 'function') saveDiagramMode();
                if (typeof updateContextChip === 'function') updateContextChip();
                return 'Diagram type: ' + value;
            },
        });
    });

    [
        ['auto', ['renderer auto', 'render auto', 'set renderer auto']],
        ['mcp', ['renderer mcp', 'render mcp', 'set renderer mcp', 'use mcp']],
        ['local', ['renderer local', 'render local', 'set renderer local', 'use local renderer']],
    ].forEach(([value, phrases]) => {
        registerVoiceCommand({
            id: 'renderer-' + value,
            label: 'Renderer ' + value,
            phrases,
            run: () => {
                voiceSetSelect('diagram-renderer-select', value);
                return 'Renderer: ' + value;
            },
        });
    });

    registerVoiceCommand({
        id: 'suggestions-on',
        label: 'Enable suggestions',
        phrases: ['enable suggestions', 'turn on suggestions', 'show suggestions'],
        run: () => {
            voiceSetToggle('suggestions-toggle', true);
            return 'Suggestions on';
        },
    });
    registerVoiceCommand({
        id: 'suggestions-off',
        label: 'Disable suggestions',
        phrases: ['disable suggestions', 'turn off suggestions', 'hide suggestions'],
        run: () => {
            voiceSetToggle('suggestions-toggle', false);
            return 'Suggestions off';
        },
    });
    registerVoiceCommand({
        id: 'cost-on',
        label: 'Enable cost estimate',
        phrases: ['enable cost', 'enable cost estimate', 'turn on cost', 'show cost'],
        run: () => {
            voiceSetToggle('cost-estimate-toggle', true);
            return 'Cost estimation on';
        },
    });
    registerVoiceCommand({
        id: 'cost-off',
        label: 'Disable cost estimate',
        phrases: ['disable cost', 'disable cost estimate', 'turn off cost', 'hide cost'],
        run: () => {
            voiceSetToggle('cost-estimate-toggle', false);
            return 'Cost estimation off';
        },
    });
    registerVoiceCommand({
        id: 'waf-on',
        label: 'Enable Well-Architected',
        phrases: ['enable well architected', 'enable waf', 'turn on waf', 'show waf'],
        run: () => {
            voiceSetToggle('well-architected-toggle', true);
            return 'Well-Architected on';
        },
    });
    registerVoiceCommand({
        id: 'waf-off',
        label: 'Disable Well-Architected',
        phrases: ['disable well architected', 'disable waf', 'turn off waf', 'hide waf'],
        run: () => {
            voiceSetToggle('well-architected-toggle', false);
            return 'Well-Architected off';
        },
    });
    registerVoiceCommand({
        id: 'compliance-on',
        label: 'Enable compliance',
        phrases: ['enable compliance', 'turn on compliance', 'show compliance'],
        run: () => {
            voiceSetToggle('compliance-toggle', true);
            return 'Compliance on';
        },
    });
    registerVoiceCommand({
        id: 'compliance-off',
        label: 'Disable compliance',
        phrases: ['disable compliance', 'turn off compliance', 'hide compliance'],
        run: () => {
            voiceSetToggle('compliance-toggle', false);
            return 'Compliance off';
        },
    });
    registerVoiceCommand({
        id: 'logs-on',
        label: 'Enable pipeline logs',
        phrases: ['enable logs', 'enable pipeline logs', 'turn on logs', 'show logs panel'],
        run: () => {
            voiceSetToggle('verbose-toggle', true);
            return 'Pipeline logs on';
        },
    });
    registerVoiceCommand({
        id: 'logs-off',
        label: 'Disable pipeline logs',
        phrases: ['disable logs', 'disable pipeline logs', 'turn off logs', 'hide logs panel'],
        run: () => {
            voiceSetToggle('verbose-toggle', false);
            return 'Pipeline logs off';
        },
    });

    registerVoiceCommand({
        id: 'enable-framework',
        label: 'Enable compliance framework',
        pattern: /^enable (hipaa|pci(?:\s*dss)?|gdpr|soc\s*2?|naic)$/,
        run: (m) => {
            const err = voiceSetComplianceFramework(m[1], true);
            return err ? { error: err } : ('Enabled ' + m[1].toUpperCase());
        },
    });
    registerVoiceCommand({
        id: 'disable-framework',
        label: 'Disable compliance framework',
        pattern: /^disable (hipaa|pci(?:\s*dss)?|gdpr|soc\s*2?|naic)$/,
        run: (m) => {
            const err = voiceSetComplianceFramework(m[1], false);
            return err ? { error: err } : ('Disabled ' + m[1].toUpperCase());
        },
    });

    [
        ['midnight-slate', ['theme midnight', 'theme midnight slate', 'set theme midnight', 'dark theme']],
        ['daylight', ['theme daylight', 'set theme daylight', 'light theme']],
        ['aurora-contrast', ['theme aurora', 'theme aurora contrast', 'set theme aurora']],
    ].forEach(([value, phrases]) => {
        registerVoiceCommand({
            id: 'theme-' + value,
            label: 'Theme ' + value,
            phrases,
            run: () => {
                voiceSetSelect('app-theme-select', value);
                if (typeof changeAppTheme === 'function') changeAppTheme();
                return 'Theme: ' + value;
            },
        });
    });

    registerVoiceCommand({
        id: 'save-template',
        label: 'Save template',
        phrases: ['save template', 'save current prompt', 'save as template'],
        run: () => {
            saveCurrentAsTemplate();
            return 'Template saved';
        },
    });

    registerVoiceCommand({
        id: 'use-template',
        label: 'Use template',
        pattern: /^use template (.+)$/,
        run: (m) => {
            const found = voiceFindTemplate(m[1]);
            if (found.error) return { error: found.error };
            document.getElementById('chat-input').value = found.template.prompt;
            if (typeof autoResizeChatInput === 'function') autoResizeChatInput();
            return 'Loaded template ' + (found.index + 1);
        },
    });

    registerVoiceCommand({
        id: 'delete-template',
        label: 'Delete template',
        pattern: /^delete template (.+)$/,
        run: (m) => {
            const found = voiceFindTemplate(m[1]);
            if (found.error) return { error: found.error };
            deleteTemplate(found.template.id);
            return 'Deleted template ' + (found.index + 1);
        },
    });

    registerVoiceCommand({
        id: 'captures-on',
        label: 'Enable save captures',
        phrases: ['enable captures', 'enable capture runs', 'turn on captures', 'save captures'],
        run: () => {
            voiceSetToggle('save-captures-toggle', true);
            return 'Saving captured runs on';
        },
    });
    registerVoiceCommand({
        id: 'captures-off',
        label: 'Disable save captures',
        phrases: ['disable captures', 'disable capture runs', 'turn off captures', 'stop saving captures'],
        run: () => {
            voiceSetToggle('save-captures-toggle', false);
            return 'Saving captured runs off';
        },
    });

    registerVoiceCommand({
        id: 'refresh-captures',
        label: 'Refresh captures',
        phrases: ['refresh captures', 'reload captures', 'list captures'],
        run: () => {
            loadCapturesList();
            return 'Refreshing captures';
        },
    });

    registerVoiceCommand({
        id: 'select-capture',
        label: 'Select capture',
        pattern: /^select capture (.+)$/,
        run: (m) => {
            const err = voiceSelectCapture(m[1]);
            return err ? { error: err } : ('Selected capture ' + m[1]);
        },
    });

    registerVoiceCommand({
        id: 'load-capture',
        label: 'Load capture',
        phrases: ['load capture', 'playback capture', 'load into preview', 'play capture'],
        run: () => {
            playbackSelectedCapture();
            return 'Loading capture into preview';
        },
    });

    registerVoiceCommand({
        id: 'download-drawio',
        label: 'Download .drawio',
        phrases: ['download diagram', 'download draw io', 'download drawio', 'save diagram', 'download xml'],
        when: voiceRequireDiagram,
        run: () => {
            downloadXML();
            return 'Downloading .drawio';
        },
    });

    registerVoiceCommand({
        id: 'download-png',
        label: 'Export PNG',
        phrases: ['download png', 'export png', 'save png', 'png export'],
        when: voiceRequireDiagram,
        run: () => {
            downloadPNG();
            return 'Exporting PNG';
        },
    });

    registerVoiceCommand({
        id: 'open-drawio',
        label: 'Open in diagrams.net',
        phrases: ['open in draw io', 'open in drawio', 'open in diagrams net', 'open externally'],
        when: voiceRequireDiagram,
        run: () => {
            openInDrawio();
            return 'Opening in diagrams.net';
        },
    });

    registerVoiceCommand({
        id: 'restore-version',
        label: 'Restore version',
        pattern: /^restore version (\d+)$/,
        when: () => (typeof diagramVersions !== 'undefined' && diagramVersions.length
            ? null
            : 'No diagram versions saved yet.'),
        run: (m) => {
            const idx = parseInt(m[1], 10) - 1;
            if (idx < 0 || idx >= diagramVersions.length) {
                return { error: 'Version ' + m[1] + ' not found.' };
            }
            restoreDiagramVersion(idx);
            return 'Restored version ' + m[1];
        },
    });

    registerVoiceCommand({
        id: 'previous-version',
        label: 'Previous version',
        phrases: ['previous version', 'restore previous version', 'undo version'],
        when: () => (typeof diagramVersions !== 'undefined' && diagramVersions.length
            ? null
            : 'No diagram versions saved yet.'),
        run: () => {
            const target = (typeof activeDiagramVersion === 'number' && activeDiagramVersion > 0)
                ? activeDiagramVersion - 1
                : 0;
            restoreDiagramVersion(target);
            return 'Restored previous version';
        },
    });

    registerVoiceCommand({
        id: 'insights-cost',
        label: 'Insights: Cost',
        phrases: ['show cost tab', 'cost tab', 'insights cost', 'switch to cost'],
        run: () => {
            switchInsightsTab('cost');
            return 'Cost tab';
        },
    });
    registerVoiceCommand({
        id: 'insights-suggestions',
        label: 'Insights: Suggestions',
        phrases: ['show suggestions tab', 'suggestions tab', 'insights suggestions', 'switch to suggestions'],
        run: () => {
            switchInsightsTab('suggestions');
            return 'Suggestions tab';
        },
    });
    registerVoiceCommand({
        id: 'insights-waf',
        label: 'Insights: Network',
        phrases: [
            'show waf tab', 'waf tab', 'compliance tab', 'insights waf',
            'switch to waf', 'switch to compliance', 'waf and compliance',
        ],
        run: () => {
            switchInsightsTab('waf');
            return 'WAF / Compliance tab';
        },
    });
    registerVoiceCommand({
        id: 'insights-logs',
        label: 'Insights: Logs',
        phrases: ['show logs tab', 'logs tab', 'insights logs', 'switch to logs', 'pipeline logs tab'],
        run: () => {
            switchInsightsTab('logs');
            return 'Logs tab';
        },
    });

    registerVoiceCommand({
        id: 'hide-cost-panel',
        label: 'Hide cost panel',
        phrases: ['hide cost panel', 'close cost panel'],
        run: () => {
            hideCostEstimate();
            return 'Cost panel hidden';
        },
    });
    registerVoiceCommand({
        id: 'hide-waf-panel',
        label: 'Hide WAF panel',
        phrases: ['hide waf panel', 'close waf panel'],
        run: () => {
            hideWellArchitectedPanel();
            return 'WAF panel hidden';
        },
    });
    registerVoiceCommand({
        id: 'hide-compliance-panel',
        label: 'Hide compliance panel',
        phrases: ['hide compliance panel', 'close compliance panel'],
        run: () => {
            hideCompliancePanel();
            return 'Compliance panel hidden';
        },
    });

    registerVoiceCommand({
        id: 'compliance-framework-tab',
        label: 'Compliance framework tab',
        pattern: /^(?:show|switch to) (hipaa|pci(?:\s*dss)?|gdpr|soc\s*2?|naic)(?: tab)?$/,
        when: () => {
            const tabs = document.getElementById('compliance-tabs');
            return (tabs && tabs.children.length) ? null : 'No compliance results yet.';
        },
        run: (m) => {
            const id = voiceComplianceFrameworkId(m[1]);
            if (!id) return { error: 'Unknown framework.' };
            switchComplianceTab(id);
            return 'Compliance tab: ' + id;
        },
    });

    registerVoiceCommand({
        id: 'select-all-insights',
        label: 'Select all insights',
        phrases: ['select all insights', 'select all findings'],
        run: () => {
            const err = voiceApplyInsightAction('select-all');
            return err ? { error: err } : 'All insights selected';
        },
    });
    registerVoiceCommand({
        id: 'select-all-waf',
        label: 'Select all WAF findings',
        phrases: ['select all waf', 'select all waf findings', 'select waf findings'],
        run: () => {
            const err = voiceApplyInsightAction('select-all', 'waf');
            return err ? { error: err } : 'All WAF findings selected';
        },
    });
    registerVoiceCommand({
        id: 'select-all-compliance',
        label: 'Select all compliance findings',
        phrases: [
            'select all compliance', 'select all compliance findings',
            'select compliance findings',
        ],
        run: () => {
            const err = voiceApplyInsightAction('select-all', 'compliance');
            return err ? { error: err } : 'All compliance findings selected';
        },
    });
    registerVoiceCommand({
        id: 'apply-selected-insights',
        label: 'Apply selected insights',
        phrases: ['apply selected', 'apply selected insights', 'apply selected findings'],
        run: () => {
            const err = voiceApplyInsightAction('selected');
            return err ? { error: err } : 'Applying selected insights';
        },
    });
    registerVoiceCommand({
        id: 'apply-selected-waf',
        label: 'Apply selected WAF',
        phrases: ['apply selected waf', 'apply selected waf findings'],
        run: () => {
            const err = voiceApplyInsightAction('selected', 'waf');
            return err ? { error: err } : 'Applying selected WAF findings';
        },
    });
    registerVoiceCommand({
        id: 'apply-selected-compliance',
        label: 'Apply selected compliance',
        phrases: ['apply selected compliance', 'apply selected compliance findings'],
        run: () => {
            const err = voiceApplyInsightAction('selected', 'compliance');
            return err ? { error: err } : 'Applying selected compliance findings';
        },
    });
    registerVoiceCommand({
        id: 'apply-all-insights',
        label: 'Apply all insights',
        phrases: ['apply all', 'apply all insights', 'apply all findings'],
        run: () => {
            const err = voiceApplyInsightAction('all');
            return err ? { error: err } : 'Applying all insights';
        },
    });
    registerVoiceCommand({
        id: 'apply-all-waf',
        label: 'Apply all WAF findings',
        phrases: [
            'apply waf', 'apply all waf', 'apply waf findings', 'apply all waf findings',
            'apply well architected', 'apply all well architected',
        ],
        run: () => {
            const err = voiceApplyInsightAction('all', 'waf');
            return err ? { error: err } : 'Applying all WAF findings';
        },
    });
    registerVoiceCommand({
        id: 'apply-all-compliance',
        label: 'Apply all compliance findings',
        phrases: [
            'apply compliance', 'apply all compliance', 'apply compliance findings',
            'apply all compliance findings',
        ],
        run: () => {
            const err = voiceApplyInsightAction('all', 'compliance');
            return err ? { error: err } : 'Applying all compliance findings';
        },
    });

    registerVoiceCommand({
        id: 'list-patterns',
        label: 'List patterns',
        phrases: [
            'list patterns', 'show patterns', 'pattern gallery', 'list architecture patterns',
            'what patterns', 'show pattern gallery',
        ],
        run: () => voiceListPatterns(),
    });
    registerVoiceCommand({
        id: 'use-pattern',
        label: 'Use pattern',
        pattern: /^(?:use|select|load|run|start) pattern (.+)$/,
        run: (m) => voiceUsePattern(m[1], true),
    });
    registerVoiceCommand({
        id: 'load-pattern',
        label: 'Load pattern into composer',
        pattern: /^load pattern (.+) into (?:composer|chat|input)$/,
        run: (m) => voiceUsePattern(m[1], false),
    });
    // Short forms: "pattern 1", "pattern serverless"
    registerVoiceCommand({
        id: 'pattern-shortcut',
        label: 'Use pattern (short)',
        pattern: /^pattern (.+)$/,
        run: (m) => voiceUsePattern(m[1], true),
    });

    registerVoiceCommand({
        id: 'clear-logs',
        label: 'Clear logs',
        phrases: ['clear logs', 'clear pipeline logs'],
        run: () => {
            clearVerboseLogs();
            return 'Logs cleared';
        },
    });
    registerVoiceCommand({
        id: 'close-log-panel',
        label: 'Close log panel',
        phrases: ['close log panel', 'close logs'],
        run: () => {
            closeLogPanel();
            return 'Log panel closed';
        },
    });


    registerVoiceCommand({
        id: 'mobile-chat',
        label: 'Mobile: Chat',
        phrases: ['show chat', 'chat pane', 'mobile chat', 'switch to chat'],
        run: () => {
            switchMobilePane('chat');
            return 'Chat pane';
        },
    });
    registerVoiceCommand({
        id: 'mobile-diagram',
        label: 'Mobile: Diagram',
        phrases: ['show diagram', 'diagram pane', 'mobile diagram', 'switch to diagram'],
        run: () => {
            switchMobilePane('diagram');
            return 'Diagram pane';
        },
    });
    registerVoiceCommand({
        id: 'mobile-insights',
        label: 'Mobile: Insights',
        phrases: ['show insights', 'insights pane', 'mobile insights', 'switch to insights'],
        run: () => {
            switchMobilePane('insights');
            return 'Insights pane';
        },
    });

    registerVoiceCommand({
        id: 'set-provider',
        label: 'Set provider',
        pattern: /^(?:set provider|use provider|provider) (.+)$/,
        run: (m) => {
            const err = voiceSelectProvider(m[1]);
            return err ? { error: err } : ('Provider: ' + m[1]);
        },
    });

    // Longer phrases first across the registry for exact matching stability.
    VOICE_COMMANDS.sort((a, b) => {
        const al = (a.phrases[0] || '').length;
        const bl = (b.phrases[0] || '').length;
        return bl - al;
    });
}
