/** @odoo-module **/

import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { _t } from "@web/core/l10n/translation";

class AiChatAction extends Component {
    static template = "sdlc_ai_assistant.AiChatAction";
    static props = { ...standardActionServiceProps };

    setup() {
        this.notification = useService("notification");
        this.messagesRef = useRef("messagesContainer");
        this.inputRef = useRef("chatInput");

        this.state = useState({
            messages: [],
            conversations: [],
            conversationId: null,
            inputValue: "",
            loading: false,
            models: [],
            selectedModel: "",
            sidebarOpen: true,
            hasMoreMessages: false,
            totalMessages: 0,
            loadingMore: false,
            renamingConvId: null,
            renameValue: "",
        });

        this._onKeyboardShortcut = this._onKeyboardShortcut.bind(this);

        onMounted(async () => {
            await this.loadModels();
            this.loadConversations();
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
            document.addEventListener("keydown", this._onKeyboardShortcut);
        });
    }

    // Called by OWL when component is destroyed
    destroy() {
        document.removeEventListener("keydown", this._onKeyboardShortcut);
        super.destroy(...arguments);
    }

    // ── Keyboard shortcuts ──────────────────────────────────────
    _onKeyboardShortcut(ev) {
        // Alt+N → New chat (Alt doesn't conflict with browser shortcuts)
        if (ev.altKey && ev.key === "n") {
            ev.preventDefault();
            this.onNewConversation();
            return;
        }
        // Escape → Clear input or cancel rename
        if (ev.key === "Escape") {
            if (this.state.renamingConvId) {
                this.state.renamingConvId = null;
                this.state.renameValue = "";
                return;
            }
            if (this.state.inputValue) {
                this.state.inputValue = "";
                return;
            }
        }
    }

    async loadModels() {
        try {
            const models = await rpc("/ai_assistant/models", {});
            this.state.models = models || [];
            if (models && models.length > 0) {
                this.state.selectedModel = models[0].model;
            }
        } catch (e) {
            console.error("Failed to load models:", e);
        }
    }

    async loadConversations() {
        try {
            const result = await rpc("/ai_assistant/conversations", { limit: 30 });
            this.state.conversations = result || [];
        } catch (e) {
            console.error("Failed to load conversations:", e);
        }
    }

    async loadMessages(conversationId) {
        try {
            const result = await rpc("/ai_assistant/conversation/messages", {
                conversation_id: conversationId,
                offset: 0,
                limit: 50,
            });
            if (result && Array.isArray(result.messages)) {
                this.state.messages = result.messages.map(msg => ({
                    ...msg,
                    tables: Array.isArray(msg.tables) ? msg.tables : [],
                }));
                this.state.hasMoreMessages = result.has_more || false;
                this.state.totalMessages = result.total || 0;
                this.scrollToBottom();
            } else if (Array.isArray(result)) {
                // Backward compatibility
                this.state.messages = result.map(msg => ({
                    ...msg,
                    tables: Array.isArray(msg.tables) ? msg.tables : [],
                }));
                this.state.hasMoreMessages = false;
                this.scrollToBottom();
            }
        } catch (e) {
            console.error("Failed to load messages:", e);
        }
    }

    async onLoadMoreMessages() {
        if (this.state.loadingMore || !this.state.hasMoreMessages) return;
        this.state.loadingMore = true;

        try {
            const result = await rpc("/ai_assistant/conversation/messages", {
                conversation_id: this.state.conversationId,
                offset: this.state.messages.length,
                limit: 50,
            });
            if (result && Array.isArray(result.messages) && result.messages.length) {
                const olderMessages = result.messages.map(msg => ({
                    ...msg,
                    tables: Array.isArray(msg.tables) ? msg.tables : [],
                }));
                // Prepend older messages
                this.state.messages = [...olderMessages, ...this.state.messages];
                this.state.hasMoreMessages = result.has_more || false;
            } else {
                this.state.hasMoreMessages = false;
            }
        } catch (e) {
            console.error("Failed to load more messages:", e);
        } finally {
            this.state.loadingMore = false;
        }
    }

    onModelChange(ev) {
        this.state.selectedModel = ev.target.value;
    }

    async onSend() {
        const query = this.state.inputValue.trim();
        if (!query || this.state.loading) return;

        this.state.messages.push({
            role: "user",
            content: query,
            model_accessed: "",
            records_accessed: 0,
        });
        this.state.inputValue = "";
        this.state.loading = true;
        this.scrollToBottom();

        try {
            const result = await rpc("/ai_assistant/ask", {
                query: query,
                conversation_id: this.state.conversationId,
                model: this.state.selectedModel || null,
            });

            if (result && result.success) {
                this.state.messages.push({
                    role: "assistant",
                    content: result.response || "",
                    tables: Array.isArray(result.tables) ? result.tables : [],
                    response_type: result.response_type || "text",
                    model_accessed: result.model_accessed || "",
                    records_accessed: result.records_found || 0,
                    response_time: result.response_time || 0,
                });

                if (result.conversation_id) {
                    this.state.conversationId = result.conversation_id;
                }
                this.loadConversations();
            } else {
                this.state.messages.push({
                    role: "system",
                    content: (result && result.response) || (result && result.error) || _t("Unknown error"),
                    tables: [],
                    model_accessed: "",
                    records_accessed: 0,
                });
            }
        } catch (e) {
            this.state.messages.push({
                role: "system",
                content: _t("Connection error: ") + (e.message || e),
                tables: [],
                model_accessed: "",
                records_accessed: 0,
            });
        } finally {
            this.state.loading = false;
            this.scrollToBottom();
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        }
    }

