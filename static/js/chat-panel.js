/**
 * A5: Example component — demonstrates using AppStateManager.
 *
 * This pattern replaces the old global-state approach with:
 * - Explicit dependencies (receives state manager)
 * - Reactive rendering (subscribes to state changes)
 * - Cleanup (unsubscribe on destroy)
 *
 * Usage:
 *   const chatPanel = new ChatPanel(document.getElementById('chat'));
 *   chatPanel.render();
 *   // Later: chatPanel.destroy();
 */

import { AppStateManager } from './app-state.js';

export class ChatPanel {
    /**
     * Initialize chat panel component.
     * @param {HTMLElement} container
     */
    constructor(container) {
        this.container = container;
        this.unsubscribe = null;
    }

    /**
     * Render component and subscribe to state changes.
     */
    render() {
        // Re-render when state changes
        this.unsubscribe = AppStateManager.subscribe((state) => {
            this.renderContent(state);
        });

        // Initial render
        this.renderContent(AppStateManager.state);
    }

    /**
     * Render chat UI based on current state.
     * @private
     * @param {AppState} state
     */
    renderContent(state) {
        if (!state.chatMessages || state.chatMessages.length === 0) {
            this.container.innerHTML = `
                <div class="chat-empty">
                    <p>No messages yet. Start a conversation to generate a diagram.</p>
                </div>
            `;
            return;
        }

        const messagesHtml = state.chatMessages
            .map(msg => this.renderMessage(msg))
            .join('');

        this.container.innerHTML = `
            <div class="chat-messages">
                ${messagesHtml}
            </div>
            <div class="chat-input">
                <input type="text" placeholder="Describe your architecture..." id="message-input" />
                <button id="send-button" ${state.isSending ? 'disabled' : ''}>
                    ${state.isSending ? 'Generating...' : 'Send'}
                </button>
            </div>
        `;

        // Attach event listeners
        const input = this.container.querySelector('#message-input');
        const button = this.container.querySelector('#send-button');

        if (button) {
            button.addEventListener('click', () => this.onSendMessage(input.value));
        }
        if (input) {
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.onSendMessage(input.value);
                }
            });
        }
    }

    /**
     * Render a single chat message.
     * @private
     * @param {ChatMessage} message
     * @returns {string}
     */
    renderMessage(message) {
        const className = `message message-${message.role}`;
        return `
            <div class="${className}">
                <div class="message-text">${this.escapeHtml(message.text)}</div>
                <div class="message-time">${new Date(message.timestamp).toLocaleTimeString()}</div>
            </div>
        `;
    }

    /**
     * Handle send message.
     * @private
     * @param {string} text
     */
    async onSendMessage(text) {
        if (!text.trim()) return;

        // Add user message to state
        AppStateManager.addChatMessage({
            id: `msg-${Date.now()}`,
            role: 'user',
            text: text.trim(),
            timestamp: Date.now(),
        });

        // Clear input
        const input = this.container.querySelector('#message-input');
        if (input) input.value = '';

        // Mark as sending
        AppStateManager.setSending(true);

        try {
            // Call /chat/stream API
            const response = await fetch('/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    provider: null, // Use default
                }),
            });

            // Handle SSE stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value);
                const lines = buffer.split('\n');

                for (let i = 0; i < lines.length - 1; i++) {
                    const line = lines[i];
                    if (line.startsWith('data: ')) {
                        try {
                            const json = JSON.parse(line.slice(6));
                            this.handleStreamEvent(json);
                        } catch (error) {
                            console.error('Failed to parse SSE event:', error);
                        }
                    }
                }

                buffer = lines[lines.length - 1];
            }
        } catch (error) {
            console.error('Chat error:', error);
            AppStateManager.addChatMessage({
                id: `msg-${Date.now()}`,
                role: 'assistant',
                text: `Error: ${error.message}`,
                timestamp: Date.now(),
            });
        } finally {
            AppStateManager.setSending(false);
        }
    }

    /**
     * Handle SSE event from stream.
     * @private
     * @param {Object} event
     */
    handleStreamEvent(event) {
        if (event.status === 'error') {
            AppStateManager.addChatMessage({
                id: `msg-${Date.now()}`,
                role: 'system',
                text: `Error: ${event.error}`,
                timestamp: Date.now(),
            });
        } else if (event.intermediate_xml) {
            // Update diagram XML
            AppStateManager.setCurrentXml(event.intermediate_xml);

            // Add assistant message
            AppStateManager.addChatMessage({
                id: `msg-${Date.now()}`,
                role: 'assistant',
                text: event.step_log?.message || 'Diagram updated',
                timestamp: Date.now(),
            });
        }
    }

    /**
     * Escape HTML to prevent XSS.
     * @private
     * @param {string} text
     * @returns {string}
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Clean up component (unsubscribe from state).
     */
    destroy() {
        if (this.unsubscribe) {
            this.unsubscribe();
        }
        this.container.innerHTML = '';
    }
}
