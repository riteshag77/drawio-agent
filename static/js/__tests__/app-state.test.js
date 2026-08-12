/**
 * A5 + A10: Frontend test example — unit testing with Jest.
 *
 * Demonstrates testing the new app-state manager and components.
 *
 * To run:
 *   npm test static/js/__tests__/app-state.test.js
 */

import { AppStateManager } from '../app-state.js';

describe('AppStateManager', () => {
    let unsubscribes = [];

    beforeEach(() => {
        // Reset state and listeners before each test
        AppStateManager.listeners.clear();
        AppStateManager.state = AppStateManager.initialState();
    });

    afterEach(() => {
        // Clean up subscriptions
        unsubscribes.forEach(fn => fn());
        unsubscribes = [];
    });

    // ──────────────────────────────────────────────────────────────────
    // State Updates
    // ──────────────────────────────────────────────────────────────────

    test('setSending updates state', () => {
        expect(AppStateManager.state.isSending).toBe(false);

        AppStateManager.setSending(true);

        expect(AppStateManager.state.isSending).toBe(true);
    });

    test('setCurrentXml updates diagram XML', () => {
        const xml = '<mxfile>...</mxfile>';
        AppStateManager.setCurrentXml(xml);

        expect(AppStateManager.state.currentXml).toBe(xml);
    });

    test('addChatMessage appends to messages', () => {
        const msg = {
            id: 'msg-1',
            role: 'user',
            text: 'Hello',
            timestamp: Date.now(),
        };

        AppStateManager.addChatMessage(msg);

        expect(AppStateManager.state.chatMessages).toHaveLength(1);
        expect(AppStateManager.state.chatMessages[0]).toEqual(msg);
    });

    test('clearChatMessages empties message list', () => {
        AppStateManager.addChatMessage({
            id: 'msg-1',
            role: 'user',
            text: 'Hello',
            timestamp: Date.now(),
        });

        expect(AppStateManager.state.chatMessages).toHaveLength(1);

        AppStateManager.clearChatMessages();

        expect(AppStateManager.state.chatMessages).toHaveLength(0);
    });

    test('addAttachment adds attachment', () => {
        const attachment = {
            filename: 'architecture.pdf',
            status: 'ready',
        };

        AppStateManager.addAttachment(attachment);

        expect(AppStateManager.state.attachments).toHaveLength(1);
        expect(AppStateManager.state.attachments[0]).toEqual(attachment);
    });

    test('removeAttachment removes by filename', () => {
        AppStateManager.addAttachment({ filename: 'a.pdf', status: 'ready' });
        AppStateManager.addAttachment({ filename: 'b.pdf', status: 'ready' });

        AppStateManager.removeAttachment('a.pdf');

        expect(AppStateManager.state.attachments).toHaveLength(1);
        expect(AppStateManager.state.attachments[0].filename).toBe('b.pdf');
    });

    // ──────────────────────────────────────────────────────────────────
    // Subscriptions
    // ──────────────────────────────────────────────────────────────────

    test('subscribe notifies listener on state change', () => {
        const listener = jest.fn();
        unsubscribes.push(AppStateManager.subscribe(listener));

        AppStateManager.setSending(true);

        expect(listener).toHaveBeenCalled();
        expect(listener).toHaveBeenCalledWith(expect.objectContaining({
            isSending: true,
        }));
    });

    test('unsubscribe stops notifications', () => {
        const listener = jest.fn();
        const unsubscribe = AppStateManager.subscribe(listener);

        AppStateManager.setSending(true);
        expect(listener).toHaveBeenCalledTimes(1);

        unsubscribe();

        AppStateManager.setSending(false);
        expect(listener).toHaveBeenCalledTimes(1); // Not called again
    });

    test('multiple listeners all receive updates', () => {
        const listener1 = jest.fn();
        const listener2 = jest.fn();
        unsubscribes.push(AppStateManager.subscribe(listener1));
        unsubscribes.push(AppStateManager.subscribe(listener2));

        AppStateManager.setSelectedPlatform('gcp');

        expect(listener1).toHaveBeenCalled();
        expect(listener2).toHaveBeenCalled();
    });

    // ──────────────────────────────────────────────────────────────────
    // Immutability
    // ──────────────────────────────────────────────────────────────────

    test('state updates create new state object (no mutation)', () => {
        const originalState = AppStateManager.state;

        AppStateManager.setSending(true);

        expect(AppStateManager.state).not.toBe(originalState);
        expect(originalState.isSending).toBe(false); // Original unchanged
    });

    test('adding message doesn\'t mutate original array', () => {
        const originalMessages = AppStateManager.state.chatMessages;

        AppStateManager.addChatMessage({
            id: 'msg-1',
            role: 'user',
            text: 'Hello',
            timestamp: Date.now(),
        });

        expect(AppStateManager.state.chatMessages).not.toBe(originalMessages);
        expect(originalMessages).toHaveLength(0); // Original unchanged
    });

    // ──────────────────────────────────────────────────────────────────
    // Storage
    // ──────────────────────────────────────────────────────────────────

    test('saveToStorage persists UI state', () => {
        // Mock localStorage
        global.localStorage = {
            data: {},
            getItem(key) {
                return this.data[key];
            },
            setItem(key, value) {
                this.data[key] = value;
            },
            removeItem(key) {
                delete this.data[key];
            },
        };

        AppStateManager.setSelectedPlatform('azure');
        AppStateManager.setSidebarWidth(500);
        AppStateManager.saveToStorage();

        const stored = JSON.parse(localStorage.getItem('drawio-agent-ui-state'));
        expect(stored.selectedPlatform).toBe('azure');
        expect(stored.sidebarWidth).toBe(500);
    });

    test('loadFromStorage restores UI state', () => {
        global.localStorage = {
            data: {
                'drawio-agent-ui-state': JSON.stringify({
                    selectedPlatform: 'gcp',
                    sidebarWidth: 600,
                }),
            },
            getItem(key) {
                return this.data[key];
            },
            setItem(key, value) {
                this.data[key] = value;
            },
        };

        AppStateManager.loadFromStorage();

        expect(AppStateManager.state.selectedPlatform).toBe('gcp');
        expect(AppStateManager.state.sidebarWidth).toBe(600);
    });
});
