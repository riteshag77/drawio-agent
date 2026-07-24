/** Platform UI — card selection, accent colours, regenerate-on-switch. */

function applyPlatformUi(platform) {
    selectedPlatform = platform;
    document.querySelectorAll('.platform-card').forEach(card => {
        const isActive = card.getAttribute('data-platform') === platform;
        card.classList.toggle('active', isActive);
        card.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    const chatInput = document.getElementById('chat-input');

    // --accent/--accent-hover/--accent-glow are owned by the selected app
    // theme (static/themes/*.css) — platform selection no longer overrides
    // them with a hardcoded per-cloud color (that used to be set here as an
    // inline style on <html>, which always beat every theme's accent
    // regardless of specificity, silently defeating the whole theme system).

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
