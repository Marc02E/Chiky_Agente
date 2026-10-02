/**
 * Chiky — Personal AI Secretary
 * Frontend application with conversation management, themes, and markdown.
 */
(function () {
    'use strict';

    function renderMarkdown(text) {
        if (typeof window.marked === 'undefined' || typeof window.DOMPurify === 'undefined') {
            return escapeHtml(text);
        }
        try {
            var raw = window.marked.parse(text, { breaks: true, gfm: true });
            return window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
        } catch (_) {
            return escapeHtml(text);
        }
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    var THEMES = ['dark', 'light', 'warm', 'blue', 'contrast'];
    var DEFAULT_THEME = 'dark';
    var THEME_KEY = 'chiky_theme';

    function applyTheme(theme) {
        if (!THEMES.includes(theme)) theme = DEFAULT_THEME;
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
        var radios = document.querySelectorAll('#theme-options input[type="radio"]');
        radios.forEach(function (r) {
            r.checked = r.value === theme;
        });
    }

    function loadTheme() {
        var saved = localStorage.getItem(THEME_KEY) || DEFAULT_THEME;
        applyTheme(saved);
    }

    var state = {
        currentSessionId: null,
        sessions: [],
        sending: false,
        providerMode: null,
        currentModel: null,
        pendingDeleteId: null,
        pendingRenameId: null,
        searchQuery: '',
        attachedFiles: [],
        // Pending approval state: stores what the agent wants to execute
        pendingApproval: null,  // {originalMessage, originalFiles, sessionId, approvalRequest}
        // FASE T: multi-provider model state
        allProviders: [],
        selectedProvider: null,
        selectedModel: null,
        connectivity: { online: true, latency_ms: 0 },
        modelMode: 'recommended',
        // FASE V: provider transparency state
        modelStatus: 'unknown',
        fallbackActive: false,
        fallbackFrom: null,
        verifiedCapabilities: {},
        // FASE X: routing mode state
        routingMode: 'automatic',
        // FASE AB.6: latest execution provenance (from fallback_info)
        lastExecutedProvider: null,
        lastExecutedModel: null,
        lastExecutionStatus: null,
        lastLatencyMs: null,
        lastFallbackChain: [],
    };

    var dom = {
        sessionList: document.getElementById('session-list'),
        sidebarEmpty: document.getElementById('sidebar-empty'),
        chatEmpty: document.getElementById('chat-empty'),
        chatMessages: document.getElementById('chat-messages'),
        chatInputArea: document.getElementById('chat-input-area'),
        chatForm: document.getElementById('chat-form'),
        chatInput: document.getElementById('chat-input'),
        btnSend: document.getElementById('btn-send'),
        btnNewChat: document.getElementById('btn-new-chat'),
        btnSettings: document.getElementById('btn-settings'),
        btnAttach: document.getElementById('btn-attach'),
        fileInput: document.getElementById('file-input'),
        fileChips: document.getElementById('file-chips'),
        providerBadge: document.getElementById('provider-badge'),
        modelSelectorWrapper: document.getElementById('model-selector-wrapper'),
        modelSelector: document.getElementById('model-selector'),
        errorToast: document.getElementById('error-toast'),
        errorMessage: document.getElementById('error-toast-message'),
        errorClose: document.getElementById('error-toast-close'),
        searchInput: document.getElementById('search-input'),
        settingsModal: document.getElementById('settings-modal'),
        btnCloseSettings: document.getElementById('btn-close-settings'),
        themeOptions: document.getElementById('theme-options'),
        confirmModal: document.getElementById('confirm-modal'),
        btnCancelDelete: document.getElementById('btn-cancel-delete'),
        btnConfirmDelete: document.getElementById('btn-confirm-delete'),
        renameModal: document.getElementById('rename-modal'),
        renameInput: document.getElementById('rename-input'),
        btnCloseRename: document.getElementById('btn-close-rename'),
        btnCancelRename: document.getElementById('btn-cancel-rename'),
        btnConfirmRename: document.getElementById('btn-confirm-rename'),
        // FASE T: model management DOM
        modelQuickSelect: document.getElementById('model-quick-select'),
        modelAdvancedPanel: document.getElementById('model-advanced-panel'),
        modelList: document.getElementById('model-list'),
        btnVerifyModel: document.getElementById('btn-verify-model'),
        btnRefreshModels: document.getElementById('btn-refresh-models'),
        modelStatusBar: document.getElementById('model-status-bar'),
        modelStatusText: document.getElementById('model-status-text'),
        modelConnectivity: document.getElementById('model-connectivity'),
        // FASE AB.6: first-run wizard
        wizardModal: document.getElementById('wizard-modal'),
        btnWizardFinish: document.getElementById('btn-wizard-finish'),
        btnWizardSkip: document.getElementById('btn-wizard-skip'),
        wizardSaveStatus: document.getElementById('wizard-save-status'),
        wizardProviderGrid: document.getElementById('wizard-provider-grid'),
        wizardManualSelection: document.getElementById('wizard-manual-selection'),
        wizardManualProvider: document.getElementById('wizard-manual-provider'),
        wizardManualModel: document.getElementById('wizard-manual-model'),
        // FASE AB.6: settings additions
        ollamaStatus: document.getElementById('ollama-status'),
        opencodeStatus: document.getElementById('opencode-status'),
        btnTestOllama: document.getElementById('btn-test-ollama'),
        btnTestOpencode: document.getElementById('btn-test-opencode'),
    };

    var API = '/api/v1';

    // FASE RELEASE (chat availability): cap chat requests so a dead or silent
    // backend can never leave the UI in an infinite "Thinking…" state. Operators
    // /E2E may override per page via window.CHIKY_CHAT_TIMEOUT_MS.
    var CHAT_REQUEST_TIMEOUT_MS = 180000;
    function chatRequestTimeoutMs() {
        var custom = window.CHIKY_CHAT_TIMEOUT_MS;
        return (typeof custom === 'number' && custom > 0) ? custom : CHAT_REQUEST_TIMEOUT_MS;
    }

    async function apiCall(method, path, body, extraHeaders, options) {
        var headers = Object.assign({ 'Content-Type': 'application/json' }, extraHeaders || {});
        var opts = { method: method, headers: headers };
        if (body !== undefined) opts.body = JSON.stringify(body);
        var timeoutId = null;
        var controller = null;
        if (options && options.timeoutMs) {
            controller = new AbortController();
            timeoutId = setTimeout(function () { controller.abort(); }, options.timeoutMs);
            opts.signal = controller.signal;
        }
        var res;
        try {
            res = await fetch(API + path, opts);
            if (res.status === 204) return null;
            if (!res.ok) {
                var msg = 'Something went wrong';
                try {
                    var err = await res.json();
                    msg = err.message || err.detail || msg;
                } catch (_) {}
                throw new Error(msg);
            }
            return res.json();
        } finally {
            if (timeoutId) clearTimeout(timeoutId);
        }
    }

    function chatFailureMessage(err, sessionId) {
        if (err && err.name === 'AbortError') {
            console.error('[chat] request timed out', { sessionId: sessionId, timeoutMs: chatRequestTimeoutMs() });
            return 'The request did not receive a response before the timeout. Nothing was retried automatically — you can send the message again.';
        }
        if (err instanceof TypeError) {
            console.error('[chat] network error while sending message', { sessionId: sessionId, error: String(err) });
            return 'Could not reach the backend. Check that the server is running, then try again.';
        }
        console.error('[chat] send error', { sessionId: sessionId, error: err && err.message });
        return 'Request failed: ' + (err.message || 'The backend returned an error.');
    }

    function formatTime(isoString) {
        try {
            var d = new Date(isoString);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (_) { return ''; }
    }

    function formatRelativeDate(isoString) {
        try {
            var d = new Date(isoString);
            var now = new Date();
            var diffMs = now - d;
            var diffDays = Math.floor(diffMs / 86400000);
            if (diffDays === 0) return 'Today';
            if (diffDays === 1) return 'Yesterday';
            if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'long' });
            return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        } catch (_) { return ''; }
    }

    var errorTimer = null;

    function showError(msg) {
        dom.errorMessage.textContent = msg;
        dom.errorToast.classList.remove('hidden');
        if (errorTimer) clearTimeout(errorTimer);
        errorTimer = setTimeout(hideError, 8000);
    }

    function hideError() {
        dom.errorToast.classList.add('hidden');
        if (errorTimer) { clearTimeout(errorTimer); errorTimer = null; }
    }

    function renderSessions() {
        dom.sessionList.querySelectorAll('.session-item').forEach(function (el) { el.remove(); });

        var filtered = state.sessions.filter(function (s) {
            if (!state.searchQuery) return true;
            var title = s.title || 'New conversation';
            return title.toLowerCase().includes(state.searchQuery.toLowerCase());
        });

        if (filtered.length === 0) {
            dom.sidebarEmpty.classList.remove('hidden');
            return;
        }
        dom.sidebarEmpty.classList.add('hidden');

        filtered.forEach(function (s) {
            var el = document.createElement('div');
            el.className = 'session-item' + (s.session_id === state.currentSessionId ? ' active' : '');
            el.setAttribute('data-session-id', s.session_id);

            var content = document.createElement('div');
            content.className = 'session-item-content';

            var title = document.createElement('div');
            title.className = 'session-item-title';
            title.textContent = s.title || 'New conversation';

            var meta = document.createElement('div');
            meta.className = 'session-item-meta';
            var dateStr = formatRelativeDate(s.updated_at || s.created_at);
            var countStr = s.request_count ? s.request_count + ' msg' : '';
            meta.textContent = [dateStr, countStr].filter(Boolean).join(' · ');

            content.appendChild(title);
            content.appendChild(meta);
            el.appendChild(content);

            var menuBtn = document.createElement('button');
            menuBtn.className = 'session-menu-btn';
            menuBtn.setAttribute('aria-label', 'Session options');
            menuBtn.setAttribute('title', 'Options');
            menuBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>';
            menuBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                openContextMenu(e, s.session_id);
            });
            el.appendChild(menuBtn);

            el.addEventListener('click', function () { openSession(s.session_id); });
            dom.sessionList.appendChild(el);
        });
    }

    var activeContextMenu = null;

    function closeContextMenu() {
        if (activeContextMenu) {
            activeContextMenu.remove();
            activeContextMenu = null;
        }
        document.removeEventListener('click', closeContextMenu);
    }

    function openContextMenu(event, sessionId) {
        closeContextMenu();

        var menu = document.createElement('div');
        menu.className = 'context-menu';
        menu.setAttribute('role', 'menu');

        var renameItem = document.createElement('button');
        renameItem.className = 'context-menu-item';
        renameItem.setAttribute('role', 'menuitem');
        renameItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg><span>Rename</span>';
        renameItem.addEventListener('click', function () {
            closeContextMenu();
            openRenameModal(sessionId);
        });

        var deleteItem = document.createElement('button');
        deleteItem.className = 'context-menu-item danger';
        deleteItem.setAttribute('role', 'menuitem');
        deleteItem.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg><span>Delete</span>';
        deleteItem.addEventListener('click', function () {
            closeContextMenu();
            openDeleteModal(sessionId);
        });

        menu.appendChild(renameItem);
        menu.appendChild(deleteItem);

        var rect = event.currentTarget ? event.currentTarget.getBoundingClientRect() : { left: event.clientX, top: event.clientY, right: event.clientX + 1 };
        menu.style.top = (rect.bottom + 4) + 'px';
        menu.style.left = Math.min(rect.left, window.innerWidth - 180) + 'px';

        document.body.appendChild(menu);
        activeContextMenu = menu;

        setTimeout(function () {
            document.addEventListener('click', closeContextMenu);
        }, 0);
    }

    function openDeleteModal(sessionId) {
        state.pendingDeleteId = sessionId;
        dom.confirmModal.classList.remove('hidden');
    }

    function closeDeleteModal() {
        state.pendingDeleteId = null;
        dom.confirmModal.classList.add('hidden');
    }

    async function confirmDelete() {
        var id = state.pendingDeleteId;
        if (!id) return;
        closeDeleteModal();
        try {
            await apiCall('DELETE', '/ui/sessions/' + id);
            if (state.currentSessionId === id) {
                state.currentSessionId = null;
                showEmpty();
            }
            await loadSessions();
        } catch (err) {
            showError(err.message || 'Failed to delete conversation');
        }
    }

    function openRenameModal(sessionId) {
        state.pendingRenameId = sessionId;
        var session = state.sessions.find(function (s) { return s.session_id === sessionId; });
        dom.renameInput.value = session ? (session.title || '') : '';
        dom.renameModal.classList.remove('hidden');
        setTimeout(function () { dom.renameInput.focus(); dom.renameInput.select(); }, 50);
    }

    function closeRenameModal() {
        state.pendingRenameId = null;
        dom.renameModal.classList.add('hidden');
        dom.renameInput.value = '';
    }

    async function confirmRename() {
        var id = state.pendingRenameId;
        var newTitle = dom.renameInput.value.trim();
        if (!id || !newTitle) {
            showError('Title cannot be empty');
            return;
        }
        closeRenameModal();
        try {
            await apiCall('PATCH', '/ui/sessions/' + id, { title: newTitle });
            await loadSessions();
        } catch (err) {
            showError(err.message || 'Failed to rename conversation');
        }
    }

    function openSettings() { dom.settingsModal.classList.remove('hidden'); }
    function closeSettings() { dom.settingsModal.classList.add('hidden'); }

    function renderMessages(messages) {
        dom.chatMessages.innerHTML = '';
        messages.forEach(function (msg) { appendMessage(msg.role, msg.content, msg.created_at); });
        scrollToBottom();
    }

    function appendMessage(role, content, timestamp, provenance) {
        var el = document.createElement('div');
        el.className = 'message ' + role;

        var label = document.createElement('div');
        label.className = 'message-label';
        label.textContent = role === 'user' ? 'You' : 'Chiky';

        var bubble = document.createElement('div');
        bubble.className = 'message-bubble';
        if (role === 'assistant') {
            bubble.innerHTML = renderMarkdown(content);
        } else {
            bubble.textContent = content;
        }

        el.appendChild(label);
        el.appendChild(bubble);

        // FASE AB.6: per-message provenance ("Ejecutado por: provider · model")
        if (role === 'assistant' && provenance && provenance.executed_provider) {
            var prov = document.createElement('div');
            prov.className = 'message-provenance';
            var execLabel = 'Ejecutado por: ' + provenance.executed_provider + ' · ' + (provenance.executed_model || '');
            if (provenance.status === 'ok') {
                execLabel += ' · OK';
            } else if (provenance.status) {
                execLabel += ' · ' + provenance.status;
            }
            if (typeof provenance.latency_ms === 'number') {
                execLabel += ' · ' + provenance.latency_ms + 'ms';
            }
            if (provenance.fallback_active) {
                var chain = (provenance.fallback_chain || []).join(' → ');
                execLabel += ' · fallback' + (chain ? ' (' + chain + ')' : '');
            }
            prov.textContent = execLabel;
            el.appendChild(prov);
        }

        if (timestamp) {
            var time = document.createElement('div');
            time.className = 'message-time';
            time.textContent = formatTime(timestamp);
            el.appendChild(time);
        }

        dom.chatMessages.appendChild(el);
    }

    function showLoading() {
        var el = document.createElement('div');
        el.className = 'loading-indicator';
        el.id = 'loading-indicator';
        el.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div><span class="loading-text">Thinking…</span>';
        dom.chatMessages.appendChild(el);
        scrollToBottom();
    }

    function hideLoading() {
        var el = document.getElementById('loading-indicator');
        if (el) el.remove();
    }

    function scrollToBottom() {
        requestAnimationFrame(function () { dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight; });
    }

    function showEmpty() {
        dom.chatEmpty.classList.remove('hidden');
        dom.chatMessages.classList.add('hidden');
        dom.chatInputArea.classList.add('hidden');
        renderSessions();
    }

    function showConversation() {
        dom.chatEmpty.classList.add('hidden');
        dom.chatMessages.classList.remove('hidden');
        dom.chatInputArea.classList.remove('hidden');
    }

    async function loadSessions() {
        try {
            var query = state.searchQuery ? '?search=' + encodeURIComponent(state.searchQuery) : '';
            var data = await apiCall('GET', '/ui/sessions' + query);
            state.sessions = data.sessions || [];
            renderSessions();
        } catch (_) {
            showError('Could not load conversations');
        }
    }

    async function openSession(sessionId) {
        state.currentSessionId = sessionId;
        renderSessions();
        showConversation();
        try {
            var data = await apiCall('GET', '/sessions/' + sessionId + '/messages');
            renderMessages(data.messages || []);
        } catch (_) {
            showError('Could not load conversation');
        }
        dom.chatInput.focus();
    }

    function newChat() {
        state.currentSessionId = null;
        state.attachedFiles = [];
        renderFileChips();
        renderSessions();
        showConversation();
        dom.chatMessages.innerHTML = '';
        dom.chatInput.value = '';
        dom.chatInput.focus();
        updateSendButton();
    }

    async function sendMessage(text) {
        if (state.sending || !text.trim()) return;
        state.sending = true;
        dom.btnSend.disabled = true;
        dom.chatInput.value = '';
        var filesToSend = state.attachedFiles.slice();
        state.attachedFiles = [];
        renderFileChips();
        updateSendButton();
        autoResize();

        var sessionId = state.currentSessionId || generateUUID();
        appendMessage('user', text, new Date().toISOString());
        showLoading();
        scrollToBottom();

        try {
            var payload = { input: text };
            if (filesToSend.length > 0) payload.attached_files = filesToSend;
            // Normal messages do NOT carry approval — approval must be explicit per-operation.
            var data = await apiCall('POST', '/sessions/' + sessionId + '/messages', payload, undefined, { timeoutMs: chatRequestTimeoutMs() });
            hideLoading();
            if (!state.currentSessionId) {
                state.currentSessionId = sessionId;
                await loadSessions();
            }
            if (data && data.assistant_message) {
                appendMessage(data.assistant_message.role, data.assistant_message.content, data.assistant_message.created_at, data.fallback_info);
                updateLastExecutionProvenance(data.fallback_info);
            } else if (data && data.status === 'blocked') {
                appendMessage('assistant', 'This request requires approval and has been blocked.', new Date().toISOString());
            } else if (data && data.status === 'failed') {
                appendMessage('assistant', 'The request could not be completed. Please try again.', new Date().toISOString());
            }
            // If the agent needs explicit approval, show the confirmation dialog.
            if (data && data.approval_request) {
                state.pendingApproval = {
                    originalMessage: text,
                    originalFiles: filesToSend,
                    sessionId: sessionId,
                    approvalRequest: data.approval_request,
                };
                showApprovalModal(data.approval_request);
            }
            scrollToBottom();
            await loadSessions();
        } catch (err) {
            hideLoading();
            var userMessage = chatFailureMessage(err, sessionId);
            appendMessage('assistant', userMessage, new Date().toISOString());
            showError(userMessage);
        } finally {
            state.sending = false;
            updateSendButton();
            dom.chatInput.focus();
        }
    }

    function renderFileChips() {
        dom.fileChips.innerHTML = '';
        if (state.attachedFiles.length === 0) {
            dom.fileChips.classList.add('hidden');
            return;
        }
        dom.fileChips.classList.remove('hidden');
        state.attachedFiles.forEach(function (af, idx) {
            var chip = document.createElement('span');
            chip.className = 'file-chip';
            chip.textContent = af.name;
            var removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'file-chip-remove';
            removeBtn.setAttribute('aria-label', 'Remove ' + af.name);
            removeBtn.textContent = '×';
            removeBtn.addEventListener('click', function () {
                state.attachedFiles.splice(idx, 1);
                renderFileChips();
            });
            chip.appendChild(removeBtn);
            dom.fileChips.appendChild(chip);
        });
    }

    function handleFileSelect(file) {
        if (!file) return;
        var isImage = file.type && file.type.indexOf('image/') === 0;
        var MAX_SIZE = 5000;
        var MAX_IMAGE_CHARS = 1500000;
        var reader = new FileReader();
        reader.onload = function (e) {
            var raw = e.target.result;
            var content;
            var isBase64 = false;
            if (isImage) {
                // FASE AB.6: images travel as base64 so the model receives
                // the real bytes (vision), never text-mangled.
                var dataUrl = String(raw);
                var comma = dataUrl.indexOf(',');
                content = comma >= 0 ? dataUrl.substring(comma + 1) : dataUrl;
                isBase64 = true;
                if (content.length > MAX_IMAGE_CHARS) {
                    content = content.substring(0, MAX_IMAGE_CHARS);
                }
            } else {
                content = String(raw);
                if (content.length > MAX_SIZE) {
                    content = content.substring(0, MAX_SIZE);
                }
            }
            state.attachedFiles.push({
                name: file.name,
                content: content,
                size: file.size,
                mime_type: file.type || (isBase64 ? 'image/png' : 'text/plain'),
                is_base64: isBase64,
            });
            renderFileChips();
        };
        reader.onerror = function () { showError('Could not read file: ' + file.name); };
        if (isImage) {
            reader.readAsDataURL(file);
        } else {
            reader.readAsText(file);
        }
        dom.fileInput.value = '';
    }

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    }

    function updateSendButton() {
        dom.btnSend.disabled = state.sending || dom.chatInput.value.trim().length === 0;
    }

    function autoResize() {
        dom.chatInput.style.height = 'auto';
        dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 160) + 'px';
    }

    function providerLabel(mode) {
        return { deterministic: 'Demo Mode', local: 'Local AI', remote: 'Cloud AI' }[mode] || mode;
    }

    function updateProviderBadge() {
        // FASE V: Show current model/provider with status dot and fallback indicator
        var label = '';
        var statusClass = 'unknown';
        var providerEmoji = '';
        var providerNames = { ollama: 'Ollama', gemini: 'Gemini', nvidia: 'NVIDIA', opencode: 'OpenCode' };

        if (state.selectedProvider && state.selectedModel) {
            var emojis = { ollama: '\uD83D\uDDA5\uFE0F', gemini: '\uD83D\uDC19', nvidia: '\uD83D\uDE80', opencode: '\uD83D\uDD30' };
            providerEmoji = emojis[state.selectedProvider] || '';
            var displayName = providerNames[state.selectedProvider] || state.selectedProvider;
            label = providerEmoji + ' ' + displayName + ' \u00B7 ' + state.selectedModel;

            if (state.modelStatus) {
                statusClass = state.modelStatus;
            }
        } else if (state.providerMode) {
            label = providerLabel(state.providerMode);
            if (state.providerMode === 'local' && state.currentModel) {
                label += ' \u00B7 ' + state.currentModel;
            }
        } else {
            label = 'Chiky';
        }

        // Build badge HTML with status dot
        var dotHtml = '<span class="status-dot ' + statusClass + '"></span>';
        var fallbackHtml = '';
        if (state.fallbackActive && state.fallbackFrom) {
            fallbackHtml = '<span class="fallback-indicator" title="Fallback from ' + state.fallbackFrom + '">\u21BB</span>';
        }
        dom.providerBadge.innerHTML = dotHtml + label + fallbackHtml;

        // Update offline indicator
        if (!state.connectivity.online) {
            dom.providerBadge.classList.add('offline');
            dom.providerBadge.title = 'Offline mode - using local models only';
        } else {
            dom.providerBadge.classList.remove('offline');
            dom.providerBadge.title = 'Provider: ' + (state.selectedProvider || state.providerMode || 'auto');
        }
    }

    async function loadProvider() {
        try {
            // FASE V: Use the consolidated transparency endpoint
            var data = await apiCall('GET', '/providers/transparency');
            if (data && data.provider) {
                state.providerMode = data.mode || data.provider;
                if (data.model) state.currentModel = data.model;
                if (data.connectivity) {
                    state.connectivity.online = data.connectivity.online;
                }
                if (data.provider) state.selectedProvider = data.provider;
                if (data.model) state.selectedModel = data.model;
                state.modelStatus = data.status || 'unknown';
                state.fallbackActive = data.fallback_active || false;
                state.fallbackFrom = data.fallback_from || null;
                state.verifiedCapabilities = data.verified_capabilities || {};
                state.allProviders = data.all_providers || [];
                updateProviderBadge();
                // Render provider info section if visible
                renderProviderInfo(data.all_providers || []);
            }
        } catch (_) {
            // Fallback to legacy endpoints
            try {
                var currentData = await apiCall('GET', '/providers/current');
                if (currentData && currentData.provider) {
                    state.providerMode = currentData.provider;
                    if (currentData.model) state.currentModel = currentData.model;
                    if (currentData.connectivity) {
                        state.connectivity.online = currentData.connectivity.online;
                    }
                    if (currentData.provider) state.selectedProvider = currentData.provider;
                    if (currentData.model) state.selectedModel = currentData.model;
                    updateProviderBadge();
                }
                var legacyData = await apiCall('GET', '/providers');
                if (legacyData.active && legacyData.active.mode) {
                    state.providerMode = legacyData.active.mode;
                    if (legacyData.current_model) state.currentModel = legacyData.current_model;
                    updateProviderBadge();
                    if (legacyData.active.mode === 'local') await loadModels();
                }
            } catch (e2) { dom.providerBadge.innerHTML = 'Chiky'; }
        }
    }

    async function loadModels() {
        try {
            var data = await apiCall('GET', '/providers/local/models');
            var models = data.models || [];
            state.currentModel = data.current || null;
            dom.modelSelector.innerHTML = '';
            models.forEach(function (m) {
                var opt = document.createElement('option');
                opt.value = m; opt.textContent = m;
                if (m === state.currentModel) opt.selected = true;
                dom.modelSelector.appendChild(opt);
            });
            dom.modelSelectorWrapper.classList.remove('hidden');
            updateProviderBadge();
        } catch (_) { dom.modelSelectorWrapper.classList.add('hidden'); }
    }

    async function switchModel(modelName) {
        if (!modelName || modelName === state.currentModel) return;
        try {
            await apiCall('POST', '/providers/local/model', { model: modelName });
            state.currentModel = modelName;
            updateProviderBadge();
        } catch (err) {
            showError(err.message || 'Failed to switch model');
            await loadModels();
        }
    }

    dom.chatForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = dom.chatInput.value.trim();
        if (text) sendMessage(text);
    });
    dom.chatInput.addEventListener('input', function () { updateSendButton(); autoResize(); });
    dom.chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); dom.chatForm.dispatchEvent(new Event('submit')); }
    });
    dom.btnAttach.addEventListener('click', function () { dom.fileInput.click(); });
    dom.fileInput.addEventListener('change', function () { handleFileSelect(this.files[0]); });
    dom.btnNewChat.addEventListener('click', newChat);
    dom.errorClose.addEventListener('click', hideError);
    dom.modelSelector.addEventListener('change', function () { switchModel(this.value); });
    dom.btnSettings.addEventListener('click', openSettings);
    dom.btnCloseSettings.addEventListener('click', closeSettings);
    dom.settingsModal.addEventListener('click', function (e) { if (e.target === dom.settingsModal) closeSettings(); });
    dom.themeOptions.addEventListener('change', function (e) {
        if (e.target.name === 'theme') applyTheme(e.target.value);
    });
    dom.btnCancelDelete.addEventListener('click', closeDeleteModal);
    dom.btnConfirmDelete.addEventListener('click', confirmDelete);
    dom.confirmModal.addEventListener('click', function (e) { if (e.target === dom.confirmModal) closeDeleteModal(); });
    dom.btnCloseRename.addEventListener('click', closeRenameModal);
    dom.btnCancelRename.addEventListener('click', closeRenameModal);
    dom.btnConfirmRename.addEventListener('click', confirmRename);
    dom.renameModal.addEventListener('click', function (e) { if (e.target === dom.renameModal) closeRenameModal(); });
    dom.renameInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') confirmRename(); if (e.key === 'Escape') closeRenameModal(); });
    dom.searchInput.addEventListener('input', function () {
        state.searchQuery = this.value;
        renderSessions();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeSettings();
            closeDeleteModal();
            closeRenameModal();
            closeApprovalModal();
            closeContextMenu();
        }
    });

    // ── Approval Modal ──────────────────────────────────────────────────────
    function showApprovalModal(approvalRequest) {
        var modal = document.getElementById('approval-modal');
        var toolName = approvalRequest.tool_name || 'unknown';
        var toolArgs = approvalRequest.tool_args || {};

        document.getElementById('approval-tool-name').textContent = toolName;

        // Build a readable description of the operation
        var lines = [];
        if (toolArgs.path) lines.push('Path: ' + toolArgs.path);
        if (toolArgs.project_name) lines.push('Project: ' + toolArgs.project_name);
        if (toolArgs.base_path) lines.push('Location: ' + toolArgs.base_path);
        if (toolArgs.content && toolArgs.content.length > 0) {
            var preview = toolArgs.content.length > 200 ? toolArgs.content.slice(0, 200) + '…' : toolArgs.content;
            lines.push('Content preview:\n' + preview);
        }
        if (toolArgs.files && Array.isArray(toolArgs.files)) {
            lines.push('Files: ' + toolArgs.files.map(function(f) { return f.path || f; }).join(', '));
        }

        document.getElementById('approval-details').textContent = lines.join('\n') || JSON.stringify(toolArgs, null, 2);
        modal.classList.remove('hidden');
    }

    // ── FASE T: Model Management ───────────────────────────────────────────

    function renderModelList() {
        if (!dom.modelList) return;
        dom.modelList.innerHTML = '';
        var providers = state.allProviders || [];
        if (providers.length === 0) {
            dom.modelList.innerHTML = '<div class="model-empty">No models discovered</div>';
            return;
        }
        providers.forEach(function (m) {
            var el = document.createElement('div');
            el.className = 'model-item';
            if (m.provider === state.selectedProvider && m.model_id === state.selectedModel) {
                el.className += ' active';
            }
            var statusClass = {
                verified: 'status-ok',
                slow: 'status-warn',
                limited: 'status-warn',
                unavailable: 'status-err',
                failed: 'status-err',
                unknown: 'status-unknown',
                checking: 'status-checking',
            }[m.status] || 'status-unknown';

            el.innerHTML =
                '<div class="model-item-info">' +
                    '<span class="model-item-name">' + escapeHtml(m.display_name || m.model_id) + '</span>' +
                    '<span class="model-item-provider">' + escapeHtml(m.provider) + '</span>' +
                '</div>' +
                '<div class="model-item-meta">' +
                    '<span class="model-item-status ' + statusClass + '">' + escapeHtml(m.status) + '</span>' +
                    (m.measured_latency_ms > 0 ? '<span class="model-item-latency">' + Math.round(m.measured_latency_ms) + 'ms</span>' : '') +
                    (m.verified ? '<span class="model-item-verified">&#10003;</span>' : '') +
                '</div>';
            el.addEventListener('click', function () {
                selectModel(m.provider, m.model_id);
            });
            dom.modelList.appendChild(el);
        });
    }

    async function loadAllProviders() {
        try {
            var data = await apiCall('GET', '/providers/models');
            state.allProviders = data.providers || [];
            state.connectivity = data.connectivity || { online: true };
            state.selectedProvider = data.selected_provider || null;
            state.selectedModel = data.selected_model || null;
            renderModelList();
            updateModelStatusBar();
        } catch (_) {
            state.allProviders = [];
        }
    }

    function updateModelStatusBar() {
        if (!dom.modelStatusBar) return;
        var sel = state.allProviders.find(function (m) {
            return m.provider === state.selectedProvider && m.model_id === state.selectedModel;
        });
        if (sel) {
            dom.modelStatusBar.classList.remove('hidden');
            dom.modelStatusText.textContent = sel.display_name || sel.model_id;
            dom.modelConnectivity.textContent = state.connectivity.online ? 'Online' : 'Offline';
            dom.modelConnectivity.className = 'model-connectivity ' + (state.connectivity.online ? 'online' : 'offline');
        } else {
            dom.modelStatusBar.classList.add('hidden');
        }
    }

    async function selectModel(provider, modelId) {
        try {
            await apiCall('POST', '/providers/select', { provider: provider, model: modelId });
            state.selectedProvider = provider;
            state.selectedModel = modelId;
            renderModelList();
            updateModelStatusBar();
            updateProviderBadge();
        } catch (err) {
            showError(err.message || 'Failed to select model');
        }
    }

    async function verifySelectedModel() {
        var provider = state.selectedProvider;
        var model = state.selectedModel;
        if (!provider || !model) {
            showError('Select a model first');
            return;
        }
        dom.modelStatusText.textContent = 'Verifying...';
        dom.modelStatusBar.classList.remove('hidden');
        try {
            var data = await apiCall('POST', '/providers/verify', { provider: provider, model: model });
            if (data.passed) {
                dom.modelStatusText.textContent = (data.passed_count + '/' + data.total_tests) + ' tests passed - VERIFIED';
            } else {
                dom.modelStatusText.textContent = (data.passed_count + '/' + data.total_tests) + ' tests passed - LIMITED';
            }
            await loadAllProviders();
        } catch (err) {
            showError(err.message || 'Verification failed');
            dom.modelStatusText.textContent = 'Verification failed';
        }
    }

    function setModelMode(mode) {
        state.modelMode = mode;
        var buttons = dom.modelQuickSelect ? dom.modelQuickSelect.querySelectorAll('.model-mode-btn') : [];
        buttons.forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-mode') === mode);
        });
        if (mode === 'advanced') {
            dom.modelAdvancedPanel.classList.remove('hidden');
            loadAllProviders();
        } else {
            dom.modelAdvancedPanel.classList.add('hidden');
            // Quick mode: let the backend select
            var quickMap = { recommended: 'recommended', coding: 'coding', vision: 'vision', offline: 'offline' };
            if (quickMap[mode]) {
                selectModel('auto', quickMap[mode]);
            }
        }
    }

    function initModelManagement() {
        if (dom.modelQuickSelect) {
            dom.modelQuickSelect.addEventListener('click', function (e) {
                var btn = e.target.closest('.model-mode-btn');
                if (btn) setModelMode(btn.getAttribute('data-mode'));
            });
        }
        if (dom.btnVerifyModel) dom.btnVerifyModel.addEventListener('click', verifySelectedModel);
        if (dom.btnRefreshModels) dom.btnRefreshModels.addEventListener('click', loadAllProviders);
        // FASE U: Provider configuration
        initProviderConfig();
    }

    // ── FASE U: Provider Configuration ─────────────────────────────────────

    async function loadProviderConfig() {
        try {
            var data = await apiCall('GET', '/providers/config');
            var geminiStatus = document.getElementById('gemini-status');
            var nvidiaStatus = document.getElementById('nvidia-status');
            var aiMode = document.getElementById('ai-provider-mode');
            var ollamaBaseUrl = document.getElementById('ollama-base-url');
            var ocUser = document.getElementById('opencode-username');
            if (geminiStatus) {
                geminiStatus.textContent = data.gemini_configured ? 'Configured' : 'Not configured';
                geminiStatus.className = 'provider-config-status ' + (data.gemini_configured ? 'success' : '');
            }
            if (nvidiaStatus) {
                nvidiaStatus.textContent = data.nvidia_configured ? 'Configured' : 'Not configured';
                nvidiaStatus.className = 'provider-config-status ' + (data.nvidia_configured ? 'success' : '');
            }
            if (ollamaBaseUrl && data.ollama_base_url) {
                ollamaBaseUrl.value = data.ollama_base_url;
            }
            if (ocUser && data.opencode_username_configured) {
                ocUser.value = '';
                ocUser.placeholder = 'Configured';
            }
            if (aiMode && data.ai_provider) {
                aiMode.value = data.ai_provider;
            }
        } catch (_) {}
    }

    async function saveProviderConfig() {
        var payload = {};
        var geminiKey = document.getElementById('gemini-api-key');
        var nvidiaKey = document.getElementById('nvidia-api-key');
        var aiMode = document.getElementById('ai-provider-mode');
        var ocUser = document.getElementById('opencode-username');
        var ocPass = document.getElementById('opencode-password');
        var statusEl = document.getElementById('config-save-status');
        if (geminiKey && geminiKey.value.trim().length > 0) {
            payload.gemini_api_key = geminiKey.value.trim();
        }
        if (nvidiaKey && nvidiaKey.value.trim().length > 0) {
            payload.nvidia_api_key = nvidiaKey.value.trim();
        }
        if (ocUser && ocUser.value.trim().length > 0) {
            payload.opencode_server_username = ocUser.value.trim();
        }
        if (ocPass && ocPass.value.trim().length > 0) {
            payload.opencode_server_password = ocPass.value.trim();
        }
        if (aiMode) {
            payload.ai_provider = aiMode.value;
        }
        if (Object.keys(payload).length === 0) {
            if (statusEl) {
                statusEl.textContent = 'No changes to save';
                statusEl.className = 'provider-config-status';
            }
            return;
        }
        try {
            await apiCall('POST', '/providers/config', payload);
            if (statusEl) {
                statusEl.textContent = 'Saved';
                statusEl.className = 'provider-config-status success';
            }
            // Clear password fields
            if (geminiKey) geminiKey.value = '';
            if (nvidiaKey) nvidiaKey.value = '';
            if (ocPass) ocPass.value = '';
            // Reload config to show updated status
            await loadProviderConfig();
            // Reload providers to reflect changes
            await loadAllProviders();
            await loadProvider();
        } catch (err) {
            if (statusEl) {
                statusEl.textContent = err.message || 'Failed to save';
                statusEl.className = 'provider-config-status error';
            }
        }
    }

    function initProviderConfig() {
        var btnSave = document.getElementById('btn-save-config');
        if (btnSave) btnSave.addEventListener('click', saveProviderConfig);
        // FASE V: Test connection buttons
        var btnTestGemini = document.getElementById('btn-test-gemini');
        var btnTestNvidia = document.getElementById('btn-test-nvidia');
        if (btnTestGemini) btnTestGemini.addEventListener('click', function() { testProviderConnection('gemini'); });
        if (btnTestNvidia) btnTestNvidia.addEventListener('click', function() { testProviderConnection('nvidia'); });
        // FASE AB.6: Ollama + OpenCode test buttons
        if (dom.btnTestOllama) dom.btnTestOllama.addEventListener('click', function() { testProviderConnection('ollama'); });
        if (dom.btnTestOpencode) dom.btnTestOpencode.addEventListener('click', function() { testProviderConnection('opencode'); });
        loadProviderConfig();
    }

    // ── FASE V: Provider Transparency ──────────────────────────────────────

    function renderProviderInfo(providers) {
        var container = document.getElementById('provider-info-content');
        if (!container) return;
        if (!providers || providers.length === 0) {
            container.innerHTML = '<div class="provider-info-loading">No providers discovered</div>';
            return;
        }
        var capabilityNames = {
            basic_response: 'Chat', tool_calling: 'Tools', coding: 'Coding',
            file_creation: 'Files', file_modification: 'Modify', multi_step: 'Multi-step',
            security: 'Security', context_handling: 'Context', verification: 'Verify',
            system_prompt: 'System Prompt', argument_compatibility: 'Args'
        };
        var html = '';
        // Group by provider
        var grouped = {};
        providers.forEach(function(p) {
            if (!grouped[p.provider]) grouped[p.provider] = [];
            grouped[p.provider].push(p);
        });
        Object.keys(grouped).forEach(function(provName) {
            var models = grouped[provName];
            var bestModel = models.reduce(function(a, b) {
                var scoreA = a.verified_capabilities_score || 0;
                var scoreB = b.verified_capabilities_score || 0;
                return scoreB > scoreA ? b : a;
            }, models[0]);
            var providerEmojis = { ollama: '\uD83D\uDDA5\uFE0F', gemini: '\uD83D\uDC19', nvidia: '\uD83D\uDE80', opencode: '\uD83D\uDD30' };
            var emoji = providerEmojis[provName] || '\uD83D\uDDA5\uFE0F';
            html += '<div class="provider-info-card">';
            html += '<div class="provider-info-header">';
            html += '<span class="provider-info-name">' + emoji + ' ' + provName.charAt(0).toUpperCase() + provName.slice(1) + '</span>';
            html += '<span class="provider-info-status ' + bestModel.status + '">' + bestModel.status + '</span>';
            html += '</div>';
            html += '<div class="provider-info-meta">';
            html += '<span>Models: ' + models.length + '</span>';
            html += '<span>Latency: ' + bestModel.latency_ms + 'ms</span>';
            html += '<span>Reliability: ' + Math.round(bestModel.reliability * 100) + '%</span>';
            html += '</div>';
            html += '<div class="provider-info-models">';
            models.forEach(function(m) {
                html += '<div>' + m.model + ' <span class="provider-info-status ' + m.status + '" style="font-size:0.65rem">' + m.status + '</span></div>';
            });
            html += '</div>';
            html += '</div>';
        });
        container.innerHTML = html;
    }

    function showAutoSelectToast(provider, model, reason) {
        var existing = document.querySelector('.auto-select-toast');
        if (existing) existing.remove();
        var toast = document.createElement('div');
        toast.className = 'auto-select-toast';
        toast.innerHTML = '<div>\u2934 Auto-selected: <span class="toast-provider">' + provider + ' \u00B7 ' + model + '</span></div>' +
            (reason ? '<div class="toast-reason">' + reason + '</div>' : '');
        document.body.appendChild(toast);
        requestAnimationFrame(function() { toast.classList.add('visible'); });
        setTimeout(function() {
            toast.classList.remove('visible');
            setTimeout(function() { toast.remove(); }, 400);
        }, 4000);
    }

    async function testProviderConnection(providerName) {
        var btn = document.getElementById('btn-test-' + providerName);
        var statusEl = document.getElementById(providerName + '-status');
        if (!btn || !statusEl) return;
        btn.classList.add('testing');
        btn.textContent = 'Testing...';
        statusEl.textContent = '';
        statusEl.className = 'provider-config-status';
        try {
            // Save any pending credentials first (keys are only read from config)
            var keyPayload = {};
            if (providerName === 'gemini') {
                var gemKey = document.getElementById('gemini-api-key');
                if (gemKey && gemKey.value.trim().length > 0) keyPayload.gemini_api_key = gemKey.value.trim();
            } else if (providerName === 'nvidia') {
                var nvKey = document.getElementById('nvidia-api-key');
                if (nvKey && nvKey.value.trim().length > 0) keyPayload.nvidia_api_key = nvKey.value.trim();
            } else if (providerName === 'opencode') {
                var ocUser = document.getElementById('opencode-username');
                var ocPass = document.getElementById('opencode-password');
                if (ocUser && ocUser.value.trim().length > 0) keyPayload.opencode_server_username = ocUser.value.trim();
                if (ocPass && ocPass.value.trim().length > 0) keyPayload.opencode_server_password = ocPass.value.trim();
            }
            if (Object.keys(keyPayload).length > 0) {
                await apiCall('POST', '/providers/config', keyPayload);
            }
            // FASE AB.6: real end-to-end Test Connection (actual inference)
            var ctrl = new AbortController();
            var timer = setTimeout(function () { ctrl.abort(); }, 120000);
            var result;
            try {
                result = await fetch(API + '/providers/test-connection', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: providerName }),
                    signal: ctrl.signal,
                });
                clearTimeout(timer);
            } catch (err) {
                clearTimeout(timer);
                throw err;
            }
            var payload = await result.json();
            if (result.ok && payload.status === 'AVAILABLE') {
                statusEl.textContent = '\u2713 ' + payload.provider + ' \u00B7 ' + payload.model + ' \u00B7 ' + (payload.latency_ms || '?') + 'ms';
                statusEl.className = 'provider-config-status success';
                statusEl.title = payload.detail || 'Connection verified';
            } else {
                statusEl.textContent = '\u2717 ' + (payload.detail || payload.status || 'Failed');
                statusEl.className = 'provider-config-status error';
                statusEl.title = 'Model: ' + (payload.model || '—') + ' | Status: ' + (payload.status || '—');
            }
        } catch (err) {
            statusEl.textContent = '\u2717 ' + (err.message || 'Failed');
            statusEl.className = 'provider-config-status error';
        } finally {
            btn.classList.remove('testing');
            btn.textContent = 'Test Connection';
        }
    }

    // ── FASE U: Auto-Selection ─────────────────────────────────────────────

    async function autoSelectModel(taskDescription, needsVision) {
        try {
            var data = await apiCall('POST', '/providers/auto-select', {
                task_description: taskDescription || 'general task',
                needs_vision: needsVision || false,
            });
            if (data.selected) {
                // FASE V: Show auto-select transparency toast
                showAutoSelectToast(data.provider, data.model, data.reason);
                state.selectedProvider = data.provider;
                state.selectedModel = data.model;
                updateProviderBadge();
                // TEST 6 (AB.8): keep the "Provider selected by Chiky" display in
                // sync with the router decision (it was stale after an auto-select
                // where the router changed the model).
                updateAutoProviderDisplay();
            }
            return data;
        } catch (_) {
            return { selected: false };
        }
    }

    // FASE X: Routing mode (Manual/Automatic)
    function initRoutingMode() {
        var selector = document.getElementById('routing-mode-selector');
        var manualSection = document.getElementById('manual-provider-section');
        var autoSection = document.getElementById('auto-provider-display');
        if (!selector) return;

        // Load saved routing mode from config
        loadRoutingMode();

        selector.addEventListener('change', function(e) {
            if (e.target.name === 'routing-mode') {
                state.routingMode = e.target.value;
                if (e.target.value === 'manual') {
                    manualSection.classList.remove('hidden');
                    autoSection.classList.add('hidden');
                    loadManualProviderList();
                } else {
                    manualSection.classList.add('hidden');
                    autoSection.classList.remove('hidden');
                    autoSelectModel('general task');
                }
                // Persist to backend
                apiCall('POST', '/providers/config', { routing_mode: state.routingMode }).catch(function() {});
            }
        });
    }

    async function loadRoutingMode() {
        try {
            var data = await apiCall('GET', '/providers/config');
            if (data.routing_mode) {
                state.routingMode = data.routing_mode;
                var radio = document.querySelector('input[name="routing-mode"][value="' + data.routing_mode + '"]');
                if (radio) radio.checked = true;
                var manualSection = document.getElementById('manual-provider-section');
                var autoSection = document.getElementById('auto-provider-display');
                if (data.routing_mode === 'manual') {
                    if (manualSection) manualSection.classList.remove('hidden');
                    if (autoSection) autoSection.classList.add('hidden');
                    loadManualProviderList();
                }
            }
        } catch (_) {}
    }

    async function loadManualProviderList() {
        var provSelect = document.getElementById('manual-provider-select');
        var modelSelect = document.getElementById('manual-model-select');
        if (!provSelect || !modelSelect) return;

        provSelect.innerHTML = '<option value="">-- Select provider --</option>';
        modelSelect.innerHTML = '<option value="">-- Select model --</option>';

        try {
            var data = await apiCall('GET', '/providers/transparency');
            var providers = data.all_providers || [];
            if (providers.length === 0) {
                var modelsData = await apiCall('GET', '/providers/models');
                providers = modelsData.providers || [];
            }
            var grouped = {};
            var noModelProviders = {};
            providers.forEach(function(p) {
                var provName = p.provider;
                var modelName = p.model || p.model_id;
                if (!provName) return;
                if (modelName) {
                    if (!grouped[provName]) grouped[provName] = [];
                    grouped[provName].push({
                        provider: provName,
                        model: modelName,
                        status: p.status || 'unknown'
                    });
                } else {
                    noModelProviders[provName] = {
                        provider: provName,
                        status: p.status || 'unavailable',
                        detail: p.detail || ''
                    };
                }
            });
            Object.keys(grouped).forEach(function(name) {
                var opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name.charAt(0).toUpperCase() + name.slice(1) + ' (' + grouped[name].length + ' models)';
                provSelect.appendChild(opt);
            });
            Object.keys(noModelProviders).forEach(function(name) {
                if (grouped[name]) return;
                var opt = document.createElement('option');
                opt.value = name;
                var detail = noModelProviders[name].detail ? ' - ' + noModelProviders[name].detail : ' (unavailable)';
                opt.textContent = name.charAt(0).toUpperCase() + name.slice(1) + ' (0 models)' + detail;
                provSelect.appendChild(opt);
            });
            if (state.currentProviders && !Object.keys(grouped).length && !Object.keys(noModelProviders).length) {
                var prev = (state.currentProviders || []).forEach(function(name) { grouped[name] = []; });
            }

            // Pre-select current selected provider if any
            if (state.selectedProvider && grouped[state.selectedProvider]) {
                provSelect.value = state.selectedProvider;
                populateModelsForProvider(state.selectedProvider, grouped, modelSelect);
                if (state.selectedModel) {
                    modelSelect.value = state.selectedModel;
                    // TEST 6 (AB.8): lock + persist the pre-selected model so the
                    // Manual status text, dropdowns and backend never disagree
                    // (previously the "Locked: ..." status and DB could keep a
                    // stale entry while the dropdown showed a different model).
                    selectManualProvider(state.selectedProvider, state.selectedModel);
                }
            }

            // Remove previous event listener by cloning or direct assignment
            provSelect.onchange = function() {
                var prov = provSelect.value;
                modelSelect.innerHTML = '<option value="">-- Select model --</option>';
                if (grouped[prov]) {
                    grouped[prov].forEach(function(m) {
                        var opt = document.createElement('option');
                        opt.value = m.model;
                        opt.textContent = m.model + ' [' + m.status + ']';
                        modelSelect.appendChild(opt);
                    });
                    if (grouped[prov].length > 0) {
                        modelSelect.value = grouped[prov][0].model;
                        selectManualProvider(prov, modelSelect.value);
                    }
                } else if (noModelProviders[prov]) {
                    var statusBadge = document.getElementById('manual-status-badge');
                    var statusText = document.getElementById('manual-status-text');
                    var statusContainer = document.getElementById('manual-provider-status');
                    if (statusContainer) statusContainer.classList.remove('hidden');
                    if (statusBadge) statusBadge.className = 'status-badge unavailable';
                    if (statusText) statusText.textContent = 'Unavailable: ' + prov + ' (no discoverable models)';
                }
            };

            modelSelect.onchange = function() {
                var prov = provSelect.value;
                var model = modelSelect.value;
                if (prov && model) {
                    selectManualProvider(prov, model);
                }
            };
        } catch (_) {}
    }

    function populateModelsForProvider(prov, grouped, modelSelect) {
        modelSelect.innerHTML = '<option value="">-- Select model --</option>';
        if (grouped[prov]) {
            grouped[prov].forEach(function(m) {
                var opt = document.createElement('option');
                opt.value = m.model;
                opt.textContent = m.model + ' [' + m.status + ']';
                modelSelect.appendChild(opt);
            });
        }
    }

    async function selectManualProvider(provider, model) {
        var statusBadge = document.getElementById('manual-status-badge');
        var statusText = document.getElementById('manual-status-text');
        var statusContainer = document.getElementById('manual-provider-status');
        if (statusContainer) statusContainer.classList.remove('hidden');
        if (statusBadge) statusBadge.className = 'status-badge checking';
        if (statusText) statusText.textContent = 'Verifying ' + provider + '/' + model + '...';

        try {
            var result = await apiCall('POST', '/providers/select', {
                provider: provider,
                model: model,
            });
            if (statusBadge) statusBadge.className = 'status-badge verified';
            if (statusText) statusText.textContent = 'Locked: ' + provider + ' / ' + model;
            state.selectedProvider = provider;
            state.selectedModel = model;
            updateProviderBadge();
        } catch (err) {
            if (statusBadge) statusBadge.className = 'status-badge unavailable';
            if (statusText) statusText.textContent = 'Failed: ' + (err.message || 'Provider unavailable');
        }
    }

    // FASE X: Provenance display
    function updateProvenanceDisplay() {
        var mode = document.getElementById('prov-mode');
        var requested = document.getElementById('prov-requested');
        var actual = document.getElementById('prov-actual');
        var status = document.getElementById('prov-status');
        var fallback = document.getElementById('prov-fallback');

        if (mode) mode.textContent = state.routingMode === 'manual' ? 'Manual' : 'Automatic';
        if (requested && state.selectedProvider) {
            requested.textContent = state.selectedProvider + (state.selectedModel ? ' / ' + state.selectedModel : '');
        }
        if (actual) {
            if (state.lastExecutedProvider) {
                var lat = state.lastLatencyMs !== null ? ' · ' + state.lastLatencyMs + 'ms' : '';
                actual.textContent = state.lastExecutedProvider + (state.lastExecutedModel ? ' / ' + state.lastExecutedModel : '') + lat;
            } else {
                actual.textContent = state.selectedProvider ? (state.selectedProvider + (state.selectedModel ? ' / ' + state.selectedModel : '')) : '--';
            }
        }
        if (status) status.textContent = state.lastExecutionStatus || state.modelStatus || 'unknown';
        if (fallback) {
            if (state.lastFallbackChain && state.lastFallbackChain.length > 0) {
                fallback.textContent = state.lastFallbackChain.join(' → ');
            } else {
                fallback.textContent = state.fallbackActive ? (state.fallbackFrom || 'Yes') : 'None';
            }
        }
    }

    // FASE AB.6: reflect the actual executor of the last completed message
    function updateLastExecutionProvenance(provenance) {
        if (!provenance) return;
        if (provenance.executed_provider) {
            state.lastExecutedProvider = provenance.executed_provider;
            state.lastExecutedModel = provenance.executed_model || '';
        }
        state.lastExecutionStatus = provenance.status || 'ok';
        state.lastLatencyMs = typeof provenance.latency_ms === 'number' ? provenance.latency_ms : null;
        state.lastFallbackChain = provenance.fallback_chain || (provenance.fallback_active ? ['fallback'] : []);
        state.fallbackActive = !!provenance.fallback_active;
        state.fallbackFrom = provenance.fallback_from_provider || (state.lastFallbackChain.length ? state.lastFallbackChain[0] : null);
        updateProvenanceDisplay();
    }

    // FASE X: Auto provider display
    function updateAutoProviderDisplay() {
        var name = document.getElementById('auto-provider-name');
        var model = document.getElementById('auto-provider-model');
        var reason = document.getElementById('auto-provider-reason');
        if (name) name.textContent = state.selectedProvider || '--';
        if (model) model.textContent = state.selectedModel || '';
        if (reason) reason.textContent = state.modelMode !== 'advanced' ? 'Mode: ' + state.modelMode : '';
    }

    // ── FASE AB.6: First-Run Setup Wizard ─────────────────────────────

    var WIZARD_PROVIDER_ORDER = ['ollama', 'gemini', 'nvidia', 'opencode'];
    var WIZARD_PROVIDER_LABELS = {
        ollama: '\uD83D\uDDA5\uFE0F Ollama (local)',
        gemini: '\uD83D\uDC19 Gemini',
        nvidia: '\uD83D\uDE80 NVIDIA',
        opencode: '\uD83D\uDD10 OpenCode',
    };

    function wizardStatusClass(status) {
        var map = {
            'AVAILABLE': 'verified',
            'NOT_CONFIGURED': 'unavailable',
            'UNAVAILABLE': 'unavailable',
            'AUTH_ERROR': 'error',
            'ERROR': 'error',
        };
        return 'status-badge ' + (map[status] || 'unknown');
    }

    function wizardStatusLabel(status) {
        var map = {
            'AVAILABLE': 'Available',
            'NOT_CONFIGURED': 'Not configured',
            'UNAVAILABLE': 'Unavailable',
            'AUTH_ERROR': 'Authentication error',
            'ERROR': 'Error',
        };
        return map[status] || status || 'Unknown';
    }

    function wizardCredentialFields(provider) {
        if (provider === 'gemini') {
            return '<input id="wiz-gemini-key" type="password" class="text-input" placeholder="Gemini API key (optional)" autocomplete="off" />';
        }
        if (provider === 'nvidia') {
            return '<input id="wiz-nvidia-key" type="password" class="text-input" placeholder="NVIDIA API key (optional)" autocomplete="off" />';
        }
        if (provider === 'opencode') {
            return '<input id="wiz-opencode-user" type="text" class="text-input" placeholder="OpenCode username" autocomplete="off" style="margin-bottom:6px" />' +
                '<input id="wiz-opencode-pass" type="password" class="text-input" placeholder="OpenCode password" autocomplete="off" />';
        }
        if (provider === 'ollama') {
            return '<span class="setting-hint">Local server: http://localhost:11434</span>';
        }
        return '';
    }

    function wizardProviderCard(p) {
        var label = WIZARD_PROVIDER_LABELS[p.provider] || p.provider;
        var models = (p.models && p.models.length) ? p.models.join(', ') : (p.model || '');
        var html = '<div class="wizard-provider-card">';
        html += '<div class="wizard-provider-head">';
        html += '<span class="wizard-provider-name">' + label + '</span>';
        html += '<span class="' + wizardStatusClass(p.status) + '">' + wizardStatusLabel(p.status) + '</span>';
        html += '</div>';
        html += '<div class="wizard-provider-detail" title="' + escapeHtml(p.detail || '') + '">' + escapeHtml(p.detail || '') +
            (models ? '<div class="wizard-provider-models">Models: ' + escapeHtml(models) + '</div>' : '') + '</div>';
        html += '<div class="wizard-provider-actions">';
        html += wizardCredentialFields(p.provider);
        html += '<button class="btn btn-ghost btn-sm wizard-test-btn" data-provider="' + p.provider + '">Test Connection</button>';
        html += '<span class="provider-config-status" id="wiz-status-' + p.provider + '"></span>';
        html += '</div></div>';
        return html;
    }

    async function loadWizardProviders() {
        try {
            var statuses = await apiCall('GET', '/providers/status');
            var html = '';
            WIZARD_PROVIDER_ORDER.forEach(function (name) {
                var p = null;
                statuses.forEach(function (s) { if (s.provider === name) p = s; });
                if (!p) p = { provider: name, status: 'UNAVAILABLE', detail: 'Not discovered', models: [] };
                html += wizardProviderCard(p);
            });
            dom.wizardProviderGrid.innerHTML = '<div id="wizard-grid-inner">' + html + '</div>';
            document.querySelectorAll('.wizard-test-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    wizardTestConnection(btn.getAttribute('data-provider'));
                });
            });
            await buildWizardManualProviders(statuses);
        } catch (_) {
            dom.wizardProviderGrid.innerHTML = '<div class="provider-info-loading">Could not contact backend.</div>';
        }
    }

    async function wizardTestConnection(provider) {
        var btn = document.querySelector('.wizard-test-btn[data-provider="' + provider + '"]');
        var statusEl = document.getElementById('wiz-status-' + provider);
        if (!btn || !statusEl) return;
        btn.classList.add('testing');
        btn.textContent = 'Testing...';
        statusEl.textContent = '';
        statusEl.className = 'provider-config-status';
        var payload = {};
        if (provider === 'gemini') {
            var gk = document.getElementById('wiz-gemini-key');
            if (gk && gk.value.trim()) payload.gemini_api_key = gk.value.trim();
        } else if (provider === 'nvidia') {
            var nk = document.getElementById('wiz-nvidia-key');
            if (nk && nk.value.trim()) payload.nvidia_api_key = nk.value.trim();
        } else if (provider === 'opencode') {
            var ou = document.getElementById('wiz-opencode-user');
            var op = document.getElementById('wiz-opencode-pass');
            if (ou && ou.value.trim()) payload.opencode_server_username = ou.value.trim();
            if (op && op.value.trim()) payload.opencode_server_password = op.value.trim();
        }
        if (Object.keys(payload).length > 0) {
            await apiCall('POST', '/providers/config', payload).catch(function () {});
        }
        try {
            var result = await apiCall('POST', '/providers/test-connection', { provider: provider });
            if (result.status === 'AVAILABLE') {
                statusEl.textContent = '\u2713 ' + result.provider + ' \u00B7 ' + result.model + ' \u00B7 ' + result.latency_ms + 'ms';
                statusEl.className = 'provider-config-status success';
            } else {
                statusEl.textContent = '\u2717 ' + (result.detail || result.status || 'Failed');
                statusEl.className = 'provider-config-status error';
            }
        } catch (err) {
            statusEl.textContent = '\u2717 ' + (err.message || 'Failed');
            statusEl.className = 'provider-config-status error';
        } finally {
            btn.classList.remove('testing');
            btn.textContent = 'Test Connection';
        }
    }

    async function buildWizardManualProviders(statuses) {
        var provSel = dom.wizardManualProvider;
        var modelSel = dom.wizardManualModel;
        if (!provSel || !modelSel) return;
        provSel.innerHTML = '<option value="">-- Select provider --</option>';
        modelSel.innerHTML = '<option value="">-- Select model --</option>';
        var grouped = {};
        statuses.forEach(function (s) {
            if (s.status === 'AVAILABLE') {
                if ((s.models || []).length > 0 || s.model) {
                    grouped[s.provider] = (s.models && s.models.length) ? s.models : [s.model];
                }
            }
        });
        Object.keys(grouped).forEach(function (name) {
            var opt = document.createElement('option');
            opt.value = name;
            opt.textContent = (WIZARD_PROVIDER_LABELS[name] || name) + ' (' + grouped[name].length + ' models)';
            provSel.appendChild(opt);
        });
        provSel.onchange = async function () {
            var prov = provSel.value;
            modelSel.innerHTML = '<option value="">-- Select model --</option>';
            if (!prov) return;
            try {
                var data = await apiCall('GET', '/providers/' + prov + '/models');
                (data.models || []).forEach(function (m) {
                    var opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    modelSel.appendChild(opt);
                });
                if (modelSel.options.length > 1) modelSel.selectedIndex = 1;
            } catch (_) {
                (grouped[prov] || []).forEach(function (m) {
                    var opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    modelSel.appendChild(opt);
                });
            }
        };
    }

    async function initWizard() {
        var statusData = await checkSetupStatus();
        if (!statusData) return;
        if (statusData.routing_configured && !statusData.setup_required) {
            return;
        }
        dom.wizardModal.classList.remove('hidden');
        await loadWizardProviders();

        document.querySelectorAll('input[name="wizard-routing-mode"]').forEach(function (r) {
            r.addEventListener('change', function () {
                if (r.value === 'manual') {
                    if (dom.wizardManualSelection) dom.wizardManualSelection.classList.remove('hidden');
                } else {
                    if (dom.wizardManualSelection) dom.wizardManualSelection.classList.add('hidden');
                }
            });
        });

        if (dom.btnWizardSkip) dom.btnWizardSkip.addEventListener('click', function () {
            dom.wizardModal.classList.add('hidden');
            apiCall('POST', '/providers/config', { routing_mode: 'automatic' }).catch(function () {});
        });

        if (dom.btnWizardFinish) dom.btnWizardFinish.addEventListener('click', async function () {
            var payload = {};
            var gk = document.getElementById('wiz-gemini-key');
            var nk = document.getElementById('wiz-nvidia-key');
            var ou = document.getElementById('wiz-opencode-user');
            var op = document.getElementById('wiz-opencode-pass');
            if (gk && gk.value.trim()) payload.gemini_api_key = gk.value.trim();
            if (nk && nk.value.trim()) payload.nvidia_api_key = nk.value.trim();
            if (ou && ou.value.trim()) payload.opencode_server_username = ou.value.trim();
            if (op && op.value.trim()) payload.opencode_server_password = op.value.trim();
            var mode = document.querySelector('input[name="wizard-routing-mode"]:checked');
            payload.routing_mode = mode ? mode.value : 'automatic';
            var manualProv = '';
            var manualModel = '';
            if (payload.routing_mode === 'manual') {
                manualProv = dom.wizardManualProvider ? dom.wizardManualProvider.value : '';
                manualModel = dom.wizardManualModel ? dom.wizardManualModel.value : '';
            }
            if (dom.wizardSaveStatus) {
                dom.wizardSaveStatus.textContent = 'Saving...';
                dom.wizardSaveStatus.className = 'provider-config-status';
            }
            try {
                await apiCall('POST', '/providers/config', payload);
                if (payload.routing_mode === 'manual' && manualProv) {
                    await apiCall('POST', '/providers/select', {
                        provider: manualProv,
                        model: manualModel,
                    }).catch(function () {});
                } else if (payload.routing_mode === 'automatic') {
                    await autoSelectModel('general task').catch(function () {});
                }
                dom.wizardModal.classList.add('hidden');
                await loadAllProviders();
                await loadProvider();
                await loadSessions();
                if (state.currentSessionId === null) newChat();
                await sendMessage('\u00BFQu\u00E9 puedes hacer?');
            } catch (err) {
                if (dom.wizardSaveStatus) {
                    dom.wizardSaveStatus.textContent = 'Failed: ' + (err.message || 'error');
                    dom.wizardSaveStatus.className = 'provider-config-status error';
                }
            }
        });
    }

    async function checkSetupStatus() {
        try {
            return await apiCall('GET', '/setup/status');
        } catch (_) {
            return null;
        }
    }

    function closeApprovalModal() {
        document.getElementById('approval-modal').classList.add('hidden');
        state.pendingApproval = null;
    }

    async function confirmApproval() {
        if (!state.pendingApproval) return;
        var pending = state.pendingApproval;
        state.pendingApproval = null;
        document.getElementById('approval-modal').classList.add('hidden');

        // Re-send the original message with explicit approval
        var sessionId = pending.sessionId || state.currentSessionId || generateUUID();
        appendMessage('user', '✅ Approved — proceeding with the operation.', new Date().toISOString());
        showLoading();
        scrollToBottom();

        try {
            var payload = { input: pending.originalMessage };
            if (pending.originalFiles && pending.originalFiles.length > 0) {
                payload.attached_files = pending.originalFiles;
            }
            // This is the only place where X-Approval-Granted: true is sent —
            // it is scoped to exactly one request execution after user explicitly clicked Approve.
            var data = await apiCall('POST', '/sessions/' + sessionId + '/messages', payload, { 'X-Approval-Granted': 'true' }, { timeoutMs: chatRequestTimeoutMs() });
            hideLoading();
            if (!state.currentSessionId) {
                state.currentSessionId = sessionId;
                await loadSessions();
            }
            if (data && data.assistant_message) {
                appendMessage(data.assistant_message.role, data.assistant_message.content, data.assistant_message.created_at);
            } else if (data && data.status === 'failed') {
                appendMessage('assistant', 'The operation could not be completed. Please try again.', new Date().toISOString());
            }
            // If approval is required again (chained operations), show the dialog again
            if (data && data.approval_request) {
                state.pendingApproval = {
                    originalMessage: pending.originalMessage,
                    originalFiles: pending.originalFiles,
                    sessionId: sessionId,
                    approvalRequest: data.approval_request,
                };
                showApprovalModal(data.approval_request);
            }
            scrollToBottom();
            await loadSessions();
        } catch (err) {
            hideLoading();
            var approvedMessage = chatFailureMessage(err, sessionId);
            appendMessage('assistant', approvedMessage, new Date().toISOString());
            showError(approvedMessage);
        } finally {
            state.sending = false;
            updateSendButton();
            dom.chatInput.focus();
        }
    }

    async function init() {
        loadTheme();
        showEmpty();
        initModelManagement();
        initRoutingMode();
        await Promise.all([loadSessions(), loadProvider(), loadAllProviders()]);
        initWizard();
        updateProvenanceDisplay();
        updateAutoProviderDisplay();
    }

    // Wire up approval modal buttons
    var btnApprove = document.getElementById('btn-confirm-approval');
    var btnCancelApproval = document.getElementById('btn-cancel-approval');
    var approvalModal = document.getElementById('approval-modal');
    if (btnApprove) btnApprove.addEventListener('click', confirmApproval);
    if (btnCancelApproval) btnCancelApproval.addEventListener('click', function () {
        closeApprovalModal();
        appendMessage('assistant', 'Operation cancelled. Let me know if you need something else.', new Date().toISOString());
        scrollToBottom();
    });
    if (approvalModal) approvalModal.addEventListener('click', function (e) { if (e.target === approvalModal) closeApprovalModal(); });

    init();
})();
