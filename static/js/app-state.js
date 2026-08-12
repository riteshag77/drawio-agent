/**
 * A5: Application state manager — immutable state updates with observer pattern.
 *
 * Replaces 30 unscoped global variables with a single AppState object and
 * immutable state updates. Subscribers are notified of changes.
 *
 * Usage:
 *   import { AppStateManager } from './app-state.js';
 *   
 *   AppStateManager.subscribe(() => render());
 *   AppStateManager.setSending(true);
 *   AppStateManager.addChatMessage(msg);
 */

// Type definitions (JSDoc for TypeScript-like types in vanilla JS)

/**
 * @typedef {Object} Project
 * @property {string} id
 * @property {string} title
 * @property {string} [description]
 * @property {Diagram[]} diagrams
 * @property {number} createdAt
 */

/**
 * @typedef {Object} Diagram
 * @property {string} id
 * @property {string} title
 * @property {string} [description]
 * @property {string} [xml]
 * @property {Object} [architecture]
 * @property {number} createdAt
 * @property {number} updatedAt
 */

/**
 * @typedef {Object} ChatMessage
 * @property {string} id
 * @property {string} role
 * @property {string} text
 * @property {number} timestamp
 */

/**
 * @typedef {Object} AttachmentInfo
 * @property {string} filename
 * @property {'loading'|'ready'|'error'} status
 * @property {string} [message]
 * @property {boolean} [truncated]
 */

/**
 * @typedef {Object} AppState
 * @property {Project|null} project
 * @property {Diagram|null} currentDiagram
 * @property {boolean} isSending
 * @property {boolean} drawioReady
 * @property {'aws'|'gcp'|'azure'} selectedPlatform
 * @property {boolean} sidebarCollapsed
 * @property {number} sidebarWidth
 * @property {ChatMessage[]} chatMessages
 * @property {AttachmentInfo[]} attachments
 * @property {Object} [currentArchitecture]
 * @property {string} [currentXml]
 * @property {Object[]} [pendingInterviewAnswers]
 * @property {string} [customSystemMessage]
 */

class AppStateManager {
    constructor() {
        /** @type {AppState} */
        this.state = this.initialState();
        
        /** @type {Set<Function>} */
        this.listeners = new Set();
    }

    /**
     * Create initial app state.
     * @returns {AppState}
     */
    initialState() {
        return {
            // Project / Diagram
            project: null,
            currentDiagram: null,

            // Chat
            isSending: false,
            chatMessages: [],
            customSystemMessage: '',

            // Attachments
            attachments: [],

            // Draw.io
            drawioReady: false,
            currentXml: '',
            currentArchitecture: null,

            // UI
            selectedPlatform: 'aws',
            sidebarCollapsed: false,
            sidebarWidth: 400,

            // Interview
            pendingInterviewAnswers: null,
        };
    }

    // ──────────────────────────────────────────────────────────────────
    // State Updates (Immutable)
    // ──────────────────────────────────────────────────────────────────

    /**
     * Set the current project.
     * @param {Project|null} project
     */
    setProject(project) {
        this.state = { ...this.state, project };
        this.notifyListeners();
    }

    /**
     * Set the current diagram.
     * @param {Diagram|null} diagram
     */
    setCurrentDiagram(diagram) {
        this.state = { ...this.state, currentDiagram: diagram };
        this.notifyListeners();
    }

    /**
     * Update current XML (when user edits in draw.io).
     * @param {string} xml
     */
    setCurrentXml(xml) {
        this.state = { ...this.state, currentXml: xml };
        this.notifyListeners();
    }

    /**
     * Set sending state (true when request in flight).
     * @param {boolean} isSending
     */
    setSending(isSending) {
        this.state = { ...this.state, isSending };
        this.notifyListeners();
    }

    /**
     * Add a chat message.
     * @param {ChatMessage} message
     */
    addChatMessage(message) {
        this.state = {
            ...this.state,
            chatMessages: [...this.state.chatMessages, message],
        };
        this.notifyListeners();
    }

