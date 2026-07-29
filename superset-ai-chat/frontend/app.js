/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

(() => {
  "use strict";

  // ---- element refs ----
  const loginView = document.getElementById("login-view");
  const chatView = document.getElementById("chat-view");
  const loginForm = document.getElementById("login-form");
  const loginError = document.getElementById("login-error");
  const loginBtn = document.getElementById("login-btn");
  const usernameInput = document.getElementById("login-username");
  const passwordInput = document.getElementById("login-password");

  const messagesEl = document.getElementById("messages");
  const emptyState = document.getElementById("empty-state");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const newChatBtn = document.getElementById("new-chat-btn");
  const logoutBtn = document.getElementById("logout-btn");
  const userNameEl = document.getElementById("user-name");
  const userAvatarEl = document.getElementById("user-avatar");
  const convListEl = document.getElementById("conversation-list");
  const attachBtn = document.getElementById("attach-btn");
  const imageInput = document.getElementById("image-input");
  const attachmentPreviewsEl = document.getElementById("attachment-previews");

  // ---- conversation state (Anthropic message format) ----
  let history = [];
  let streaming = false;
  let currentConversationId = null;
  let conversations = [];
  let activeTaskId = null;

  // Keep the selected conversation through a refresh in this browser tab.
  // sessionStorage intentionally scopes the selection to a single tab and
  // clears it when the tab closes, avoiding cross-user/tab chat restoration.
  const ACTIVE_CONVERSATION_STORAGE_KEY = "superset-ai-chat.activeConversation";

  function saveActiveConversation(id) {
    if (id) {
      sessionStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, id);
    } else {
      sessionStorage.removeItem(ACTIVE_CONVERSATION_STORAGE_KEY);
    }
  }

  function getSavedActiveConversation() {
    return sessionStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY);
  }

  // Pending image attachments for the message currently being composed.
  // Each entry: { id, name, mediaType, base64 (raw, no data: prefix) }.
  let pendingAttachments = [];
  const ALLOWED_IMAGE_TYPES = [
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
  ];
  const IMAGE_TYPE_BY_EXTENSION = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    gif: "image/gif",
    webp: "image/webp",
  };
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024; // 5MB
  const MAX_ATTACHMENTS = 4;

  // Let marked use its version-compatible built-in link renderer. The prior
  // custom callback relied on a parser API that marked@12 does not expose,
  // causing a runtime error for any assistant response containing a link.
  marked.setOptions({ breaks: true, gfm: true });

  function renderMarkdown(text) {
    const raw = marked.parse(text || "");
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target", "rel"] });
  }

  // ---- image attachments ----
  function imageMediaType(file) {
    if (ALLOWED_IMAGE_TYPES.includes(file.type)) return file.type;
    const extension = file.name.split(".").pop().toLowerCase();
    return IMAGE_TYPE_BY_EXTENSION[extension] || null;
  }

  function fileToAttachment(file, mediaType) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // reader.result looks like "data:image/png;base64,AAAA..."
        const base64 = String(reader.result).split(",", 2)[1] || "";
        resolve({
          id: Math.random().toString(36).slice(2),
          name: file.name,
          mediaType,
          base64,
        });
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function attachmentError(message) {
    const banner = document.createElement("div");
    banner.className = "error-banner";
    banner.textContent = message;
    messagesEl.appendChild(banner);
    scrollToBottom();
  }

  async function addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    for (const file of files) {
      const mediaType = imageMediaType(file);
      if (!mediaType) {
        attachmentError(
          `"${file.name || "Clipboard image"}" is not a supported image. Use PNG, JPEG, GIF, or WEBP.`,
        );
        continue;
      }
      if (pendingAttachments.length >= MAX_ATTACHMENTS) {
        attachmentError(
          `You can attach up to ${MAX_ATTACHMENTS} images per message.`,
        );
        break;
      }
      if (file.size > MAX_IMAGE_BYTES) {
        attachmentError(`"${file.name}" is too large (max 5MB).`);
        continue;
      }
      try {
        const attachment = await fileToAttachment(file, mediaType);
        pendingAttachments.push(attachment);
      } catch (e) {
        attachmentError(`Could not read "${file.name}".`);
      }
    }
    renderAttachmentPreviews();
  }

  function removeAttachment(id) {
    pendingAttachments = pendingAttachments.filter((a) => a.id !== id);
    renderAttachmentPreviews();
  }

  function renderAttachmentPreviews() {
    attachmentPreviewsEl.innerHTML = "";
    attachmentPreviewsEl.classList.toggle("hidden", !pendingAttachments.length);
    pendingAttachments.forEach((a) => {
      const thumb = document.createElement("div");
      thumb.className = "attachment-thumb";

      const img = document.createElement("img");
      img.src = `data:${a.mediaType};base64,${a.base64}`;
      img.alt = a.name;
      thumb.appendChild(img);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "attachment-remove";
      removeBtn.setAttribute("aria-label", "Remove image");
      removeBtn.textContent = "\u2715";
      removeBtn.addEventListener("click", () => removeAttachment(a.id));
      thumb.appendChild(removeBtn);

      attachmentPreviewsEl.appendChild(thumb);
    });
  }

  // Renders a user message's content, which may be plain text or an
  // Anthropic-style content-block array mixing text and images.
  function renderMessageContent(body, content) {
    if (typeof content === "string") {
      body.textContent = content;
      return;
    }
    if (Array.isArray(content)) {
      content.forEach((block) => {
        if (!block || typeof block !== "object") return;
        if (block.type === "text" && block.text) {
          const div = document.createElement("div");
          div.className = "msg-text-block";
          div.textContent = block.text;
          body.appendChild(div);
        } else if (block.type === "image" && block.source) {
          const img = document.createElement("img");
          img.className = "attachment-img";
          img.alt = "Attached image";
          if (block.source.type === "base64") {
            img.src = `data:${block.source.media_type};base64,${block.source.data}`;
          } else if (block.source.type === "url" && block.source.url) {
            img.src = block.source.url;
          }
          body.appendChild(img);
        }
      });
      return;
    }
    body.textContent = JSON.stringify(content, null, 2);
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showLogin() {
    loginView.classList.remove("hidden");
    chatView.classList.add("hidden");
  }

  function showChat(user) {
    loginView.classList.add("hidden");
    chatView.classList.remove("hidden");
    const name = (user && (user.display_name || user.username)) || "User";
    userNameEl.textContent = name;
    userAvatarEl.textContent = name.charAt(0);
    chatInput.focus();
    loadConversations();
  }

  function resetChatUI() {
    messagesEl.innerHTML = "";
    if (emptyState) {
      messagesEl.appendChild(emptyState);
      emptyState.classList.remove("hidden");
    }
  }

  // ---- message DOM helpers ----
  function addMessageRow(role) {
    if (emptyState && !emptyState.classList.contains("hidden")) {
      emptyState.classList.add("hidden");
    }
    const row = document.createElement("div");
    row.className = "msg-row";

    const avatar = document.createElement("div");
    avatar.className = "msg-avatar " + role;
    avatar.textContent = role === "user" ? "" : "✦";

    const body = document.createElement("div");
    body.className = "msg-body";

    row.appendChild(avatar);
    row.appendChild(body);
    messagesEl.appendChild(row);
    scrollToBottom();
    return body;
  }

  function toolLabel(name) {
    return name.replace(/_/g, " ");
  }

  // ---- conversation persistence ----
  async function loadConversations() {
    try {
      const resp = await fetch("/api/conversations");
      if (!resp.ok) return;
      conversations = await resp.json();
      renderConversationList();
    } catch (e) {
      /* ignore */
    }
  }

  function renderConversationList() {
    if (!convListEl) return;
    convListEl.innerHTML = "";
    conversations.forEach((c) => {
      const item = document.createElement("div");
      item.className =
        "conv-item" + (c.id === currentConversationId ? " active" : "");

      const title = document.createElement("button");
      title.className = "conv-title";
      title.textContent = c.title || "New chat";
      title.title = c.title || "New chat";
      title.addEventListener("click", () => openConversation(c.id));

      const del = document.createElement("button");
      del.className = "conv-del";
      del.setAttribute("aria-label", "Delete chat");
      del.textContent = "\u2715";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteConversation(c.id);
      });

      item.appendChild(title);
      item.appendChild(del);
      convListEl.appendChild(item);
    });
  }

  function renderHistory() {
    resetChatUI();
    if (emptyState) emptyState.classList.add("hidden");
    history.forEach((m) => {
      if (m.role === "user") {
        const body = addMessageRow("user");
        renderMessageContent(body, m.content);
      } else {
        const content =
          typeof m.content === "string"
            ? m.content
            : JSON.stringify(m.content, null, 2);
        const body = addMessageRow("assistant");
        const span = document.createElement("div");
        span.className = "assistant-text";
        span.innerHTML = renderMarkdown(content);
        body.appendChild(span);
      }
    });
    scrollToBottom();
  }

  async function openConversation(id) {
    if (streaming) return;
    try {
      const resp = await fetch("/api/conversations/" + encodeURIComponent(id));
      if (!resp.ok) {
        if (resp.status === 404 && id === getSavedActiveConversation()) {
          saveActiveConversation(null);
        }
        return;
      }
      const conv = await resp.json();
      currentConversationId = conv.id;
      saveActiveConversation(currentConversationId);
      history = Array.isArray(conv.messages) ? conv.messages : [];
      renderHistory();
      renderConversationList();
      if (conv.active_task) {
        showTaskInProgress(conv.active_task.id);
      }
    } catch (e) {
      /* ignore */
    }
  }

  async function deleteConversation(id) {
    if (!window.confirm("Delete this chat?")) return;
    try {
      const resp = await fetch("/api/conversations/" + encodeURIComponent(id), {
        method: "DELETE",
      });
      if (!resp.ok) return;
      if (id === currentConversationId) {
        currentConversationId = null;
        saveActiveConversation(null);
        history = [];
        resetChatUI();
      }
      loadConversations();
    } catch (e) {
      /* ignore */
    }
  }

  async function ensureConversation(firstText) {
    if (currentConversationId) return;
    try {
      const resp = await fetch("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: firstText.slice(0, 80) }),
      });
      if (resp.ok) {
        const data = await resp.json();
        currentConversationId = data.id;
        saveActiveConversation(currentConversationId);
      }
    } catch (e) {
      /* ignore */
    }
  }

  async function persistConversation() {
    if (!currentConversationId || !history.length) return;
    try {
      await fetch(
        "/api/conversations/" + encodeURIComponent(currentConversationId),
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: history }),
        },
      );
    } catch (e) {
      /* ignore */
    }
    loadConversations();
  }

  function setStreaming(value) {
    streaming = value;
    sendBtn.disabled = value;
  }

  function showTaskInProgress(taskId) {
    if (activeTaskId === taskId) return;
    activeTaskId = taskId;
    setStreaming(true);
    const assistantBody = addMessageRow("assistant");
    const textSpan = document.createElement("div");
    textSpan.className = "assistant-text cursor-blink";
    textSpan.textContent = "Still working on your request…";
    assistantBody.appendChild(textSpan);
    monitorTask(taskId, assistantBody, textSpan);
  }

  async function monitorTask(taskId, assistantBody, textSpan) {
    try {
      while (activeTaskId === taskId) {
        const resp = await fetch("/api/chat/" + encodeURIComponent(taskId));
        if (!resp.ok) throw new Error("Could not check task status.");
        const task = await resp.json();
        if (task.status === "running") {
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
          continue;
        }
        textSpan.classList.remove("cursor-blink");
        activeTaskId = null;
        setStreaming(false);
        if (task.status === "complete") {
          await openConversation(currentConversationId);
          return;
        }
        const banner = document.createElement("div");
        banner.className = "error-banner";
        banner.textContent = task.error || "The chat task did not complete.";
        assistantBody.appendChild(banner);
        return;
      }
    } catch (err) {
      textSpan.classList.remove("cursor-blink");
      activeTaskId = null;
      setStreaming(false);
      const banner = document.createElement("div");
      banner.className = "error-banner";
      banner.textContent = "Connection error: " + err.message;
      assistantBody.appendChild(banner);
    }
  }

  // ---- send a turn ----
  async function sendMessage(text) {
    if (streaming || (!text.trim() && !pendingAttachments.length)) return;
    setStreaming(true);

    // Build the Anthropic-style content: plain text when there are no
    // attachments (backward compatible), or a content-block array of
    // images + text when the user attached/pasted images.
    const attachments = pendingAttachments;
    pendingAttachments = [];
    renderAttachmentPreviews();

    let content;
    if (attachments.length) {
      content = attachments.map((a) => ({
        type: "image",
        source: { type: "base64", media_type: a.mediaType, data: a.base64 },
      }));
      content.push({
        type: "text",
        text: text.trim() || "Please analyze the attached image(s).",
      });
    } else {
      content = text;
    }

    // user bubble
    const userBody = addMessageRow("user");
    renderMessageContent(userBody, content);
    history.push({ role: "user", content });

    // create a conversation on first message so it shows in the sidebar
    await ensureConversation(text || "Image");

    // assistant container
    const assistantBody = addMessageRow("assistant");
    const textSpan = document.createElement("div");
    textSpan.className = "assistant-text cursor-blink";
    assistantBody.appendChild(textSpan);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: currentConversationId,
          messages: history,
        }),
      });

      if (resp.status === 401) {
        showLogin();
        return;
      }
      if (!resp.ok) {
        const error = await resp.json().catch(() => ({}));
        const detail = Array.isArray(error.detail)
          ? error.detail
              .map((item) => item.msg)
              .filter(Boolean)
              .join("; ")
          : error.detail;
        throw new Error(detail || "Request failed (" + resp.status + ")");
      }
      const task = await resp.json();
      activeTaskId = task.task_id;
      textSpan.textContent = "Working on your request…";
      monitorTask(task.task_id, assistantBody, textSpan);
    } catch (err) {
      textSpan.classList.remove("cursor-blink");
      const banner = document.createElement("div");
      banner.className = "error-banner";
      banner.textContent = "Connection error: " + err.message;
      assistantBody.appendChild(banner);
    } finally {
      if (!activeTaskId) {
        setStreaming(false);
      }
      chatInput.focus();
      scrollToBottom();
    }
  }

  // ---- events ----
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    loginError.classList.add("hidden");
    loginBtn.disabled = true;
    loginBtn.textContent = "Signing in…";
    try {
      const resp = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: usernameInput.value,
          password: passwordInput.value,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || "Login failed");
      }
      showChat(data);
    } catch (err) {
      loginError.textContent = err.message;
      loginError.classList.remove("hidden");
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = "Sign in";
    }
  });

  logoutBtn.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    history = [];
    currentConversationId = null;
    saveActiveConversation(null);
    conversations = [];
    pendingAttachments = [];
    renderAttachmentPreviews();
    if (convListEl) convListEl.innerHTML = "";
    messagesEl.innerHTML = "";
    showLogin();
  });

  newChatBtn.addEventListener("click", () => {
    if (streaming) return;
    currentConversationId = null;
    saveActiveConversation(null);
    history = [];
    pendingAttachments = [];
    renderAttachmentPreviews();
    resetChatUI();
    renderConversationList();
    chatInput.focus();
  });

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text && !pendingAttachments.length) return;
    chatInput.value = "";
    autoGrow();
    sendMessage(text);
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  function autoGrow() {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + "px";
  }
  chatInput.addEventListener("input", autoGrow);

  // ---- image attach / paste / drop ----
  attachBtn.addEventListener("click", () => {
    if (streaming) return;
    imageInput.click();
  });

  imageInput.addEventListener("change", () => {
    addFiles(imageInput.files);
    imageInput.value = "";
  });

  chatInput.addEventListener("paste", (e) => {
    const items = Array.from(e.clipboardData ? e.clipboardData.items : []);
    const imageFiles = items
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (imageFiles.length) {
      e.preventDefault();
      addFiles(imageFiles);
    }
  });

  ["dragover", "dragenter"].forEach((evt) => {
    chatForm.addEventListener(evt, (e) => {
      if (
        e.dataTransfer &&
        Array.from(e.dataTransfer.types).includes("Files")
      ) {
        e.preventDefault();
      }
    });
  });
  chatForm.addEventListener("drop", (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  });

  document.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
      chatInput.value = btn.textContent;
      chatForm.requestSubmit();
    });
  });

  // ---- init: check existing session ----
  (async () => {
    try {
      const resp = await fetch("/api/me");
      if (resp.ok) {
        showChat(await resp.json());
        const activeConversationId = getSavedActiveConversation();
        if (activeConversationId) {
          await openConversation(activeConversationId);
        }
      } else {
        showLogin();
      }
    } catch (e) {
      showLogin();
    }
  })();
})();
