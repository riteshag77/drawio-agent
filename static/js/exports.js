/** Diagram export helpers (XML download / PNG / clipboard). */

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
