/** Diagram export helpers (XML download / PNG / SVG / clipboard). */

async function downloadSVG() {
    try {
        setDiagramStatus('Exporting SVG…');
        const svg = await exportDiagramSvg();
        if (!svg) throw new Error('SVG export returned empty');
        const blob = new Blob([svg], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'architecture.svg';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('SVG downloaded', 'success');
        setDiagramStatus('SVG exported');
    } catch (err) {
        showToast(err.message || 'SVG export failed', 'error');
        setDiagramStatus('SVG export failed');
    }
}

async function downloadPNG() {
    try {
        setDiagramStatus('Exporting PNG…');
        const dataUrl = await exportDiagramPng();
        if (!dataUrl) throw new Error('PNG export returned empty');
        const a = document.createElement('a');
        a.href = dataUrl;
        a.download = 'architecture.png';
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('PNG downloaded', 'success');
        setDiagramStatus('PNG exported');
    } catch (err) {
        showToast(err.message || 'PNG export failed', 'error');
        setDiagramStatus('PNG export failed');
    }
}
