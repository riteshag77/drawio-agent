const DRAWIO_ORIGIN = 'https://embed.diagrams.net';
        const DRAWIO_THEMES = ['kennedy', 'atlas', 'dark', 'min', 'sketch', 'simple'];
        const THEME_STORAGE_KEY = 'drawio-agent-drawio-theme';
        const TEMPLATES_STORAGE_KEY = 'drawio-agent-templates';

        function getDrawioUrl(theme) {
            const ui = DRAWIO_THEMES.includes(theme) ? theme : 'kennedy';
            return `${DRAWIO_ORIGIN}/?embed=1&ui=${ui}&spin=1&proto=json&configure=1&libs=aws4;gcp2;azure2&nav=1&saveAndExit=0&noSaveBtn=1&noExitBtn=1`;
        }

        let currentXml = '';
        let previousXml = null;
        let currentArchitecture = null;
        let previousArchitecture = null;
        let pendingXmlExport = null;
        let attachmentContext = '';
        let attachmentImageData = null;
        let hasImageAttachment = false;
        let lastValidationFeedback = null;
        let pendingPngExport = null;
        let chatMessages = [];
        let appliedSuggestionTexts = [];
        let isSending = false;
        let chatAbortController = null;
        let runStoppedByUser = false;
        let selectedPlatform = 'aws';
        let drawioReady = false;
        let pendingXml = null;
        let pendingMermaid = null;
        let currentMermaid = '';
        let messageHandler = null;
        let verboseLogging = false;
        let validationPanelActive = false;
        let logEntryCount = 0;

        const VERBOSE_STORAGE_KEY = 'drawio-agent-verbose-logging';
        const MINIMAL_STYLE_STORAGE_KEY = 'drawio-agent-minimal-style';
        const CUSTOM_SYSTEM_MESSAGE_KEY = 'drawio-agent-custom-system-message';
        const VLM_VALIDATION_KEY = 'drawio-agent-vlm-validation';
        const VALIDATION_PROVIDER_KEY = 'drawio-agent-validation-provider';
        const AUTO_LAYOUT_STORAGE_KEY = 'drawio-agent-autolayout-on-load';
        const DIAGRAM_MODE_STORAGE_KEY = 'drawio-agent-diagram-mode';
        const MAX_VLM_CLIENT_RETRIES = 2;
        let vlmAutoRetryCount = 0;

        const EXAMPLE_PROMPTS = {
            aws: [
                'Highly available web app with API Gateway, Fargate in public/private subnets, Aurora, ElastiCache, CloudFront, Route 53, WAF, and CloudWatch',
                'Serverless data pipeline: S3 → Lambda → SQS → ECS workers → Redshift with KMS encryption',
            ],
            gcp: [
                'Cloud Run in Shared VPC with Cloud SQL, Memorystore, Cloud CDN, Cloud DNS, Cloud Armor, and Cloud Monitoring',
                'GKE cluster with private nodes, Cloud Load Balancing, Pub/Sub event bus, and BigQuery analytics',
            ],
            azure: [
                'Application Gateway, AKS in VNet subnets, Cosmos DB, Redis, Front Door, and Azure Monitor',
                'Event-driven Azure Functions with Service Bus, Blob Storage, and Key Vault secrets',
            ],
            multi: [
                'Hybrid: AWS ECS tasks querying GCP Spanner over site-to-site VPN with centralized logging',
                'Multi-cloud DR: primary AWS Aurora with Azure Cosmos DB read replica and global traffic manager',
            ],
        };

        const CHAT_PLACEHOLDERS = {
            aws: 'e.g., Add ElastiCache between API and database, or describe a new AWS architecture…',
            gcp: 'e.g., Switch Cloud Run to GKE, or describe a new GCP architecture…',
            azure: 'e.g., Add Azure Front Door WAF, or describe a new Azure architecture…',
            multi: 'e.g., Add cross-cloud VPN, or describe a hybrid architecture…',
        };

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

        function isAutolayoutOnLoadEnabled() {
            const toggle = document.getElementById('autolayout-toggle');
            return toggle ? toggle.checked : true;
        }

        function saveAutolayoutSetting() {
            localStorage.setItem(
                AUTO_LAYOUT_STORAGE_KEY,
                isAutolayoutOnLoadEnabled() ? '1' : '0'
            );
        }

        function initAutolayoutSetting() {
            const stored = localStorage.getItem(AUTO_LAYOUT_STORAGE_KEY);
            const toggle = document.getElementById('autolayout-toggle');
            if (toggle) {
                toggle.checked = stored === null ? true : stored === '1';
            }
        }

        function updateValidationProviderVisibility() {
            const vlmOn = document.getElementById('vlm-validation-toggle').checked;
            const sel = document.getElementById('validation-provider-select');
            sel.hidden = !vlmOn;
        }

        function getPromptSettings() {
            return {
                minimal_style: document.getElementById('minimal-style-toggle').checked,
                custom_system_message: document.getElementById('custom-system-message').value.slice(0, 5000),
                vlm_validation: document.getElementById('vlm-validation-toggle').checked,
                validation_provider: document.getElementById('validation-provider-select').value,
            };
        }

        function savePromptSettings() {
            const minimal = document.getElementById('minimal-style-toggle').checked;
            const custom = document.getElementById('custom-system-message').value.slice(0, 5000);
            const vlm = document.getElementById('vlm-validation-toggle').checked;
            const vp = document.getElementById('validation-provider-select').value;
            localStorage.setItem(MINIMAL_STYLE_STORAGE_KEY, minimal ? '1' : '0');
            localStorage.setItem(CUSTOM_SYSTEM_MESSAGE_KEY, custom);
            localStorage.setItem(VLM_VALIDATION_KEY, vlm ? '1' : '0');
            localStorage.setItem(VALIDATION_PROVIDER_KEY, vp);
            updateValidationProviderVisibility();
        }

        function initPromptSettings() {
            document.getElementById('minimal-style-toggle').checked =
                localStorage.getItem(MINIMAL_STYLE_STORAGE_KEY) === '1';
            document.getElementById('custom-system-message').value =
                localStorage.getItem(CUSTOM_SYSTEM_MESSAGE_KEY) || '';
            const vlmStored = localStorage.getItem(VLM_VALIDATION_KEY);
            document.getElementById('vlm-validation-toggle').checked =
                vlmStored === null ? true : vlmStored === '1';
            const vpStored = localStorage.getItem(VALIDATION_PROVIDER_KEY);
            if (vpStored) {
                document.getElementById('validation-provider-select').value = vpStored;
            }
            updateValidationProviderVisibility();
        }

        function hideValidationBanner() {
            validationPanelActive = false;
            const panel = document.getElementById('validation-panel');
            panel.classList.remove('active', 'critical', 'passed');
            document.getElementById('validation-panel-body').innerHTML = '';
            document.getElementById('btn-fix-layout').style.display = 'none';
            lastValidationFeedback = null;
            updateRightPanelVisibility();
        }

        function showValidationBanner(validation, feedback) {
            const panel = document.getElementById('validation-panel');
            const body = document.getElementById('validation-panel-body');
            const fixBtn = document.getElementById('btn-fix-layout');
            body.innerHTML = '';

            const issues = validation?.issues || [];
            issues.forEach(issue => {
                const item = document.createElement('div');
                item.className = `validation-issue-item ${issue.severity}`;
                item.innerHTML = `
                    <div class="validation-issue-type">${issue.type} · ${issue.severity}</div>
                    <div>${issue.description}</div>`;
                body.appendChild(item);
            });

            const suggestions = validation?.suggestions || [];
            if (suggestions.length) {
                const title = document.createElement('div');
                title.className = 'validation-suggestions-title';
                title.textContent = 'Suggestions';
                body.appendChild(title);
                suggestions.forEach(text => {
                    const item = document.createElement('div');
                    item.className = 'validation-suggestion-item';
                    item.textContent = text;
                    body.appendChild(item);
                });
            }

            const hasCritical = issues.some(i => i.severity === 'critical');
            panel.classList.remove('passed');
            panel.classList.toggle('critical', hasCritical);
            panel.classList.add('active');
            fixBtn.style.display = 'block';

            const badge = document.getElementById('validation-status-badge');
            badge.textContent = hasCritical
                ? `${issues.length} critical`
                : `${issues.length} warning${issues.length === 1 ? '' : 's'}`;

            validationPanelActive = true;
            lastValidationFeedback = feedback || buildClientValidationFeedback(validation);
            updateRightPanelVisibility();
        }

        function buildClientValidationFeedback(validation) {
            if (!validation?.issues?.length) return null;
            const lines = ['DIAGRAM VALIDATION FAILED', ''];
            validation.issues.forEach(issue => {
                lines.push(`- [${issue.type}] ${issue.description}`);
            });
            (validation.suggestions || []).forEach(s => lines.push(`- ${s}`));
            return lines.join('\n');
        }

        function exportDiagramXml() {
            return new Promise((resolve, reject) => {
                if (!drawioReady) {
                    resolve(currentXml || null);
                    return;
                }
                const iframe = document.getElementById('drawio-frame');
                pendingXmlExport = { resolve, reject };
                iframe.contentWindow.postMessage(JSON.stringify({
                    action: 'export',
                    format: 'xml',
                }), DRAWIO_ORIGIN);
                setTimeout(() => {
                    if (pendingXmlExport) {
                        pendingXmlExport.resolve(currentXml || null);
                        pendingXmlExport = null;
                    }
                }, 5000);
            });
        }

        function changeDrawioTheme() {
            const theme = document.getElementById('drawio-theme-select').value;
            localStorage.setItem(THEME_STORAGE_KEY, theme);
            drawioReady = false;
            pendingXml = currentXml || pendingXml;
            const iframe = document.getElementById('drawio-frame');
            if (iframe) {
                iframe.src = getDrawioUrl(theme);
            }
        }

        function loadTemplates() {
            const list = document.getElementById('template-list');
            if (!list) return;
            let templates = [];
            try {
                templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
            } catch (_) { templates = []; }
            list.innerHTML = '';
            if (!templates.length) {
                list.innerHTML = '<small style="color:var(--text-muted)">No saved templates yet.</small>';
                return;
            }
            templates.forEach(t => {
                const row = document.createElement('div');
                row.className = 'template-row';
                row.innerHTML = `<span>${t.title}</span>`;
                const useBtn = document.createElement('button');
                useBtn.type = 'button';
                useBtn.className = 'btn-chat-clear';
                useBtn.textContent = 'Use';
                useBtn.onclick = () => {
                    document.getElementById('chat-input').value = t.prompt;
                    autoResizeChatInput();
                };
                const delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.className = 'btn-chat-clear';
                delBtn.textContent = '×';
                delBtn.onclick = () => deleteTemplate(t.id);
                row.appendChild(useBtn);
                row.appendChild(delBtn);
                list.appendChild(row);
            });
        }

        function saveCurrentAsTemplate() {
            const prompt = document.getElementById('chat-input').value.trim();
            if (!prompt) {
                showError('Enter a prompt to save as template.');
                return;
            }
            let templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
            const title = prompt.length > 24 ? prompt.slice(0, 24) + '…' : prompt;
            templates.unshift({
                id: 't_' + Date.now(),
                title,
                prompt,
                createdAt: Date.now(),
            });
            templates = templates.slice(0, 20);
            localStorage.setItem(TEMPLATES_STORAGE_KEY, JSON.stringify(templates));
            loadTemplates();
        }

        function deleteTemplate(id) {
            let templates = JSON.parse(localStorage.getItem(TEMPLATES_STORAGE_KEY) || '[]');
            templates = templates.filter(t => t.id !== id);
            localStorage.setItem(TEMPLATES_STORAGE_KEY, JSON.stringify(templates));
            loadTemplates();
        }

        async function handleFileSelect(event) {
            const files = Array.from(event.target.files || []);
            const preview = document.getElementById('attachment-preview');
            preview.innerHTML = '';
            preview.hidden = true;
            attachmentContext = '';
            attachmentImageData = null;
            hasImageAttachment = false;

            for (const file of files.slice(0, 3)) {
                if (file.type.startsWith('image/')) {
                    const dataUrl = await new Promise((res, rej) => {
                        const r = new FileReader();
                        r.onload = () => res(r.result);
                        r.onerror = rej;
                        r.readAsDataURL(file);
                    });
                    attachmentImageData = dataUrl;
                    hasImageAttachment = true;
                    preview.hidden = false;
                    preview.textContent = `📷 ${file.name}`;
                } else if (file.type === 'application/pdf' || file.name.endsWith('.pdf')) {
                    preview.hidden = false;
                    preview.textContent = `📄 ${file.name} (PDF — describe in prompt or paste text)`;
                    attachmentContext += `[Attached PDF: ${file.name} — extract architecture from user description]\n`;
                } else {
                    const text = await file.text();
                    attachmentContext += `\n--- File: ${file.name} ---\n${text.slice(0, 8000)}\n`;
                    preview.hidden = false;
                    preview.textContent = `📄 ${file.name}`;
                }
            }
            event.target.value = '';
        }

        async function parseUrlContext(url) {
            const res = await fetch('/parse-url', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'URL parse failed');
            return `--- URL: ${url} (${data.title}) ---\n${data.content}`;
        }

        // ── Settings drawer ───────────────────────────────────────────
        function openSettings() {
            document.getElementById('settings-backdrop').classList.add('open');
            document.getElementById('settings-drawer').classList.add('open');
        }

        function closeSettings() {
            document.getElementById('settings-backdrop').classList.remove('open');
            document.getElementById('settings-drawer').classList.remove('open');
        }

        // ── Voice input (Web Speech API) ──────────────────────────────
        let speechRecognition = null;
        let isListening = false;
        let voiceBaseText = '';
        let voiceManuallyStopping = false;
        let voiceStatusBeforeListen = '';

        function isVoiceSecureContext() {
            // SpeechRecognition only works on https or localhost
            if (window.isSecureContext) return true;
            const h = location.hostname;
            return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '';
        }

        function setVoiceStatus(visible, text) {
            const bar = document.getElementById('voice-status');
            const label = document.getElementById('voice-status-text');
            if (!bar) return;
            if (text && label) label.textContent = text;
            bar.hidden = !visible;
        }

        function setComposerRunStatus(text, level = 'info') {
            const el = document.getElementById('composer-run-status');
            if (!el) return;
            if (!text) {
                el.hidden = true;
                el.textContent = '';
                el.removeAttribute('data-level');
                return;
            }
            el.textContent = text;
            el.hidden = false;
            el.dataset.level = level;
        }

        function updateSendStopButtons() {
            const sendBtn = document.getElementById('send-btn');
            const stopBtn = document.getElementById('stop-btn');
            const voiceBtn = document.getElementById('btn-voice');
            if (sendBtn) sendBtn.hidden = isSending;
            if (stopBtn) stopBtn.hidden = !isSending;
            if (voiceBtn) voiceBtn.disabled = isSending;
        }

        function stopActiveRun() {
            if (!isSending || !chatAbortController) return;
            runStoppedByUser = true;
            setComposerRunStatus('Stopping…', 'stopped');
            setDiagramStatus('Stopping generation…');
            chatAbortController.abort();
        }

        function finishChatRun({ outcome = 'idle', message = '', diagramMessage = '' } = {}) {
            setTyping(false);
            document.getElementById('diagram-loading').classList.remove('active');
            document.getElementById('progress-track').classList.remove('active');
            document.getElementById('progress-bar').style.width = '0%';

            if (message) {
                const level = outcome === 'success' ? 'success'
                    : outcome === 'error' ? 'error'
                    : outcome === 'stopped' ? 'stopped'
                    : 'info';
                setComposerRunStatus(message, level);
            } else if (outcome === 'idle') {
                setComposerRunStatus('');
            }

            if (diagramMessage) {
                setDiagramStatus(diagramMessage);
            } else if (outcome === 'stopped') {
                setDiagramStatus('Stopped — ready for your next message');
            } else if (outcome === 'error') {
                setDiagramStatus('Generation failed');
            } else if (outcome === 'success' && !diagramMessage) {
                // Draw.io load handler will refine status when the editor finishes loading
                setDiagramStatus('Rendering diagram…');
            } else if (outcome === 'idle' && !isSending) {
                if (currentXml || currentMermaid) {
                    setDiagramStatus('Diagram loaded — pan and zoom to explore');
                } else {
                    setDiagramStatus('Waiting for generation');
                }
            }
        }

        function initVoiceInput() {
            const btn = document.getElementById('btn-voice');
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) {
                if (btn) {
                    btn.classList.add('voice-unsupported');
                    btn.title = 'Voice input is not supported in this browser. Try Chrome or Edge.';
                }
                return;
            }
            speechRecognition = new SR();
            speechRecognition.continuous = true;
            speechRecognition.interimResults = true;
            speechRecognition.maxAlternatives = 1;
            speechRecognition.lang = navigator.language || 'en-US';

            speechRecognition.onstart = () => {
                isListening = true;
                voiceManuallyStopping = false;
                voiceBaseText = document.getElementById('chat-input').value.trim();
                voiceStatusBeforeListen = document.getElementById('diagram-status').textContent;
                if (btn) btn.classList.add('listening');
                setVoiceStatus(true, 'Listening… speak now');
                setComposerRunStatus('Listening… speak your instruction, click mic again to finish', 'running');
                setDiagramStatus('Listening… speak your instruction');
            };
            speechRecognition.onresult = (event) => {
                let interim = '';
                const finals = [];
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const piece = event.results[i][0].transcript;
                    if (event.results[i].isFinal) {
                        finals.push(piece);
                    } else {
                        interim += piece;
                    }
                }
                if (finals.length) {
                    const joined = finals.join('').trim();
                    voiceBaseText = voiceBaseText ? `${voiceBaseText} ${joined}`.trim() : joined;
                }
                const input = document.getElementById('chat-input');
                const display = voiceBaseText
                    ? (interim ? `${voiceBaseText} ${interim}`.trim() : voiceBaseText)
                    : interim.trim();
                input.value = display;
                autoResizeChatInput();
            };
            speechRecognition.onerror = (e) => {
                if (e.error === 'aborted' && voiceManuallyStopping) {
                    voiceManuallyStopping = false;
                    stopVoiceInput();
                    return;
                }
                appendVerboseLog('client', `Voice input error: ${e.error}`, 'warn');
                let msg = '';
                switch (e.error) {
                    case 'not-allowed':
                    case 'service-not-allowed':
                        msg = 'Microphone access was blocked. Click the lock icon in the address bar and allow the microphone, then try again.';
                        break;
                    case 'no-speech':
                        msg = 'No speech detected. Try speaking a bit louder or closer to the mic.';
                        break;
                    case 'audio-capture':
                        msg = 'No microphone was found. Please connect a microphone and try again.';
                        break;
                    case 'network':
                        msg = 'Voice recognition needs an internet connection and a secure (https/localhost) page.';
                        break;
                    case 'aborted':
                        break;
                    default:
                        msg = `Voice input error: ${e.error}`;
                }
                if (msg) showError(msg);
                stopVoiceInput();
            };
            speechRecognition.onend = () => {
                if (isListening && !voiceManuallyStopping) {
                    try {
                        speechRecognition.start();
                    } catch (_) {
                        stopVoiceInput();
                    }
                    return;
                }
                stopVoiceInput();
            };
        }

        function stopVoiceInput() {
            isListening = false;
            const btn = document.getElementById('btn-voice');
            if (btn) btn.classList.remove('listening');
            setVoiceStatus(false);
            if (!isSending) {
                setComposerRunStatus('Ready — type or dictate your next message', 'info');
                if (voiceStatusBeforeListen && (currentXml || currentMermaid)) {
                    setDiagramStatus(voiceStatusBeforeListen);
                } else if (currentXml || currentMermaid) {
                    setDiagramStatus('Diagram loaded — pan and zoom to explore');
                } else {
                    setDiagramStatus('Waiting for generation');
                }
            }
        }

        async function toggleVoiceInput() {
            if (isSending) {
                showError('Wait for the current generation to finish, or press Stop.');
                return;
            }
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR || !speechRecognition) {
                showError('Voice input is not supported in this browser. Please use Chrome, Edge, or Safari.');
                return;
            }
            if (!isVoiceSecureContext()) {
                showError('Voice input requires a secure connection. Open the app via http://localhost:8000 (not a raw IP) or serve it over https.');
                return;
            }
            if (isListening) {
                voiceManuallyStopping = true;
                try { speechRecognition.stop(); } catch (_) {}
                return;
            }
            if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    stream.getTracks().forEach(t => t.stop());
                } catch (err) {
                    showError('Microphone permission is required for voice input. Please allow access and try again.');
                    appendVerboseLog('client', `getUserMedia denied: ${err.name}`, 'warn');
                    return;
                }
            }
            const startRecognition = () => {
                speechRecognition.start();
                document.getElementById('chat-input').focus();
            };
            try {
                startRecognition();
            } catch (err) {
                appendVerboseLog('client', `Voice start failed: ${err.message}`, 'warn');
                try { speechRecognition.stop(); } catch (_) {}
                setTimeout(() => {
                    try {
                        startRecognition();
                    } catch (retryErr) {
                        showError('Could not start voice input. Try again or type your message.');
                        appendVerboseLog('client', `Voice retry failed: ${retryErr.message}`, 'warn');
                    }
                }, 150);
            }
        }

        // ── Flow Notes panel ──────────────────────────────────────────
        const PATTERN_LABELS = {
            three_tier_web: 'HA 3-Tier Web',
            serverless_api: 'Serverless API',
            event_driven: 'Event-Driven',
            data_analytics: 'Data Analytics',
            microservices_k8s: 'Microservices',
            static_site_cdn: 'Static Site + CDN',
        };

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML;
        }

        function exportDiagramPng() {
            return new Promise((resolve, reject) => {
                if (!drawioReady) {
                    reject(new Error('Draw.io not ready'));
                    return;
                }
                const iframe = document.getElementById('drawio-frame');
                pendingPngExport = { resolve, reject };
                iframe.contentWindow.postMessage(JSON.stringify({
                    action: 'export',
                    format: 'png',
                    scale: 1,
                }), DRAWIO_ORIGIN);
                setTimeout(() => {
                    if (pendingPngExport) {
                        pendingPngExport.reject(new Error('PNG export timed out'));
                        pendingPngExport = null;
                    }
                }, 8000);
            });
        }

        async function runVisualValidation() {
            const settings = getPromptSettings();
            if (!settings.vlm_validation) return;
            if (!currentArchitecture && !(currentXml && currentXml.length > 200)) return;

            try {
                appendVerboseLog('validation', 'Exporting diagram PNG for VLM check', 'info');
                const imageData = await exportDiagramPng();
                const res = await fetch('/validate-diagram', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_data: imageData, validation_provider: settings.validation_provider }),
                });
                const data = await res.json();
                mergeServerLogs(data.logs);
                if (!res.ok) return;

                const validation = data.validation;
                appendVerboseLog('validation', 'VLM validation result', validation?.valid ? 'success' : 'warn', validation);

                if (validation && !validation.valid) {
                    const feedback = buildClientValidationFeedback(validation);
                    if (vlmAutoRetryCount < MAX_VLM_CLIENT_RETRIES && feedback) {
                        vlmAutoRetryCount += 1;
                        appendVerboseLog(
                            'validation',
                            `VLM auto-retry ${vlmAutoRetryCount}/${MAX_VLM_CLIENT_RETRIES}`,
                            'info',
                        );
                        await autoRetryWithVlm(imageData, feedback);
                        return;
                    }
                    showValidationBanner(validation, feedback);
                    setDiagramStatus('Diagram loaded — layout issues detected');
                } else {
                    vlmAutoRetryCount = 0;
                    showValidationPassed();
                }
            } catch (err) {
                appendVerboseLog('validation', `VLM validation skipped: ${err.message}`, 'warn');
            }
        }

        function showValidationPassed() {
            const panel = document.getElementById('validation-panel');
            const body = document.getElementById('validation-panel-body');
            const badge = document.getElementById('validation-status-badge');
            const fixBtn = document.getElementById('btn-fix-layout');
            if (!panel || !body) return;

            panel.classList.remove('critical');
            panel.classList.add('active', 'passed');
            badge.textContent = 'Passed';
            body.innerHTML = `
                <div class="validation-pass-note">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
                    <span>No visual layout issues detected. Spacing, overlaps, and edge routing look good.</span>
                </div>`;
            if (fixBtn) fixBtn.style.display = 'none';
            lastValidationFeedback = null;
            validationPanelActive = true;
            updateRightPanelVisibility();
        }

        async function autoRetryWithVlm(imageData, feedback) {
            if (isSending) return;
            const provider = document.getElementById('provider-select').value;
            const sendBtn = document.getElementById('send-btn');
            const input = document.getElementById('chat-input');
            const fixMessage = 'Fix visual layout issues from validation — improve spacing, edge routing, and remove overlaps.';

            isSending = true;
            sendBtn.disabled = true;
            input.disabled = true;
            updateSendStopButtons();
            hideValidationBanner();
            setTyping(true);
            setComposerRunStatus('Auto-fixing layout from validation…', 'running');
            setDiagramStatus('Auto-fixing layout from validation…');
            document.getElementById('diagram-loading').classList.add('active');

            chatMessages.push({ role: 'user', content: fixMessage });
            appendChatBubble('user', fixMessage);

            chatAbortController = new AbortController();
            runStoppedByUser = false;

            try {
                const promptSettings = getPromptSettings();
                const liveXml = await exportDiagramXml();
                const xmlForRequest = (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);

                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    signal: chatAbortController.signal,
                    body: JSON.stringify({
                        messages: chatMessages,
                        provider,
                        validation_provider: promptSettings.validation_provider,
                        platform: selectedPlatform,
                        diagram_mode: getDiagramMode(),
                        current_architecture: currentArchitecture,
                        previous_architecture: previousArchitecture,
                        current_xml: xmlForRequest,
                        previous_xml: previousXml,
                        minimal_style: promptSettings.minimal_style,
                        custom_system_message: promptSettings.custom_system_message,
                        validation_feedback: feedback,
                        image_data: imageData,
                    }),
                });
                const data = await res.json();
                mergeServerLogs(data.logs);
                if (!res.ok) throw new Error(data.detail || 'Server error');
                if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

                const assistantText = data.assistant_message || 'Diagram updated from validation feedback.';
                chatMessages.push({ role: 'assistant', content: assistantText });
                appendChatBubble('assistant', assistantText, data.suggestions || []);

                currentXml = data.xml;
                previousXml = xmlForRequest;
                previousArchitecture = currentArchitecture;
                if (data.edit_mode) {
                    currentArchitecture = null;
                } else {
                    currentArchitecture = data.architecture || null;
                }
                document.getElementById('xml-output').textContent = data.xml;
                document.getElementById('xml-toggle').classList.add('active');

                if (data.validation?.feedback && data.validation?.vlm && !data.validation.vlm.valid) {
                    showValidationBanner(data.validation.vlm, data.validation.feedback);
                } else if (data.validation?.structural && !data.validation.structural.valid) {
                    showValidationBanner(data.validation.structural, data.validation.feedback);
                }

                loadDiagramFromResponse(data);
                setActionButtonsEnabled(true);
                finishChatRun({
                    outcome: 'success',
                    message: 'Layout fix complete — diagram updated.',
                });
            } catch (err) {
                if (err.name === 'AbortError' || runStoppedByUser) {
                    chatMessages.pop();
                    appendVerboseLog('validation', 'VLM auto-retry stopped by user', 'warn');
                    finishChatRun({
                        outcome: 'stopped',
                        message: 'Stopped — layout fix cancelled.',
                    });
                } else {
                    appendVerboseLog('validation', `VLM auto-retry failed: ${err.message}`, 'error');
                    chatMessages.pop();
                    showError(err.message || 'Auto-fix failed');
                    finishChatRun({
                        outcome: 'error',
                        message: err.message || 'Auto-fix failed.',
                    });
                }
            } finally {
                chatAbortController = null;
                runStoppedByUser = false;
                isSending = false;
                sendBtn.disabled = false;
                input.disabled = false;
                updateSendStopButtons();
            }
        }

        async function fixLayoutIssues() {
            if (!lastValidationFeedback && !currentArchitecture) return;
            const input = document.getElementById('chat-input');
            input.value = 'Fix the layout issues identified in validation — improve spacing, edge routing, and remove overlaps.';
            await sendMessage({ validationFeedback: lastValidationFeedback });
        }

        function renderExampleChips() {
            const container = document.getElementById('example-chips');
            container.innerHTML = '';
            (EXAMPLE_PROMPTS[selectedPlatform] || EXAMPLE_PROMPTS.aws).forEach(text => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'example-chip';
                btn.textContent = text;
                btn.onclick = () => {
                    document.getElementById('chat-input').value = text;
                    autoResizeChatInput();
                    sendMessage();
                };
                container.appendChild(btn);
            });
        }

        function normalizeSuggestion(text) {
            return (text || '').trim().toLowerCase().replace(/\s+/g, ' ');
        }

        function registerAppliedSuggestions(texts) {
            (texts || []).forEach(text => {
                const normalized = normalizeSuggestion(text);
                const exists = appliedSuggestionTexts.some(item => normalizeSuggestion(item) === normalized);
                if (!exists) appliedSuggestionTexts.push(text);
            });
        }

        function isSuggestionApplied(text) {
            const normalized = normalizeSuggestion(text);
            return appliedSuggestionTexts.some(item => normalizeSuggestion(item) === normalized);
        }

        function parseImplementedSuggestions(text) {
            const trimmed = (text || '').trim();
            if (!/^Implement these suggestions:/i.test(trimmed)) return [];
            return trimmed.split('\n')
                .slice(1)
                .map(line => line.replace(/^\d+\.\s*/, '').trim())
                .filter(Boolean);
        }

        function getAppliedSuggestionsPayload() {
            return appliedSuggestionTexts.slice();
        }

        function updateSuggestionWrapActions(suggWrap) {
            const pending = suggWrap.querySelectorAll('.item-checkbox:not(:disabled)');
            const actionRow = suggWrap.querySelector('.suggestion-actions');
            if (actionRow) actionRow.hidden = pending.length === 0;
            const selectAll = suggWrap.querySelector('.suggestion-select-all');
            if (selectAll) {
                selectAll.disabled = pending.length === 0;
                if (pending.length === 0) selectAll.checked = false;
            }
        }

        function markSuggestionWrapApplied(suggWrap, texts) {
            if (!suggWrap || !texts?.length) return;
            const appliedSet = new Set(texts.map(normalizeSuggestion));
            suggWrap.querySelectorAll('.chat-suggestion-item').forEach(label => {
                const text = label.dataset.suggestionText || '';
                if (!appliedSet.has(normalizeSuggestion(text))) return;
                label.classList.add('is-applied');
                const cb = label.querySelector('.item-checkbox');
                if (cb) { cb.checked = false; cb.disabled = true; }
                const badge = label.querySelector('.suggestion-applied-badge');
                if (badge) badge.hidden = false;
            });
            updateSuggestionWrapActions(suggWrap);
        }

        function markAllMatchingSuggestionsApplied(texts) {
            if (!texts?.length) return;
            document.querySelectorAll('.chat-suggestions').forEach(wrap => markSuggestionWrapApplied(wrap, texts));
        }

        function setSuggestionPanelsBusy(busy) {
            document.querySelectorAll('.chat-suggestions').forEach(wrap => {
                wrap.querySelectorAll('.suggestion-actions button, .suggestion-checkbox').forEach(el => {
                    if (el.closest('.chat-suggestion-item.is-applied')) return;
                    el.disabled = busy;
                });
            });
        }

        function hideWelcome() {
            const welcome = document.getElementById('chat-welcome');
            if (welcome) welcome.remove();
        }

        function appendChatBubble(role, content, suggestions = null) {
            hideWelcome();
            const container = document.getElementById('chat-messages');
            const msg = document.createElement('div');
            msg.className = `chat-message ${role}`;

            const bubble = document.createElement('div');
            bubble.className = 'chat-bubble';
            bubble.textContent = content;
            msg.appendChild(bubble);

            if (role === 'assistant' && suggestions && suggestions.length) {
                const suggWrap = document.createElement('div');
                suggWrap.className = 'chat-suggestions';
                suggWrap._suggestions = suggestions.slice();

                const header = document.createElement('div');
                header.className = 'chat-suggestions-header';
                const title = document.createElement('div');
                title.className = 'chat-suggestions-title';
                title.textContent = 'Suggestions';
                header.appendChild(title);

                const selectAllLabel = document.createElement('label');
                selectAllLabel.className = 'chat-suggestions-select-all';
                const selectAllCb = document.createElement('input');
                selectAllCb.type = 'checkbox';
                selectAllCb.className = 'suggestion-checkbox suggestion-select-all';
                selectAllCb.title = 'Select all pending suggestions';
                const selectAllText = document.createElement('span');
                selectAllText.textContent = 'Select all';
                selectAllLabel.appendChild(selectAllCb);
                selectAllLabel.appendChild(selectAllText);
                header.appendChild(selectAllLabel);
                suggWrap.appendChild(header);

                const suggList = document.createElement('div');
                suggList.className = 'chat-suggestions-list';
                suggestions.forEach((s, idx) => {
                    const alreadyApplied = isSuggestionApplied(s);
                    const label = document.createElement('label');
                    label.className = 'chat-suggestion-item' + (alreadyApplied ? ' is-applied' : '');
                    label.dataset.suggestionText = s;

                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.className = 'suggestion-checkbox item-checkbox';
                    cb.dataset.index = String(idx);
                    cb.disabled = alreadyApplied;

                    const span = document.createElement('span');
                    span.textContent = s;

                    const badge = document.createElement('span');
                    badge.className = 'suggestion-applied-badge';
                    badge.textContent = 'Applied';
                    badge.hidden = !alreadyApplied;

                    cb.addEventListener('change', () => {
                        const pending = suggList.querySelectorAll('.item-checkbox:not(:disabled)');
                        const checked = suggList.querySelectorAll('.item-checkbox:not(:disabled):checked');
                        selectAllCb.checked = pending.length > 0 && checked.length === pending.length;
                        selectAllCb.indeterminate = checked.length > 0 && checked.length < pending.length;
                    });

                    label.appendChild(cb);
                    label.appendChild(span);
                    label.appendChild(badge);
                    suggList.appendChild(label);
                });
                suggWrap.appendChild(suggList);

                selectAllCb.addEventListener('change', () => {
                    suggList.querySelectorAll('.item-checkbox:not(:disabled)').forEach(cb => {
                        cb.checked = selectAllCb.checked;
                    });
                    selectAllCb.indeterminate = false;
                });

                const actionRow = document.createElement('div');
                actionRow.className = 'suggestion-actions';
                const selBtn = document.createElement('button');
                selBtn.type = 'button';
                selBtn.className = 'suggestion-btn';
                selBtn.textContent = 'Apply Selected';
                selBtn.onclick = () => {
                    const checked = suggWrap.querySelectorAll('.item-checkbox:checked:not(:disabled)');
                    if (checked.length === 0) { showError('Select at least one suggestion to apply.'); return; }
                    const texts = Array.from(checked).map(cb => suggWrap._suggestions[Number(cb.dataset.index)]);
                    implementSuggestions(texts, suggWrap);
                };
                const allBtn = document.createElement('button');
                allBtn.type = 'button';
                allBtn.className = 'suggestion-btn suggestion-btn-primary';
                allBtn.textContent = 'Apply All';
                allBtn.onclick = () => {
                    const pending = suggWrap._suggestions.filter(item => !isSuggestionApplied(item));
                    if (pending.length === 0) { showError('All suggestions are already applied.'); return; }
                    implementSuggestions(pending, suggWrap);
                };
                actionRow.appendChild(selBtn);
                actionRow.appendChild(allBtn);
                suggWrap.appendChild(actionRow);
                updateSuggestionWrapActions(suggWrap);
                msg.appendChild(suggWrap);
            }

            const meta = document.createElement('div');
            meta.className = 'chat-meta';
            meta.textContent = role === 'user' ? 'You' : 'Assistant · ' + formatLogTime();
            msg.appendChild(meta);

            container.appendChild(msg);
            container.scrollTop = container.scrollHeight;
        }

        function implementSuggestions(suggestions, suggWrap) {
            if (!suggestions || suggestions.length === 0) return;
            registerAppliedSuggestions(suggestions);
            markSuggestionWrapApplied(suggWrap, suggestions);
            const msg = 'Implement these suggestions:\n' + suggestions.map((s, i) => `${i + 1}. ${s}`).join('\n');
            document.getElementById('chat-input').value = msg;
            autoResizeChatInput();
            sendMessage();
        }

        function setTyping(active) {
            document.getElementById('chat-typing').classList.toggle('active', active);
            if (active) {
                const container = document.getElementById('chat-messages');
                container.scrollTop = container.scrollHeight;
            }
        }

        function autoResizeChatInput() {
            const el = document.getElementById('chat-input');
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 180) + 'px';
        }

        function clearChat() {
            if (isSending) return;
            chatMessages = [];
            appliedSuggestionTexts = [];
            currentArchitecture = null;
            previousArchitecture = null;
            currentXml = '';
            previousXml = null;
            attachmentContext = '';
            attachmentImageData = null;
            hasImageAttachment = false;
            const preview = document.getElementById('attachment-preview');
            if (preview) { preview.hidden = true; preview.textContent = ''; }
            hideValidationBanner();
            setActionButtonsEnabled(false);
            document.getElementById('xml-output').textContent = '';
            document.getElementById('xml-toggle').classList.remove('active');
            document.getElementById('xml-panel').classList.remove('open');
            hideError();
            setDiagramStatus('Waiting for generation');
            setComposerRunStatus('Ready — describe an architecture or ask for changes', 'info');
            document.getElementById('diagram-placeholder').classList.remove('hidden');

            const container = document.getElementById('chat-messages');
            container.innerHTML = `
                <div id="chat-welcome" class="chat-welcome">
                    <h3>Describe your architecture</h3>
                    <p>Chat naturally to create or refine diagrams — like next-ai-draw-io. Follow up to add services, change layout, or fix connections.</p>
                    <div class="example-chips" id="example-chips"></div>
                </div>
                <div id="chat-typing" class="chat-typing">
                    <span></span><span></span><span></span>
                </div>`;
            renderExampleChips();
            appendVerboseLog('ui', 'Chat cleared', 'info');
        }

        function formatLogTime(isoOrDate) {
            const d = isoOrDate ? new Date(isoOrDate) : new Date();
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

        function formatDetails(details) {
            if (!details) return '';
            if (typeof details === 'string') return details;
            return JSON.stringify(details, null, 2);
        }

        function updateLogCount() {
            const badge = document.getElementById('log-count-badge');
            badge.textContent = `${logEntryCount} ${logEntryCount === 1 ? 'entry' : 'entries'}`;
        }

        function appendVerboseLog(step, message, level = 'info', details = null, timestamp = null) {
            if (!verboseLogging) return;

            const body = document.getElementById('verbose-log-body');
            const empty = body.querySelector('.log-empty');
            if (empty) empty.remove();

            const entry = document.createElement('div');
            entry.className = `log-entry ${level}`;

            const head = document.createElement('div');
            head.className = 'log-entry-head';

            const meta = document.createElement('div');
            meta.className = 'log-entry-meta';

            const timeEl = document.createElement('span');
            timeEl.className = 'log-time';
            timeEl.textContent = formatLogTime(timestamp);

            const stepEl = document.createElement('span');
            stepEl.className = 'log-step';
            stepEl.textContent = step;

            meta.appendChild(timeEl);
            meta.appendChild(stepEl);

            const msgEl = document.createElement('span');
            msgEl.className = 'log-message';
            msgEl.textContent = message;

            head.appendChild(meta);
            head.appendChild(msgEl);
            entry.appendChild(head);

            if (details) {
                const detailEl = document.createElement('div');
                detailEl.className = 'log-details';
                detailEl.textContent = formatDetails(details);
                entry.appendChild(detailEl);
            }

            body.appendChild(entry);
            body.scrollTop = body.scrollHeight;

            logEntryCount += 1;
            updateLogCount();
        }

        function clearVerboseLogs() {
            const body = document.getElementById('verbose-log-body');
            body.innerHTML = '<div class="log-empty">Log cleared. Generate a diagram to see new pipeline steps.</div>';
            logEntryCount = 0;
            updateLogCount();
        }

        function updateRightPanelVisibility() {
            const open = verboseLogging || validationPanelActive;
            document.getElementById('log-panel').classList.toggle('active', open);
        }

        function setLogPanelOpen(open) {
            verboseLogging = open;
            document.getElementById('verbose-toggle').checked = open;
            localStorage.setItem(VERBOSE_STORAGE_KEY, open ? '1' : '0');
            updateRightPanelVisibility();
        }

        function toggleVerbosePanel() {
            const open = document.getElementById('verbose-toggle').checked;
            setLogPanelOpen(open);
            if (open) {
                appendVerboseLog('ui', 'Pipeline log panel opened', 'info');
            }
        }

        function closeLogPanel() {
            hideValidationBanner();
            setLogPanelOpen(false);
        }

        function mergeServerLogs(logs) {
            (logs || []).forEach(entry => {
                appendVerboseLog(
                    entry.step || 'server',
                    entry.message || '',
                    entry.level || 'info',
                    entry.details || null,
                    entry.timestamp || null
                );
            });
        }

        function initVerboseLogging() {
            const open = localStorage.getItem(VERBOSE_STORAGE_KEY) === '1';
            setLogPanelOpen(open);
            updateLogCount();
        }

        function setDiagramStatus(text) {
            document.getElementById('diagram-status').textContent = text;
        }

        function setActionButtonsEnabled(enabled) {
            ['btn-download', 'btn-copy', 'btn-open'].forEach(id => {
                document.getElementById(id).disabled = !enabled;
            });
        }

        function ensureDrawioFrame() {
            const iframe = document.getElementById('drawio-frame');
            const theme = localStorage.getItem(THEME_STORAGE_KEY) || 'kennedy';
            const themeSelect = document.getElementById('drawio-theme-select');
            if (themeSelect) themeSelect.value = theme;
            if (!iframe.src) {
                iframe.src = getDrawioUrl(theme);
                bindDrawioMessages(iframe);
            }
            return iframe;
        }

        function bindDrawioMessages(iframe) {
            if (messageHandler) {
                window.removeEventListener('message', messageHandler);
            }

            messageHandler = (evt) => {
                if (evt.origin !== DRAWIO_ORIGIN) return;
                if (!evt.data || typeof evt.data !== 'string') return;

                let msg;
                try {
                    msg = JSON.parse(evt.data);
                } catch {
                    return;
                }

                if (msg.event === 'configure') {
                    appendVerboseLog('drawio', 'Embed sent configure response', 'info');
                    iframe.contentWindow.postMessage(JSON.stringify({
                        action: 'configure',
                        config: {
                            defaultFonts: ['Helvetica', 'Arial', 'sans-serif'],
                            defaultLibraries: 'aws4;gcp2;azure2',
                            css: '.geDiagramContainer { background: #ffffff !important; }'
                        }
                    }), DRAWIO_ORIGIN);
                }

                if (msg.event === 'init') {
                    drawioReady = true;
                    appendVerboseLog('drawio', 'Editor initialized', 'success');
                    if (pendingMermaid) {
                        loadMermaidIntoEditor(pendingMermaid);
                        pendingMermaid = null;
                    } else if (pendingXml) {
                        loadDiagramIntoEditor(pendingXml);
                        pendingXml = null;
                    }
                }

                if (msg.event === 'load') {
                    appendVerboseLog('drawio', 'Diagram loaded in editor', 'success', {
                        page: msg.page,
                        bounds: msg.bounds || null
                    });
                    document.getElementById('diagram-loading').classList.remove('active');
                    document.getElementById('diagram-placeholder').classList.add('hidden');
                    setDiagramStatus('Diagram loaded — pan and zoom to explore');
                    if (!isSending) {
                        setComposerRunStatus('Ready — ask for changes or try another prompt', 'success');
                    }
                    setTimeout(() => runVisualValidation(), 600);
                }

                if (msg.event === 'export' && msg.data) {
                    if (pendingXmlExport) {
                        pendingXmlExport.resolve(msg.data);
                        pendingXmlExport = null;
                        return;
                    }
                    if (pendingPngExport) {
                        let dataUrl = msg.data;
                        if (!dataUrl.startsWith('data:')) {
                            dataUrl = 'data:image/png;base64,' + btoa(
                                unescape(encodeURIComponent(msg.data))
                            );
                        }
                        pendingPngExport.resolve(dataUrl);
                        pendingPngExport = null;
                    }
                }
            };

            window.addEventListener('message', messageHandler);
        }

        function loadMermaidIntoEditor(mermaid) {
            const iframe = ensureDrawioFrame();
            const payload = {
                action: 'import',
                format: 'mermaid',
                data: mermaid,
                fit: 1,
                maxFitScale: 1.25,
                border: 20
            };

            if (!drawioReady) {
                pendingMermaid = mermaid;
                pendingXml = null;
                appendVerboseLog('drawio', 'Waiting for editor init before Mermaid import', 'info', {
                    mermaid_chars: mermaid.length
                });
                return;
            }

            appendVerboseLog('drawio', 'Importing Mermaid diagram into editor', 'info', {
                mermaid_chars: mermaid.length
            });
            iframe.contentWindow.postMessage(JSON.stringify(payload), DRAWIO_ORIGIN);
        }

        function loadDiagramFromResponse(data) {
            if (data.diagram_type === 'mermaid' && data.mermaid) {
                currentMermaid = data.mermaid;
                currentXml = '';
                document.getElementById('xml-output').textContent = data.mermaid;
                loadMermaidIntoEditor(data.mermaid);
                document.getElementById('diagram-loading').classList.add('active');
                setDiagramStatus('Loading Mermaid into Draw.io…');
                return;
            }
            currentMermaid = '';
            if (data.xml) {
                document.getElementById('xml-output').textContent = data.xml;
                loadDiagramIntoEditor(data.xml);
            }
        }

        function loadDiagramIntoEditor(xml) {
            const iframe = ensureDrawioFrame();
            const payload = {
                action: 'load',
                xml: xml,
                autosave: 0,
                fit: 1,
                maxFitScale: 1.25,
                border: 20
            };
            if (isAutolayoutOnLoadEnabled() && !/container=1|shape=mxgraph\.aws4\.group/.test(xml) && !/id="flow_notes"/.test(xml)) {
                payload.layout = 'horizontalFlow';
            }

            if (!drawioReady) {
                pendingXml = xml;
                pendingMermaid = null;
                appendVerboseLog('drawio', 'Waiting for editor init before load', 'info', {
                    xml_chars: xml.length,
                    layout: payload.layout || null
                });
                return;
            }

            appendVerboseLog('drawio', 'Sending diagram to editor', 'info', {
                xml_chars: xml.length,
                fit: payload.fit,
                maxFitScale: payload.maxFitScale,
                layout: payload.layout || null
            });
            iframe.contentWindow.postMessage(JSON.stringify(payload), DRAWIO_ORIGIN);
        }

        function applyLayoutToEditor() {
            const iframe = ensureDrawioFrame();
            if (!drawioReady) {
                appendVerboseLog('drawio', 'Editor not ready for layout', 'warn');
                return;
            }
            const payload = {
                action: 'layout',
                layouts: [{
                    layout: 'mxHierarchicalLayout',
                    config: { orientation: 'west', resizeParent: true }
                }]
            };
            appendVerboseLog('drawio', 'Applying hierarchical layout in editor', 'info');
            iframe.contentWindow.postMessage(JSON.stringify(payload), DRAWIO_ORIGIN);
        }

        function setPlatformTabsBusy(busy) {
            document.querySelectorAll('.platform-card').forEach(card => {
                card.classList.toggle('is-busy', busy);
                card.setAttribute('aria-busy', busy ? 'true' : 'false');
            });
        }

        function shouldRegenerateOnPlatformSwitch() {
            if (isSending) return false;
            if (currentMermaid && !currentArchitecture) return false;
            if (currentArchitecture?.nodes?.length > 0) return true;
            if (currentXml && currentXml.length > 300 && !currentMermaid) return true;
            return false;
        }

        function applyPlatformUi(platform) {
            selectedPlatform = platform;
            document.querySelectorAll('.platform-card').forEach(card => {
                const isActive = card.getAttribute('data-platform') === platform;
                card.classList.toggle('active', isActive);
                card.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });

            const root = document.documentElement;
            const chatInput = document.getElementById('chat-input');

            if (platform === 'aws') {
                root.style.setProperty('--accent', '#ED7100');
                root.style.setProperty('--accent-glow', 'rgba(237, 113, 0, 0.2)');
                root.style.setProperty('--accent-hover', '#ff8c2a');
            } else if (platform === 'gcp') {
                root.style.setProperty('--accent', '#4285F4');
                root.style.setProperty('--accent-glow', 'rgba(66, 133, 244, 0.2)');
                root.style.setProperty('--accent-hover', '#669df6');
            } else if (platform === 'azure') {
                root.style.setProperty('--accent', '#0078D4');
                root.style.setProperty('--accent-glow', 'rgba(0, 120, 212, 0.2)');
                root.style.setProperty('--accent-hover', '#2b90f5');
            } else {
                root.style.setProperty('--accent', '#8C4FFF');
                root.style.setProperty('--accent-glow', 'rgba(140, 79, 255, 0.2)');
                root.style.setProperty('--accent-hover', '#a67cff');
            }

            chatInput.placeholder = CHAT_PLACEHOLDERS[platform] || CHAT_PLACEHOLDERS.aws;
            if (document.getElementById('example-chips')) {
                renderExampleChips();
            }
        }

        async function regenerateForPlatform(previousPlatform) {
            const targetLabel = selectedPlatform.toUpperCase();
            const message =
                `Convert this architecture from ${previousPlatform} to ${selectedPlatform}. ` +
                `Keep the same topology and flow; use equivalent ${selectedPlatform} services and icons only.`;
            await sendMessage({
                skipInput: true,
                platformConvert: true,
                previousPlatform,
                message,
                showUserBubble: true,
            });
        }

        function selectPlatform(platform) {
            if (platform === selectedPlatform) return;
            if (isSending) return;

            const previousPlatform = selectedPlatform;
            applyPlatformUi(platform);

            if (shouldRegenerateOnPlatformSwitch()) {
                regenerateForPlatform(previousPlatform);
            } else if (currentMermaid && !currentArchitecture) {
                setDiagramStatus('Platform applies to new cloud diagrams');
            }
        }

        function setProgress(percent) {
            const track = document.getElementById('progress-track');
            track.classList.add('active');
            document.getElementById('progress-bar').style.width = percent + '%';
            if (percent >= 100) {
                setTimeout(() => track.classList.remove('active'), 1200);
            }
        }

        function showError(msg) {
            const banner = document.getElementById('error-banner');
            document.getElementById('error-msg').textContent = msg;
            banner.classList.add('active');
        }

        function hideError() {
            document.getElementById('error-banner').classList.remove('active');
        }

        async function sendMessage(options = {}) {
            const input = document.getElementById('chat-input');
            const text = (options.skipInput ? options.message : input.value).trim();
            const provider = document.getElementById('provider-select').value;
            const sendBtn = document.getElementById('send-btn');
            const validationFeedback = options.validationFeedback || null;

            if (!text || isSending) {
                if (!text && !validationFeedback) showError('Please enter a message.');
                return;
            }

            hideError();
            hideValidationBanner();
            vlmAutoRetryCount = 0;
            if (isListening) {
                voiceManuallyStopping = true;
                try { speechRecognition.stop(); } catch (_) {}
            }
            isSending = true;
            sendBtn.disabled = true;
            input.disabled = true;
            updateSendStopButtons();
            setPlatformTabsBusy(true);
            setSuggestionPanelsBusy(true);

            const parsedApplied = parseImplementedSuggestions(text);
            if (parsedApplied.length) {
                registerAppliedSuggestions(parsedApplied);
                markAllMatchingSuggestionsApplied(parsedApplied);
            }

            if (!options.skipInput) {
                chatMessages.push({ role: 'user', content: text });
                appendChatBubble('user', text);
                input.value = '';
                autoResizeChatInput();
            } else if (options.platformConvert && text) {
                chatMessages.push({ role: 'user', content: text });
                if (options.showUserBubble !== false) {
                    appendChatBubble('user', text);
                }
            }

            setProgress(10);
            setTyping(true);
            setComposerRunStatus(
                options.platformConvert
                    ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
                    : 'Generating diagram…',
                'running'
            );
            setDiagramStatus(
                options.platformConvert
                    ? `Regenerating for ${selectedPlatform.toUpperCase()}…`
                    : 'Analyzing architecture...'
            );
            document.getElementById('diagram-loading').classList.add('active');

            chatAbortController = new AbortController();
            runStoppedByUser = false;

            if (verboseLogging) {
                appendVerboseLog('client', 'Sending chat message', 'info', {
                    platform: selectedPlatform,
                    provider,
                    turn: chatMessages.length,
                    has_existing_diagram: !!currentArchitecture,
                });
            }

            try {
                const promptSettings = getPromptSettings();
                const liveXml = await exportDiagramXml();
                const xmlForRequest = (liveXml && liveXml.length > 200) ? liveXml : (currentXml || null);
                previousXml = currentXml || null;

                let contextText = attachmentContext || '';
                const urlMatch = text.match(/https?:\/\/[^\s<>"']+/);
                if (urlMatch && !contextText.includes(urlMatch[0])) {
                    try {
                        contextText = (contextText ? contextText + '\n' : '') + await parseUrlContext(urlMatch[0]);
                    } catch (urlErr) {
                        appendVerboseLog('client', `URL parse skipped: ${urlErr.message}`, 'warn');
                    }
                }

                appendVerboseLog('client', 'POST /chat', 'info', {
                    ...promptSettings,
                    has_current_xml: !!xmlForRequest,
                    xml_chars: (xmlForRequest || '').length,
                });
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    signal: chatAbortController.signal,
                    body: JSON.stringify({
                        messages: chatMessages,
                        provider,
                        validation_provider: promptSettings.validation_provider,
                        platform: selectedPlatform,
                        diagram_mode: options.platformConvert ? 'cloud' : getDiagramMode(),
                        platform_convert: !!options.platformConvert,
                        previous_platform: options.previousPlatform || null,
                        current_architecture: currentArchitecture,
                        previous_architecture: previousArchitecture,
                        current_xml: xmlForRequest,
                        previous_xml: previousXml,
                        minimal_style: promptSettings.minimal_style,
                        custom_system_message: promptSettings.custom_system_message,
                        validation_feedback: validationFeedback,
                        context_text: contextText || null,
                        image_data: attachmentImageData,
                        has_image_input: hasImageAttachment,
                        applied_suggestions: getAppliedSuggestionsPayload(),
                    }),
                });

                setProgress(45);
                setDiagramStatus('Building diagram XML...');

                const data = await res.json();
                mergeServerLogs(data.logs);

                if (!res.ok) throw new Error(data.detail || 'Server error');
                if (data.status !== 'success') throw new Error(data.detail || 'Unknown error');

                const assistantText = data.assistant_message || 'Diagram updated.';
                chatMessages.push({ role: 'assistant', content: assistantText });

                appendVerboseLog('client', 'Server response received', 'success', {
                    suggestions: (data.suggestions || []).length,
                    diagram_type: data.diagram_type || 'cloud',
                    xml_chars: (data.xml || '').length,
                    mermaid_chars: (data.mermaid || '').length,
                    edit_mode: !!data.edit_mode,
                });

                currentXml = data.xml || '';
                previousXml = xmlForRequest;
                previousArchitecture = currentArchitecture;
                if (data.edit_mode) {
                    currentArchitecture = null;
                } else {
                    currentArchitecture = data.architecture || null;
                }
                document.getElementById('xml-toggle').classList.add('active');

                attachmentContext = '';
                attachmentImageData = null;
                hasImageAttachment = false;
                const attachPreview = document.getElementById('attachment-preview');
                if (attachPreview) { attachPreview.hidden = true; attachPreview.textContent = ''; }

              
                if (data.validation?.feedback) {
                    showValidationBanner(data.validation.structural || data.validation.vlm, data.validation.feedback);
                } else if (data.validation?.structural && !data.validation.structural.valid) {
                    showValidationBanner(data.validation.structural, data.validation.feedback);
                }

                setTyping(false);
                appendChatBubble('assistant', assistantText, data.suggestions || []);

                setProgress(75);
                setDiagramStatus('Loading into Draw.io...');
                loadDiagramFromResponse(data);
                setActionButtonsEnabled(true);
                setProgress(100);
                finishChatRun({
                    outcome: 'success',
                    message: 'Done — diagram updated. Pan and zoom in the preview.',
                });
                appendVerboseLog('client', 'Chat turn complete', 'success');
            } catch (err) {
                console.error(err);
                if (options.platformConvert && options.previousPlatform) {
                    applyPlatformUi(options.previousPlatform);
                }
                if (err.name === 'AbortError' || runStoppedByUser) {
                    if (!options.skipInput || !options.platformConvert) chatMessages.pop();
                    appendVerboseLog('client', 'Request stopped by user', 'warn');
                    finishChatRun({
                        outcome: 'stopped',
                        message: 'Stopped — edit your message and send again when ready.',
                    });
                } else {
                    if (!options.skipInput || !options.platformConvert) chatMessages.pop();
                    setTyping(false);
                    appendVerboseLog('client', err.message || 'Request failed', 'error');
                    showError(err.message || 'Request failed. Check the server logs.');
                    finishChatRun({
                        outcome: 'error',
                        message: err.message || 'Request failed.',
                    });
                }
            } finally {
                chatAbortController = null;
                runStoppedByUser = false;
                isSending = false;
                sendBtn.disabled = false;
                input.disabled = false;
                updateSendStopButtons();
                setPlatformTabsBusy(false);
                setSuggestionPanelsBusy(false);
                input.focus();
            }
        }

        function downloadXML() {
            if (!currentXml) return;
            const blob = new Blob([currentXml], { type: 'application/xml' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `architecture_${selectedPlatform}.drawio`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        function copyXML() {
            if (!currentXml) return;
            navigator.clipboard.writeText(currentXml).then(() => {
                const toast = document.getElementById('copy-toast');
                toast.classList.add('show');
                setTimeout(() => toast.classList.remove('show'), 2000);
            });
        }

        function openInDrawio() {
            if (!currentXml) return;
            const encoded = encodeURIComponent(currentXml);
            window.open(`https://app.diagrams.net/#R${encoded}`, '_blank');
        }

        function toggleXML() {
            const panel = document.getElementById('xml-panel');
            const toggle = document.getElementById('xml-toggle');
            panel.classList.toggle('open');
            toggle.textContent = panel.classList.contains('open')
                ? '▾ Hide Draw.io XML source'
                : '▸ View Draw.io XML source';
        }

        // ── Sidebar resizer ──────────────────────
        (function initSidebarResizer() {
            const resizer = document.getElementById('sidebar-resizer');
            const sidebar = document.querySelector('.sidebar');
            let startX, startWidth;

            function onMouseDown(e) {
                startX = e.clientX;
                startWidth = sidebar.getBoundingClientRect().width;
                resizer.classList.add('resizing');
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';
                document.documentElement.style.pointerEvents = 'none';
                resizer.style.pointerEvents = 'all';
                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
                e.preventDefault();
            }

            function onMouseMove(e) {
                const delta = e.clientX - startX;
                const newWidth = Math.min(Math.max(startWidth + delta, parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'))), parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-max-width')));
                document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
            }

            function onMouseUp() {
                resizer.classList.remove('resizing');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                document.documentElement.style.pointerEvents = '';
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            }

            resizer.addEventListener('mousedown', onMouseDown);

            // Keyboard support
            resizer.addEventListener('keydown', (e) => {
                const step = e.shiftKey ? 20 : 10;
                const min = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-min-width'));
                const max = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-max-width'));
                let current = parseFloat(getComputedStyle(sidebar).width);
                if (e.key === 'ArrowLeft') {
                    current = Math.max(min, current - step);
                    document.documentElement.style.setProperty('--sidebar-width', current + 'px');
                    e.preventDefault();
                } else if (e.key === 'ArrowRight') {
                    current = Math.min(max, current + step);
                    document.documentElement.style.setProperty('--sidebar-width', current + 'px');
                    e.preventDefault();
                }
            });
        })();

        // Boot Draw.io iframe immediately so it is ready when the user sends a message
        initVerboseLogging();
        initPromptSettings();
        document.getElementById('vlm-validation-toggle').addEventListener('change', updateValidationProviderVisibility);
        initDiagramMode();
        initAutolayoutSetting();
        hideValidationBanner();
        updateSendStopButtons();
        setComposerRunStatus('Ready — describe an architecture or ask for changes', 'info');
        loadTemplates();
        initVoiceInput();
        ensureDrawioFrame();
        const bootTheme = localStorage.getItem(THEME_STORAGE_KEY) || 'kennedy';
        appendVerboseLog('drawio', 'Embed iframe created', 'info', { url: getDrawioUrl(bootTheme) });
        applyPlatformUi('aws');
        renderExampleChips();

        const chatInput = document.getElementById('chat-input');
        chatInput.addEventListener('input', autoResizeChatInput);
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeSettings();
        });