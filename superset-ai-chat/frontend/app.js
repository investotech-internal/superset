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

  // ---- conversation state (Anthropic message format) ----
  let history = [];
  let streaming = false;

  marked.setOptions({ breaks: true, gfm: true });

  function renderMarkdown(text) {
    const raw = marked.parse(text || "");
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
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

  // ---- send a turn ----
  async function sendMessage(text) {
    if (streaming || !text.trim()) return;
    streaming = true;
    sendBtn.disabled = true;

    // user bubble
    const userBody = addMessageRow("user");
    userBody.textContent = text;
    history.push({ role: "user", content: text });

    // assistant container
    const assistantBody = addMessageRow("assistant");
    const textSpan = document.createElement("div");
    textSpan.className = "assistant-text cursor-blink";
    assistantBody.appendChild(textSpan);

    let assistantText = "";
    const toolChips = {};

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });

      if (resp.status === 401) {
        showLogin();
        return;
      }
      if (!resp.ok || !resp.body) {
        throw new Error("Request failed (" + resp.status + ")");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let evt;
          try {
            evt = JSON.parse(payload);
          } catch (e) {
            continue;
          }
          handleEvent(evt);
        }
      }

      function handleEvent(evt) {
        if (evt.type === "text") {
          assistantText += evt.text;
          textSpan.innerHTML = renderMarkdown(assistantText);
          scrollToBottom();
        } else if (evt.type === "tool_use") {
          const chip = document.createElement("div");
          chip.className = "tool-chip running";
          chip.innerHTML =
            '<span class="dot"></span><span>Running <b>' +
            toolLabel(evt.name) +
            "</b>…</span>";
          assistantBody.insertBefore(chip, textSpan);
          if (!toolChips[evt.name]) toolChips[evt.name] = [];
          toolChips[evt.name].push(chip);
          scrollToBottom();
        } else if (evt.type === "tool_result") {
          const chips = toolChips[evt.name];
          const chip = chips && chips.length ? chips.shift() : null;
          if (chip) {
            chip.classList.remove("running");
            if (evt.is_error) {
              chip.classList.add("error");
              chip.innerHTML =
                '<span class="dot"></span><span><b>' +
                toolLabel(evt.name) +
                "</b> failed</span>";
            } else {
              chip.innerHTML =
                '<span class="dot"></span><span>Used <b>' +
                toolLabel(evt.name) +
                "</b></span>";
            }
          }
        } else if (evt.type === "error") {
          const banner = document.createElement("div");
          banner.className = "error-banner";
          banner.textContent = evt.message || "An error occurred.";
          assistantBody.appendChild(banner);
        }
        // "done" handled after loop
      }

      // finalize
      textSpan.classList.remove("cursor-blink");
      if (assistantText.trim()) {
        history.push({ role: "assistant", content: assistantText });
      }
    } catch (err) {
      textSpan.classList.remove("cursor-blink");
      const banner = document.createElement("div");
      banner.className = "error-banner";
      banner.textContent = "Connection error: " + err.message;
      assistantBody.appendChild(banner);
    } finally {
      streaming = false;
      sendBtn.disabled = false;
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
    messagesEl.innerHTML = "";
    showLogin();
  });

  newChatBtn.addEventListener("click", () => {
    if (streaming) return;
    history = [];
    messagesEl.innerHTML = "";
    if (emptyState) {
      messagesEl.appendChild(emptyState);
      emptyState.classList.remove("hidden");
    }
    location.reload();
  });

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
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
      } else {
        showLogin();
      }
    } catch (e) {
      showLogin();
    }
  })();
})();
