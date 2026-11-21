// CFT assistant frontend. Session ownership is scoped to a browser-generated client UUID.
const API_BASE_URL = "";
const CHAT_ENDPOINT = "/api/chat";
const SESSION_ENDPOINT = "/api/sessions";

const nav = document.getElementById("mainNav");
const menuToggle = document.getElementById("menuToggle");
menuToggle.addEventListener("click", () => {
    const opened = nav.classList.toggle("open");
    menuToggle.setAttribute("aria-expanded", String(opened));
});
document.querySelectorAll(".nav-link").forEach(link => {
    link.addEventListener("click", () => {
        nav.classList.remove("open");
        menuToggle.setAttribute("aria-expanded", "false");
        document.querySelectorAll(".nav-link").forEach(item => item.classList.toggle("active", item === link));
    });
});

const techSearch = document.getElementById("technologySearch");
const techFilters = document.getElementById("technologyFilters");
let selectedCategory = "all";
function updateTechnologyList() {
    const query = techSearch.value.trim().toLocaleLowerCase("ru");
    document.querySelectorAll(".technology-card").forEach(card => {
        const categoryOk = selectedCategory === "all" || card.dataset.category === selectedCategory;
        const queryOk = !query || card.textContent.toLocaleLowerCase("ru").includes(query);
        card.hidden = !(categoryOk && queryOk);
    });
}
techSearch.addEventListener("input", updateTechnologyList);
techFilters.addEventListener("click", event => {
    const button = event.target.closest("[data-filter]");
    if (!button) return;
    selectedCategory = button.dataset.filter;
    techFilters.querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
    updateTechnologyList();
});

const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const chatMessages = document.getElementById("chatMessages");
const chatStatus = document.getElementById("chatStatus");
const sendButton = document.getElementById("sendButton");
const historyList = document.getElementById("historyList");
const knowledgeStatus = document.getElementById("knowledgeStatus");
const knowledgeStats = document.getElementById("knowledgeStats");
const knowledgeSources = document.getElementById("knowledgeSources");
let clientToken = localStorage.getItem("cft_assistant_client_token");
let currentSessionId = localStorage.getItem("cft_assistant_active_session");
let isSending = false;
let pendingRequestId = null;
let pendingRequestMessage = null;

async function ensureClientToken() {
    if (clientToken) return clientToken;
    const response = await fetch("/api/client/bootstrap", { method: "POST" });
    if (!response.ok) throw new Error("Не удалось создать локальную сессию браузера");
    const data = await response.json();
    clientToken = data.client_token;
    localStorage.setItem("cft_assistant_client_token", clientToken);
    return clientToken;
}

function apiHeaders() {
    return { "Content-Type": "application/json", "X-Client-Token": clientToken || "" };
}