    /**
     * Clear chat history.
     */
    clearChatMessages() {
        this.state = { ...this.state, chatMessages: [] };
        this.notifyListeners();
    }

    /**
     * Add an attachment.
     * @param {AttachmentInfo} attachment
     */
    addAttachment(attachment) {
        this.state = {
            ...this.state,
            attachments: [...this.state.attachments, attachment],
        };
        this.notifyListeners();
    }

    /**
     * Remove an attachment by filename.
     * @param {string} filename
     */
    removeAttachment(filename) {
        this.state = {
            ...this.state,
            attachments: this.state.attachments.filter(a => a.filename !== filename),
        };
        this.notifyListeners();
    }

    /**
     * Clear all attachments.
     */
    clearAttachments() {
        this.state = { ...this.state, attachments: [] };
        this.notifyListeners();
    }

    /**
     * Set draw.io ready state.
     * @param {boolean} ready
     */
    setDrawioReady(ready) {
        this.state = { ...this.state, drawioReady: ready };
        this.notifyListeners();
    }

    /**
     * Set selected cloud platform.
     * @param {'aws'|'gcp'|'azure'} platform
     */
    setSelectedPlatform(platform) {
        this.state = { ...this.state, selectedPlatform: platform };
        this.notifyListeners();
    }

    /**
     * Toggle sidebar collapsed state.
     */
    toggleSidebar() {
        this.state = {
            ...this.state,
            sidebarCollapsed: !this.state.sidebarCollapsed,
        };
        this.notifyListeners();
    }

    /**
     * Set sidebar width.
     * @param {number} width
     */
    setSidebarWidth(width) {
        this.state = { ...this.state, sidebarWidth: width };
        this.notifyListeners();
    }

    /**
     * Set custom system message.
     * @param {string} message
     */
    setCustomSystemMessage(message) {
        this.state = { ...this.state, customSystemMessage: message };
        this.notifyListeners();
    }

    /**
     * Set pending interview answers.
     * @param {Object[]|null} answers
     */
    setPendingInterviewAnswers(answers) {
        this.state = { ...this.state, pendingInterviewAnswers: answers };
        this.notifyListeners();
    }

    /**
     * Reset to initial state.
     */
    reset() {
        this.state = this.initialState();
        this.notifyListeners();
    }

    // ──────────────────────────────────────────────────────────────────
    // Subscriptions
    // ──────────────────────────────────────────────────────────────────

    /**
     * Subscribe to state changes.
     * @param {Function} listener
     * @returns {Function} unsubscribe function
     */
    subscribe(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    /**
     * Notify all subscribers of state change.
     */
    notifyListeners() {
        this.listeners.forEach(listener => {
            try {
                listener(this.state);
            } catch (error) {
                console.error('Listener error:', error);
            }
        });
    }

    // ──────────────────────────────────────────────────────────────────
    // Persistence (localStorage)
    // ──────────────────────────────────────────────────────────────────

    /**
     * Save state to localStorage.
     */
    saveToStorage() {
        try {
            // Only persist UI state, not chat history
            const uiState = {
                selectedPlatform: this.state.selectedPlatform,
                sidebarCollapsed: this.state.sidebarCollapsed,
                sidebarWidth: this.state.sidebarWidth,
                customSystemMessage: this.state.customSystemMessage,
            };
            localStorage.setItem('drawio-agent-ui-state', JSON.stringify(uiState));
        } catch (error) {
            console.warn('Failed to save state:', error);
        }
    }

    /**
     * Load state from localStorage.
     */
    loadFromStorage() {
        try {
            const stored = localStorage.getItem('drawio-agent-ui-state');
            if (stored) {
                const uiState = JSON.parse(stored);
                this.state = { ...this.state, ...uiState };
                this.notifyListeners();
            }
        } catch (error) {
            console.warn('Failed to load state:', error);
        }
    }
}

// Export singleton instance
export const AppStateManager = new AppStateManager();
