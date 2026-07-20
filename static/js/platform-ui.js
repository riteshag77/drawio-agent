/** Platform UI — card selection, accent colours, regenerate-on-switch. */

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

function shouldRegenerateOnPlatformSwitch() {
    if (isSending) return false;
    if (currentMermaid && !currentArchitecture) return false;
    if (currentArchitecture?.nodes?.length > 0) return true;
    if (currentXml && currentXml.length > 300 && !currentMermaid) return true;
    return false;
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
    if (shouldRegenerateOnPlatformSwitch()) {
        if (!confirmAction(`Convert the current diagram to ${platform.toUpperCase()}? This regenerates the architecture.`)) {
            return;
        }
    }
    applyPlatformUi(platform);
    if (typeof updateContextChip === 'function') updateContextChip();
    renderPatternGallery();

    if (shouldRegenerateOnPlatformSwitch()) {
        regenerateForPlatform(previousPlatform);
    } else if (currentMermaid && !currentArchitecture) {
        setDiagramStatus('Platform applies to new cloud diagrams');
    }
}

function setPlatformTabsBusy(busy) {
    document.querySelectorAll('.platform-card').forEach(card => {
        card.classList.toggle('is-busy', busy);
        card.setAttribute('aria-busy', busy ? 'true' : 'false');
    });
}