function appendMessage(text, role, sources = []) {
    const row = document.createElement("div");
    row.className = `chat-message ${role === "user" ? "user-message" : "assistant-message"}`;
    const avatar = document.createElement("div");
    avatar.className = "chat-avatar";
    avatar.textContent = role === "user" ? "Вы" : "Ц";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    const caption = document.createElement("span");
    caption.className = "message-caption";
    caption.textContent = role === "user" ? "ВЫ" : "АССИСТЕНТ";
    const messageContent = document.createElement("div");
    messageContent.className = "chat-markdown";

    if (role === "assistant") {
        console.log("Markdown libraries:", {
            marked: typeof window.marked,
            parse: typeof window.marked?.parse,
            dompurify: typeof window.DOMPurify,
            sanitize: typeof window.DOMPurify?.sanitize
        });
    }

    if (role === "assistant" && window.marked && window.DOMPurify) {
        const html = window.marked.parse(text || "");
        messageContent.innerHTML = window.DOMPurify.sanitize(html);
    } else {
        // Сообщения пользователя оставляем обычным текстом
        messageContent.textContent = text || "";
    }

    bubble.append(caption, messageContent);

    if (Array.isArray(sources) && sources.length) {
        const sourceBlock = document.createElement("div");
        sourceBlock.className = "message-sources";
        const label = document.createElement("strong");
        label.textContent = "Подтверждающие источники";
        sourceBlock.append(label);
        sources.forEach(source => {
            const item = document.createElement("div");
            const fileUrl = source.source_id
                ? `/api/knowledge/files/${encodeURIComponent(source.source_id)}`
                : null;
            const href = source.url && /^https?:\/\//i.test(source.url)
                ? source.url
                : fileUrl;
            if (href) {
                const anchor = document.createElement("a");
                anchor.href = href;
                anchor.target = "_blank";
                anchor.rel = "noopener noreferrer";
                anchor.textContent = source.title || source.file_name || "Открыть источник";
                item.append(anchor);
            } else {
                item.textContent = source.title || source.file_name || "Материал базы знаний";
            }
            if (source.file_name) item.append(document.createTextNode(` · ${source.file_name}`));
            if (source.page) item.append(document.createTextNode(` · ${source.page}`));
            if (source.section) item.append(document.createTextNode(` · ${source.section}`));
            sourceBlock.append(item);
        });
        bubble.append(sourceBlock);
    }
    row.append(avatar, bubble);
    chatMessages.append(row);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function resetChatWelcome() {
    chatMessages.replaceChildren();
    appendMessage(
        "Здравствуйте! Я ассистент по продуктам, технологиям и материалам АО «ЦФТ». Отвечаю по подключённой базе знаний и показываю использованные источники.",
        "assistant"
    );
}

async function apiRequest(url, options = {}) {
    await ensureClientToken();
    let response = await fetch(`${API_BASE_URL}${url}`, {
        ...options,
        headers: { ...apiHeaders(), ...(options.headers || {}) }
    });
    if (response.status === 401 && url !== "/api/client/bootstrap") {
        clientToken = null;
        localStorage.removeItem("cft_assistant_client_token");
        await ensureClientToken();
        response = await fetch(`${API_BASE_URL}${url}`, {
            ...options,
            headers: { ...apiHeaders(), ...(options.headers || {}) }
        });
    }
    if (!response.ok) {
        const details = await response.text().catch(() => "");
        throw new Error(`HTTP ${response.status}${details ? `: ${details.slice(0, 220)}` : ""}`);
    }
    return response.json();
}

async function createSession() {
    const data = await apiRequest(SESSION_ENDPOINT, { method: "POST", body: JSON.stringify({}) });
    currentSessionId = data.session_id;
    localStorage.setItem("cft_assistant_active_session", currentSessionId);
    await refreshSessions();
    return currentSessionId;
}

async function refreshSessions() {
    if (!historyList) return;
    try {
        const sessions = await apiRequest(SESSION_ENDPOINT);
        historyList.replaceChildren();
        sessions.forEach(item => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "history-item";
            button.textContent = item.title || "Новый диалог";
            button.classList.toggle("active", item.session_id === currentSessionId);
            button.addEventListener("click", () => loadSession(item.session_id));
            historyList.append(button);
        });
        if (!sessions.length) {
            const empty = document.createElement("span");
            empty.className = "history-empty";
            empty.textContent = "Сохранённых диалогов пока нет";
            historyList.append(empty);
        }
    } catch (error) {
        chatStatus.textContent = "Не удалось загрузить историю. Повторите позже.";
        console.error(error);
    }
}

async function loadSession(sessionId) {
    if (isSending) return;
    try {
        const messages = await apiRequest(`${SESSION_ENDPOINT}/${encodeURIComponent(sessionId)}/history`);
        currentSessionId = sessionId;
        localStorage.setItem("cft_assistant_active_session", currentSessionId);
        chatMessages.replaceChildren();
        messages.forEach(message => appendMessage(message.content, message.role, message.sources || []));
        if (!messages.length) resetChatWelcome();
        await refreshSessions();
        chatStatus.textContent = "История диалога загружена.";
    } catch (error) {
        chatStatus.textContent = "Не удалось открыть этот диалог.";
        if (currentSessionId === sessionId) {
            currentSessionId = null;
            localStorage.removeItem("cft_assistant_active_session");
            resetChatWelcome();
        }
        console.error(error);
    }
}

async function sendToApi(message) {
    if (pendingRequestMessage !== message) {
        pendingRequestMessage = message;
        pendingRequestId = crypto.randomUUID();
    }
    return apiRequest(CHAT_ENDPOINT, {
        method: "POST",
        body: JSON.stringify({ message, session_id: currentSessionId, request_id: pendingRequestId })
    });
}

