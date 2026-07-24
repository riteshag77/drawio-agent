/** Attachment handling — chips, file select, clear, URL parse helpers. */

const MAX_ATTACHMENT_CLIENT_BYTES = 15 * 1024 * 1024;

function attachmentChipIcon(status) {
    if (status === 'loading') return '⏳';
    if (status === 'error') return '⚠️';
    if (status === 'diagram') return '🗂️';
    return '📄';
}

function renderAttachmentChips() {
    const preview = document.getElementById('attachment-preview');
    if (!preview) return;
    preview.innerHTML = '';
    if (!attachments.length) {
        preview.hidden = true;
        return;
    }
    preview.hidden = false;
    attachments.forEach((att) => {
        const chip = document.createElement('div');
        const chipKind = att.status === 'ready' && att.kind === 'diagram' ? 'diagram' : att.status;
        chip.className = `attachment-chip attachment-chip-${chipKind === 'diagram' ? 'ready' : att.status}`;
        if (att.status === 'error') chip.title = att.message;
        else if (att.kind === 'diagram') chip.title = 'Draw.io diagram — will be used as the base for generation/edits';
        else if (att.truncated) chip.title = 'Only the first part of this file was used (content truncated).';

        const icon = document.createElement('span');
        icon.className = 'attachment-chip-icon';
        icon.textContent = attachmentChipIcon(att.kind === 'diagram' && att.status === 'ready' ? 'diagram' : att.status);
        chip.appendChild(icon);

        const name = document.createElement('span');
        name.className = 'attachment-chip-name';
        name.textContent = att.filename;
        chip.appendChild(name);

        if (att.status === 'error') {
            const msg = document.createElement('span');
            msg.className = 'attachment-chip-msg';
            msg.textContent = att.message;
            chip.appendChild(msg);
        } else if (att.kind === 'diagram') {
            const note = document.createElement('span');
            note.className = 'attachment-chip-note';
            note.textContent = 'diagram';
            chip.appendChild(note);
        } else if (att.truncated) {
            const note = document.createElement('span');
            note.className = 'attachment-chip-note';
            note.textContent = 'truncated';
            chip.appendChild(note);
        }
        preview.appendChild(chip);
    });
}

function clearAttachments() {
    attachmentContext = '';
    attachmentDocuments = [];
    attachmentDiagramXml = null;
    attachments = [];
    renderAttachmentChips();
}

async function handleFileSelect(event) {
    const files = Array.from(event.target.files || []).slice(0, 3);
    event.target.value = '';
    if (!files.length) return;

    for (const file of files) {
        const entry = { filename: file.name, status: 'loading', message: '', truncated: false, kind: null };
        attachments.push(entry);
        renderAttachmentChips();

        if (file.size > MAX_ATTACHMENT_CLIENT_BYTES) {
            entry.status = 'error';
            entry.message = `Too large (${(file.size / 1048576).toFixed(1)} MB) — the limit is 15 MB.`;
            renderAttachmentChips();
            showToast(`Couldn't read ${file.name}: ${entry.message}`, 'error');
            continue;
        }

        try {
            const formData = new FormData();
            formData.append('file', file);
            const res = await fetch('/attachments/extract', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.status !== 'success') {
                entry.status = 'error';
                entry.message = data.detail || 'Could not read this file.';
                showToast(`Couldn't read ${file.name}: ${entry.message}`, 'error');
            } else if (data.kind === 'text') {
                entry.status = 'ready';
                entry.kind = 'text';
                entry.truncated = !!data.truncated;
                attachmentContext += `\n--- File: ${data.filename} ---\n${data.content}\n`;
            } else if (data.kind === 'diagram') {
                entry.status = 'ready';
                entry.kind = 'diagram';
                attachmentDiagramXml = data.content;
                currentXml = data.content;
                currentMermaid = '';
                currentArchitecture = null;
                const xmlOut = document.getElementById('xml-output');
                if (xmlOut) xmlOut.textContent = data.content;
                if (typeof loadDiagramIntoEditor === 'function') {
                    document.getElementById('diagram-loading')?.classList.add('active');
                    if (typeof setDiagramStatus === 'function') setDiagramStatus(`Loaded ${data.filename}`);
                    loadDiagramIntoEditor(data.content);
                }
                if (typeof setActionButtonsEnabled === 'function') setActionButtonsEnabled(true);
                showToast(`Diagram ready: ${data.filename}`, 'success');
            } else {
                entry.status = 'ready';
                entry.kind = 'vision';
                attachmentDocuments.push({ filename: data.filename, mime: data.mime, data: data.data });
            }
        } catch (err) {
            entry.status = 'error';
            entry.message = 'Network error while reading this file.';
            showToast(`Couldn't read ${file.name}: ${entry.message}`, 'error');
        }
        renderAttachmentChips();
    }
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