    onInputChange(ev) {
        this.state.inputValue = ev.target.value;
    }

    onKeyDown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.onSend();
        }
    }

    onExampleClick(query) {
        this.state.inputValue = query;
        this.onSend();
    }

    onNewConversation() {
        this.state.conversationId = null;
        this.state.messages = [];
        this.state.hasMoreMessages = false;
        if (this.inputRef.el) {
            this.inputRef.el.focus();
        }
    }

    async onSelectConversation(conversationId) {
        this.state.conversationId = conversationId;
        await this.loadMessages(conversationId);
    }

    async onDeleteConversation(ev, conversationId) {
        ev.stopPropagation();
        if (!confirm(_t("Delete this conversation?"))) return;

        try {
            const result = await rpc("/ai_assistant/conversation/delete", {
                conversation_id: conversationId,
            });
            if (result && result.success) {
                if (this.state.conversationId === conversationId) {
                    this.state.conversationId = null;
                    this.state.messages = [];
                }
                this.loadConversations();
                this.notification.add(_t("Conversation deleted"), { type: "success" });
            }
        } catch (e) {
            this.notification.add(_t("Failed to delete conversation"), { type: "danger" });
        }
    }

    // ── Rename conversation ──────────────────────────────────────
    onStartRename(ev, conv) {
        ev.stopPropagation();
        this.state.renamingConvId = conv.id;
        this.state.renameValue = conv.name || "";
    }

    onRenameInput(ev) {
        this.state.renameValue = ev.target.value;
    }

    async onRenameKeyDown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            await this.onSaveRename();
        } else if (ev.key === "Escape") {
            this.state.renamingConvId = null;
            this.state.renameValue = "";
        }
    }

    async onSaveRename() {
        const name = this.state.renameValue.trim();
        if (!name || !this.state.renamingConvId) {
            this.state.renamingConvId = null;
            return;
        }
        try {
            const result = await rpc("/ai_assistant/conversation/rename", {
                conversation_id: this.state.renamingConvId,
                name: name,
            });
            if (result && result.success) {
                this.notification.add(_t("Conversation renamed"), { type: "success" });
                this.loadConversations();
            }
        } catch (e) {
            this.notification.add(_t("Failed to rename"), { type: "danger" });
        } finally {
            this.state.renamingConvId = null;
            this.state.renameValue = "";
        }
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
    }

    // ── Export functions ──────────────────────────────────────────
    exportTableCSV(table) {
        const headers = JSON.stringify(table.headers);
        const rows = JSON.stringify(table.rows);
        const label = encodeURIComponent(table.label || 'Export');
        const url = `/ai_assistant/export/csv?headers=${encodeURIComponent(headers)}&rows=${encodeURIComponent(rows)}&label=${label}`;
        window.open(url, '_blank');
    }

    exportTableJSON(table) {
        const headers = JSON.stringify(table.headers);
        const rows = JSON.stringify(table.rows);
        const label = encodeURIComponent(table.label || 'Export');
        const url = `/ai_assistant/export/json?headers=${encodeURIComponent(headers)}&rows=${encodeURIComponent(rows)}&label=${label}`;
        window.open(url, '_blank');
    }

    copyTableToClipboard(table) {
        let text = table.headers.join('\t') + '\n';
        for (const row of table.rows) {
            text += row.join('\t') + '\n';
        }
        navigator.clipboard.writeText(text).then(() => {
            this.notification.add(_t("Table copied to clipboard"), { type: "success" });
        }).catch(() => {
            this.notification.add(_t("Failed to copy"), { type: "danger" });
        });
    }

    downloadChat() {
        if (!this.state.messages.length) {
            this.notification.add(_t("No messages to download"), { type: "warning" });
            return;
        }
        let text = "SDLC AI Assistant - Chat Export\n";
        text += "=" .repeat(50) + "\n\n";
        for (const msg of this.state.messages) {
            const role = msg.role === "user" ? "You" : msg.role === "assistant" ? "AI" : "System";
            text += `[${role}]${msg.create_date ? ' (' + msg.create_date + ')' : ''}\n`;
            text += msg.content + "\n";
            if (msg.tables && msg.tables.length) {
                for (const table of msg.tables) {
                    text += `\n  Table: ${table.label}\n`;
                    text += "  " + table.headers.join(" | ") + "\n";
                    text += "  " + "-".repeat(table.headers.join(" | ").length) + "\n";
                    for (const row of table.rows) {
                        text += "  " + row.join(" | ") + "\n";
                    }
                }
            }
            text += "\n";
        }

        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `chat_export_${new Date().toISOString().slice(0, 10)}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    }

    copyMessageToClipboard(content) {
        navigator.clipboard.writeText(content).then(() => {
            this.notification.add(_t("Message copied to clipboard"), { type: "success" });
        }).catch(() => {
            this.notification.add(_t("Failed to copy"), { type: "danger" });
        });
    }

    scrollToBottom() {
        requestAnimationFrame(() => {
            const el = this.messagesRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }
}

registry.category("actions").add("ai_chat_action", AiChatAction);
