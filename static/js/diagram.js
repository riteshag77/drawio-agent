function getDrawioUrl(theme) {
    const ui = DRAWIO_THEMES.includes(theme) ? theme : 'kennedy';
    return `${DRAWIO_ORIGIN}/?embed=1&ui=${ui}&spin=1&proto=json&configure=1&libs=aws4;gcp2;azure2&nav=1&saveAndExit=0&noSaveBtn=1&noExitBtn=1`;
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
            clearDrawioLoadTimer();
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
        }

        if (msg.event === 'error') {
            clearDrawioLoadTimer();
            appendVerboseLog('drawio', 'Draw.io error: ' + (msg.message || 'Unknown'), 'error', msg);
            setComposerRunStatus('Draw.io render error: ' + (msg.message || 'Check the diagram'), 'error');
            document.getElementById('diagram-loading').classList.remove('active');
        }

        if (msg.event === 'dialog' && msg.button === 'cancel') {
            clearDrawioLoadTimer();
            appendVerboseLog('drawio', 'Diagram load cancelled by editor', 'warning');
            document.getElementById('diagram-loading').classList.remove('active');
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

let drawioLoadTimer = null;

function clearDrawioLoadTimer() {
    if (drawioLoadTimer) {
        clearTimeout(drawioLoadTimer);
        drawioLoadTimer = null;
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

    clearDrawioLoadTimer();
    drawioLoadTimer = setTimeout(() => {
        drawioLoadTimer = null;
        appendVerboseLog('drawio', 'Diagram load timed out — the XML may have errors', 'error', { xml_chars: xml.length });
        setComposerRunStatus('Diagram render timed out — XML may have errors', 'error');
        document.getElementById('diagram-loading').classList.remove('active');
    }, 15000);
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