chatForm.addEventListener("submit", async event => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message || isSending) return;
    appendMessage(message, "user");
    chatInput.value = "";
    isSending = true;
    sendButton.disabled = true;
    chatInput.disabled = true;
    chatStatus.textContent = "Ищу материалы ЦФТ и формирую ответ…";
    chatMessages.setAttribute("aria-busy", "true");
    try {
        if (!currentSessionId) await createSession();
        const data = await sendToApi(message);
        currentSessionId = data.session_id || currentSessionId;
        if (currentSessionId) localStorage.setItem("cft_assistant_active_session", currentSessionId);
        appendMessage(data.answer || "Сервер вернул пустой ответ.", "assistant", data.sources || []);
        const elapsed = Number.isFinite(data.processing_time_ms)
            ? ` Время обработки: ${(data.processing_time_ms / 1000).toFixed(2)} с.`
            : "";
        pendingRequestId = null;
        pendingRequestMessage = null;
        chatStatus.textContent = data.needs_clarification
            ? `В базе недостаточно подтверждений. Уточните вопрос или загрузите дополнительные материалы.${elapsed}`
            : `Ответ сформирован. Источники приведены под сообщением.${elapsed}`;
        await refreshSessions();
    } catch (error) {
        if (String(error.message || error).includes("HTTP 404")) {
            currentSessionId = null;
            localStorage.removeItem("cft_assistant_active_session");
        }
        chatStatus.textContent = "Не удалось получить ответ. Текст вопроса восстановлен в поле ввода — исправьте настройки или повторите отправку.";
        chatInput.value = message;
        appendMessage("Сейчас не удалось обработать запрос. Можно повторить его после восстановления соединения.", "assistant");
        console.error(error);
    } finally {
        isSending = false;
        sendButton.disabled = false;
        chatInput.disabled = false;
        chatMessages.setAttribute("aria-busy", "false");
        chatInput.focus();
    }
});

document.querySelectorAll("[data-prompt]").forEach(button => {
    button.addEventListener("click", () => {
        if (isSending) return;
        chatInput.value = button.dataset.prompt;
        chatForm.requestSubmit();
    });
});

document.getElementById("newChatButton").addEventListener("click", () => {
    if (isSending) return;
    currentSessionId = null;
    localStorage.removeItem("cft_assistant_active_session");
    resetChatWelcome();
    chatStatus.textContent = "Новый диалог. История предыдущих разговоров сохранена.";
    refreshSessions();
});

chatInput.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        chatForm.requestSubmit();
    }
});

async function loadKnowledge() {
    if (!knowledgeStatus || !knowledgeStats) return;
    try {
        const [stats, sources] = await Promise.all([
            apiRequest("/api/knowledge/stats"),
            apiRequest("/api/knowledge/sources")
        ]);
        knowledgeStatus.textContent = stats.documents
            ? "База знаний подключена"
            : "В базе пока нет документов";
        knowledgeStats.textContent = `${stats.documents} документов · ${stats.active_versions} активных версий · ${stats.chunks} фрагментов`;
        if (knowledgeSources) {
            knowledgeSources.replaceChildren();
            sources.slice(0, 8).forEach(source => {
                const item = document.createElement("div");
                const href = source.source || (source.source_id
                    ? `/api/knowledge/files/${encodeURIComponent(source.source_id)}` : null);
                if (href) {
                    const link = document.createElement("a");
                    link.href = href;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    link.textContent = source.title || source.file_name || href;
                    item.append(link);
                } else {
                    item.textContent = source.title || source.file_name || "Материал ЦФТ";
                }
                knowledgeSources.append(item);
            });
        }
    } catch (error) {
        knowledgeStatus.textContent = "Не удалось получить состояние базы";
        knowledgeStats.textContent = "Проверьте доступность backend и повторите загрузку страницы.";
        console.error(error);
    }
}

resetChatWelcome();
refreshSessions();
loadKnowledge();


// Restore the last active conversation after reload.
(async function restoreActiveConversation() {
    try {
        await ensureClientToken();
        await refreshSessions();
        if (currentSessionId) {
            await loadSession(currentSessionId);
        }
    } catch (error) {
        console.error("Session restore failed", error);
        chatStatus.textContent = "Не удалось восстановить историю. Можно начать новый диалог.";
    }
})();
