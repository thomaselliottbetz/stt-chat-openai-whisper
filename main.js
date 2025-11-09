/**
 * Main application JavaScript for chat interface.
 * Handles authentication, messaging, speech-to-text, and admin chat management.
 */
document.addEventListener("DOMContentLoaded", () => {
  // Configuration
  const WS_URL = `wss://${window.location.host}/ws`;
  const MAX_RECORD_TIME = 15;

  // Application state
  let ws;
  let userChatId = null;
  let oldestId = null;
  let loadingOlder = false;
  let intentionalClose = false;
  let validatedInviteCode = null;
  let isAdmin = false;
  let activeChatId = null;
  let chats = {};
  let chatMeta = {};
  let scrollHandlerAttached = false;

  // DOM elements
  const chatModal = document.getElementById("chatModal");
  const loginModal = document.getElementById("loginModal");
  const landingView = document.getElementById("landingView");
  const currentUsername = document.getElementById("currentUsername");
  const loginForm = document.getElementById("loginForm");
  const loginError = document.getElementById("loginError");
  const loginOverlay = document.getElementById("loginModal");
  const loginBox = document.querySelector(".modal-content");
  const closeBtn = document.getElementById("closeLoginModal");
  const inviteForm = document.getElementById("inviteForm");
  const inviteInput = document.getElementById("inviteCode");
  const inviteError = document.getElementById("inviteError");
  const registerSection = document.getElementById("registerSection");
  const regUsernameInput = document.getElementById("registerUsername");
  const regPasswordInput = document.getElementById("registerPassword");
  const registerBtn = document.getElementById("registerBtn");
  const registerError = document.getElementById("registerError");

  // Close on clicking outside the modal box
  loginOverlay.addEventListener("click", (e) => {
    if (!loginBox.contains(e.target)) {
      loginOverlay.style.display = "none";
    }
  });

  // Close on clicking the X
  closeBtn.addEventListener("click", () => {
    loginOverlay.style.display = "none";
  });

  const micFab = document.getElementById("micFab");
  if (micFab) {
    let recorder;
    let chunks = [];
    let isRecording = false;
    let countdown = 15;
    let countdownInterval = null;
    let autoStopTimeout = null;

    micFab.addEventListener("click", async () => {
      if (isRecording) {
        stopRecording();
        return;
      }
      startRecording();
    });

    const icon = micFab.querySelector(".mic-icon");

    function showCountdown() {
      const span = document.createElement("span");
      span.className = "countdown";
      span.textContent = MAX_RECORD_TIME;
      micFab.appendChild(span);

      let remaining = MAX_RECORD_TIME;
      countdownInterval = setInterval(() => {
        remaining--;
        span.textContent = remaining;
        if (remaining <= 0) stopRecording();
      }, 1000);
    }

    function removeCountdown() {
      const span = micFab.querySelector(".countdown");
      if (span) span.remove();
      clearInterval(countdownInterval);
    }

    async function startRecording() {
      // reset timers
      clearInterval(countdownInterval);
      clearTimeout(autoStopTimeout);
      icon.style.display = "none"; // hide mic if you plan to show digits
      showCountdown();

      // visual feedback
      micFab.classList.add("recording");

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      chunks = [];
      isRecording = true;

      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = onRecordingStop;
      recorder.start();

      // auto-stop safety
      autoStopTimeout = setTimeout(stopRecording, MAX_RECORD_TIME * 1000);
    }

    function stopRecording() {
      if (!isRecording) return;
      isRecording = false;
      clearInterval(countdownInterval);
      clearTimeout(autoStopTimeout);
      removeCountdown();
      icon.style.display = "block"; // restore SVG
      micFab.classList.remove("recording");

      if (recorder?.state === "recording") {
        recorder.stop();
      }
      if (recorder?.stream) {
        recorder.stream.getTracks().forEach((t) => t.stop());
      }
    }

    async function onRecordingStop() {
      const blob = new Blob(chunks, { type: "audio/webm" });

      try {
        // Admin sends chat_id, regular users don't
        const body = isAdmin && activeChatId 
          ? JSON.stringify({ chat_id: activeChatId })
          : undefined;
        const r = await fetch("/api/get-presigned-url", {
          method: "POST",
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body,
        });
        const { url, key } = await r.json();
        await fetch(url, {
          method: "PUT",
          headers: { "Content-Type": "audio/webm" },
          body: blob,
        });
      } catch (err) {
        // Silently fail - user can retry
      }
    }
  }

  /**
   * Initialize WebSocket connection for real-time messaging.
   * @param {string} token - Session token for authentication
   */
  function initWebSocket(token) {
    intentionalClose = false;
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (e) {
        return;
      }

      // Ignore keepalive ping messages
      if (message.type === "ping") {
        return;
      }

      // Speech transcription → show in textarea instead of chat
      if (message.type === "transcription") {
        const ta = document.getElementById("userText");
        if (ta) {
          ta.value = message.text.trim();
          ta.focus();
          ta.classList.add("highlight");
          setTimeout(() => ta.classList.remove("highlight"), 800);
        }
        return;
      }

      // Handle regular messages
      if (message.type === "message" || !message.type) {
        if (isAdmin) {
          const chatId = message.chat_id;
          if (chatId) {
            appendAdminMessage(chatId, {
              sender: message.sender,
              text: message.text,
              timestamp: message.timestamp,
            });
          }
        } else {
          // Security: backend filters, but we double-check chat_id
          const messageChatId = message.chat_id;
          if (messageChatId && messageChatId !== userChatId) {
            return;
          }

          const box = document.getElementById("transcriptionBox");
          const div = document.createElement("div");
          div.className = "message";
          div.textContent = `[${message.timestamp}] ${message.sender}: ${message.text}`;
          box.appendChild(div);
          box.scrollTop = box.scrollHeight;
        }
      }
    };

    ws.onclose = () => {
      if (!intentionalClose) {
        alert("Connection closed. Please refresh or log in again.");
      }
    };
  }

  /**
   * Check if user is authenticated and initialize chat interface.
   */
  async function checkAuth() {
    const res = await fetch("/api/me");
    if (res.ok) {
      let data;
      try {
        data = await res.json();
      } catch (e) {
        alert("Server returned malformed data.");
        return;
      }

      // Server determines admin status - client doesn't need to know admin username
      isAdmin = data.isAdmin === true;
      currentUsername.textContent = `👤 ${data.username}`;

      // Show chat selector button only for admin
      const selectChatBtn = document.getElementById("selectChatBtn");
      if (isAdmin && selectChatBtn) {
        selectChatBtn.classList.add("show");
        selectChatBtn.style.display = "inline-flex";
      } else if (selectChatBtn) {
        selectChatBtn.classList.remove("show");
        selectChatBtn.style.display = "none";
      }

      loginModal.style.display = "none";
      chatModal.style.display = "flex";
      landingView.style.display = "none";
      initWebSocket(data.token);

      if (isAdmin) {
        await loadAdminChats();
      } else {
        await loadUserInitialHistory();
      }
    } else {
      loginModal.style.display = "flex";
      landingView.style.display = "block";
    }
  }

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;

    const formData = new FormData();
    formData.append("username", username);
    formData.append("password", password);

    const res = await fetch("/api/login", { method: "POST", body: formData });

    let data;
    try {
      data = await res.json();
    } catch (e) {
      alert("Login failed: server returned invalid JSON.");
      return;
    }

    if (!res.ok) {
      alert(data.detail || "Login failed");
      return;
    }

    await checkAuth();
  });

  /**
   * Handle user logout.
   */
  async function handleLogout() {
    intentionalClose = true;
    await fetch("/api/logout", { method: "POST" });
    if (ws) ws.close();
    chatModal.style.display = "none";
    landingView.style.display = "block";
    document.getElementById("transcriptionBox").innerHTML = "";
  }

  document.getElementById("logoutBtn").addEventListener("click", handleLogout);

  /**
   * Load initial chat history for regular users.
   * Regular users should only see their chat with admin.
   */
  async function loadUserInitialHistory() {
    if (isAdmin) {
      return;
    }

    try {
      const r1 = await fetch("/api/chats");
      if (!r1.ok) return;
      const list = await r1.json();
      if (!Array.isArray(list) || list.length === 0) return;

      // Security: backend filters, but we only use the first chat
      userChatId = list[0].chat_id;

      const r2 = await fetch(
        `/api/get-messages?chat_id=${encodeURIComponent(userChatId)}`
      );
      if (!r2.ok) return;
      const msgs = await r2.json();

      const box = document.getElementById("transcriptionBox");
      box.innerHTML = "";
      for (const m of msgs) {
        const div = document.createElement("div");
        div.className = "message";
        div.textContent = `[${m.timestamp}] ${m.sender}: ${m.text}`;
        box.appendChild(div);
      }

      if (msgs.length > 0) {
        oldestId = msgs[0].id;
      }

      box.scrollTop = box.scrollHeight;
      attachUserScrollLoader();
    } catch (e) {
      // Silently fail - user can retry
    }
  }

  /**
   * Attach infinite scroll loader for user chat messages.
   */
  function attachUserScrollLoader() {
    const box = document.getElementById("transcriptionBox");
    if (!box || box.dataset.scrollHook === "1") return;
    box.dataset.scrollHook = "1";

    box.addEventListener("scroll", async () => {
      if (box.scrollTop <= 12) {
        await loadOlderUserMessages();
      }
    });
  }

  /**
   * Load older messages for regular user chat (infinite scroll).
   */
  async function loadOlderUserMessages() {
    if (loadingOlder || !userChatId || !oldestId) return;
    loadingOlder = true;

    try {
      const r = await fetch(
        `/api/get-messages?chat_id=${encodeURIComponent(
          userChatId
        )}&before_id=${encodeURIComponent(oldestId)}`
      );
      if (!r.ok) return;

      const msgs = await r.json();
      if (!Array.isArray(msgs) || msgs.length === 0) return;

      const box = document.getElementById("transcriptionBox");
      const prevBottom = box.scrollHeight - box.scrollTop;

      // Prepend older messages (iterate backwards to preserve order)
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        const div = document.createElement("div");
        div.className = "message";
        div.textContent = `[${m.timestamp}] ${m.sender}: ${m.text}`;
        box.insertBefore(div, box.firstChild);
      }

      oldestId = msgs[0].id;
      box.scrollTop = box.scrollHeight - prevBottom;
    } catch (e) {
      // Silently fail - user can retry by scrolling
    } finally {
      loadingOlder = false;
    }
  }

  /**
   * Update register button state based on form validation.
   */
  function updateRegisterButtonState() {
    const ok =
      !!validatedInviteCode &&
      regUsernameInput.value.trim().length > 0 &&
      regPasswordInput.value.length > 0;
    registerBtn.disabled = !ok;
  }

  /**
   * Validate invite code and reveal registration form.
   */
  inviteForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    inviteError.textContent = "";
    registerError.textContent = "";

    const code = inviteInput.value.trim();
    if (!code) return;

    try {
      const res = await fetch(
        `/api/validate-invite?code=${encodeURIComponent(code)}`
      );
      const msg = await res.json().catch(() => ({}));

      if (res.ok) {
        validatedInviteCode = code;
        inviteError.style.color = "green";
        inviteError.textContent = "Invite valid! You can now register.";
        registerSection.style.display = "block";
        inviteInput.readOnly = true;
        updateRegisterButtonState();
      } else {
        inviteError.style.color = "red";
        inviteError.textContent = msg.detail || "Invalid code, try again.";
        validatedInviteCode = null;
        registerSection.style.display = "none";
        updateRegisterButtonState();
      }
    } catch (err) {
      inviteError.style.color = "red";
      inviteError.textContent = "Network error validating invite.";
    }
  });

  regUsernameInput.addEventListener("input", updateRegisterButtonState);
  regPasswordInput.addEventListener("input", updateRegisterButtonState);

  /**
   * Submit registration form.
   */
  registerBtn.addEventListener("click", async () => {
    registerError.textContent = "";

    const username = regUsernameInput.value.trim();
    const password = regPasswordInput.value;
    if (!validatedInviteCode || !username || !password) return;

    registerBtn.disabled = true;

    try {
      const fd = new FormData();
      fd.append("username", username);
      fd.append("password", password);
      fd.append("invite_code", validatedInviteCode);

      const res = await fetch("/api/register", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        registerError.textContent = data.detail || "Registration failed.";
        registerBtn.disabled = false;
        return;
      }

      // Auto-login after successful registration
      try {
        const loginFd = new FormData();
        loginFd.append("username", username);
        loginFd.append("password", password);
        const loginRes = await fetch("/api/login", {
          method: "POST",
          body: loginFd,
        });
        const loginData = await loginRes.json().catch(() => ({}));
        if (loginRes.ok && loginData.redirect) {
          window.location.href = loginData.redirect;
          return;
        }
        registerError.textContent = "Registered! Please log in.";
      } catch {
        registerError.textContent = "Registered! Please log in.";
      } finally {
        registerBtn.disabled = false;
      }
    } catch (err) {
      registerError.textContent = "Network error during registration.";
      registerBtn.disabled = false;
    }
  });

  /**
   * Send a text message to the current chat.
   */
  async function sendUserText() {
    const ta = document.getElementById("userText");
    if (!ta) return;

    const text = ta.value.trim();
    if (!text) return;

    const chatId = isAdmin ? activeChatId : userChatId;
    if (!chatId) return;

    const res = await fetch("/api/send-message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
    if (!res.ok) {
      return;
    }

    ta.value = "";
    ta.focus();
  }

  const sendFab = document.getElementById("sendFab");
  if (sendFab) sendFab.addEventListener("click", sendUserText);

  const sendBtn = document.getElementById("userSend");
  if (sendBtn) sendBtn.addEventListener("click", sendUserText);

  const ta = document.getElementById("userText");
  if (ta) {
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendUserText();
      }
    });
  }

  document.getElementById("openLogin").addEventListener("click", async () => {
    await checkAuth();
  });

  // ========== ADMIN FUNCTIONS ==========
  // Note: Client-side checks are defense-in-depth only.
  // Real security is enforced server-side.

  /**
   * Load all chats for admin user.
   */
  async function loadAdminChats() {
    if (!isAdmin) {
      return;
    }

    const res = await fetch("/api/chats");
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    chats = {};
    chatMeta = {};

    data.forEach((chat) => {
      chats[chat.chat_id] = { username: chat.username, messages: [] };
      chatMeta[chat.chat_id] = {
        oldestTs: null,
        fullyLoaded: false,
        dedupe: new Set(),
      };
    });

    // Populate chat selector
    renderChatSelector(data);
    
    // Auto-select first chat if available
    if (data.length > 0) {
      await selectChat(data[0].chat_id);
    }
  }

  /**
   * Render the chat selector list for admin.
   * @param {Array} chatList - Array of chat objects with chat_id, username, etc.
   */
  function renderChatSelector(chatList) {
    const list = document.getElementById("chatSelectorList");
    if (!list) return;

    list.innerHTML = "";

    chatList.forEach((chat) => {
      const li = document.createElement("li");
      li.className = "chat-selector-item";
      li.dataset.chatId = chat.chat_id;
      if (chat.unread) li.classList.add("unread");
      if (chat.chat_id === activeChatId) li.classList.add("active");
      
      const name = document.createElement("div");
      name.className = "chat-selector-name";
      name.textContent = chat.username;
      
      const preview = document.createElement("div");
      preview.className = "chat-selector-preview";
      preview.textContent = chat.last_message || "No messages yet";
      
      const meta = document.createElement("div");
      meta.className = "chat-selector-meta";
      if (chat.timestamp) {
        meta.textContent = chat.timestamp;
      }
      if (chat.unread) {
        const badge = document.createElement("span");
        badge.className = "unread-badge";
        badge.textContent = "●";
        meta.appendChild(badge);
      }
      
      li.appendChild(name);
      li.appendChild(preview);
      li.appendChild(meta);
      
      li.addEventListener("click", () => {
        selectChat(chat.chat_id);
        closeChatSelectorModal();
      });
      
      list.appendChild(li);
    });
    
    // Add search functionality
    const searchInput = document.getElementById("chatSearchInput");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase();
        Array.from(list.children).forEach((li) => {
          const name = li.querySelector(".chat-selector-name").textContent.toLowerCase();
          const matches = name.includes(query);
          li.style.display = matches ? "flex" : "none";
        });
      });
    }
  }

  /**
   * Select and load a chat for admin view.
   * @param {number} chatId - ID of the chat to select
   */
  async function selectChat(chatId) {
    if (!isAdmin) {
      return;
    }

    activeChatId = chatId;
    const chat = chats[chatId];
    if (!chat) return;

    await markChatRead(chatId);

    const selectedChatName = document.getElementById("selectedChatName");
    if (selectedChatName) {
      selectedChatName.textContent = chat.username;
    }

    // Highlight active chat in selector
    const selectorItems = document.querySelectorAll(".chat-selector-item");
    selectorItems.forEach((item) => {
      const itemChatId = parseInt(item.dataset.chatId);
      if (itemChatId === chatId) {
        item.classList.add("active");
        item.classList.remove("unread");
      } else {
        item.classList.remove("active");
      }
    });

    if (chat.messages.length === 0) {
      await loadOlderAdminMessages(chatId);
    } else {
      renderAdminMessages(chatId);
    }

    // Attach scroll listener if not already attached
    const container = document.getElementById("transcriptionBox");
    if (!scrollHandlerAttached && container) {
      container.addEventListener("scroll", async () => {
        if (container.scrollTop <= 40 && activeChatId) {
          await loadOlderAdminMessages(activeChatId);
        }
      });
      scrollHandlerAttached = true;
    }
  }

  /**
   * Mark a chat as read.
   * @param {number} chatId - ID of the chat to mark as read
   */
  async function markChatRead(chatId) {
    try {
      await fetch("/api/mark-read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId }),
      });
    } catch (e) {
      // Silently fail
    }
  }

  /**
   * Render messages for a specific admin chat.
   * @param {number} chatId - ID of the chat to render
   */
  function renderAdminMessages(chatId) {
    const container = document.getElementById("transcriptionBox");
    if (!container) return;

    container.innerHTML = "";
    const chat = chats[chatId];
    if (!chat) return;

    chat.messages.forEach((msg) => {
      const div = document.createElement("div");
      div.className = "message";
      div.textContent = `[${msg.timestamp}] ${msg.sender}: ${msg.text}`;
      container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
  }

  /**
   * Append a new message to an admin chat.
   * @param {number} chatId - ID of the chat
   * @param {Object} msg - Message object with sender, text, timestamp
   */
  function appendAdminMessage(chatId, msg) {
    if (!chats[chatId]) return;

    const meta = chatMeta[chatId];
    const key = `${msg.sender}|${msg.timestamp}|${msg.text}`;
    if (meta && meta.dedupe.has(key)) {
      return;
    }
    if (meta) meta.dedupe.add(key);

    chats[chatId].messages.push(msg);

    if (chatId === activeChatId) {
      renderAdminMessages(chatId);
    } else {
      // Update chat selector to show unread indicator
      const selectorItem = document.querySelector(
        `.chat-selector-item[data-chat-id="${chatId}"]`
      );
      if (selectorItem) {
        selectorItem.classList.add("unread");
        const preview = selectorItem.querySelector(".chat-selector-preview");
        if (preview) {
          preview.textContent = msg.text;
        }
        const metaEl = selectorItem.querySelector(".chat-selector-meta");
        if (metaEl && msg.timestamp) {
          const badge = metaEl.querySelector(".unread-badge");
          metaEl.innerHTML = msg.timestamp;
          if (badge) {
            metaEl.appendChild(badge);
          } else {
            const unreadBadge = document.createElement("span");
            unreadBadge.className = "unread-badge";
            unreadBadge.textContent = "●";
            metaEl.appendChild(unreadBadge);
          }
        }
      }
    }
  }

  /**
   * Load older messages for an admin chat (infinite scroll).
   * @param {number} chatId - ID of the chat to load messages for
   */
  async function loadOlderAdminMessages(chatId) {
    const meta = chatMeta[chatId];
    if (!meta || meta.fullyLoaded) return;

    const params = new URLSearchParams({ chat_id: String(chatId) });

    if (chats[chatId].messages.length > 0) {
      const oldestMsg = chats[chatId].messages[0];
      if (oldestMsg && oldestMsg.id) {
        params.set("before_id", oldestMsg.id);
      }
    }

    const container = document.getElementById("transcriptionBox");
    const res = await fetch(`/api/get-messages?${params.toString()}`);
    if (!res.ok) return;

    const batch = await res.json();
    if (!Array.isArray(batch) || batch.length === 0) {
      meta.fullyLoaded = true;
      return;
    }

    // De-duplicate messages
    const toPrepend = [];
    for (const msg of batch) {
      const key = `${msg.sender}|${msg.timestamp}|${msg.text}`;
      if (!meta.dedupe.has(key)) {
        meta.dedupe.add(key);
        toPrepend.push(msg);
      }
    }

    chats[chatId].messages = toPrepend.concat(chats[chatId].messages);

    if (toPrepend.length > 0) {
      meta.oldestTs = toPrepend[0].timestamp;
    }

    // Re-render preserving scroll position
    if (chatId === activeChatId && container) {
      const oldBottom = container.scrollHeight - container.scrollTop;
      renderAdminMessages(chatId);
      container.scrollTop = container.scrollHeight - oldBottom;
    } else {
      renderAdminMessages(chatId);
    }
  }

  /**
   * Close the chat selector modal.
   */
  function closeChatSelectorModal() {
    const chatSelectorModal = document.getElementById("chatSelectorModal");
    if (chatSelectorModal) chatSelectorModal.style.display = "none";
  }

  // Chat selector modal handlers (admin only)
  const selectChatBtn = document.getElementById("selectChatBtn");
  const chatSelectorModal = document.getElementById("chatSelectorModal");
  const closeChatSelector = document.getElementById("closeChatSelector");

  if (selectChatBtn) {
    selectChatBtn.addEventListener("click", () => {
      if (!isAdmin) {
        return;
      }
      if (chatSelectorModal) {
        chatSelectorModal.style.display = "flex";
        const searchInput = document.getElementById("chatSearchInput");
        if (searchInput) {
          searchInput.value = "";
          searchInput.focus();
        }
      }
    });
  }

  if (closeChatSelector) {
    closeChatSelector.addEventListener("click", () => {
      closeChatSelectorModal();
    });
  }

  if (chatSelectorModal) {
    chatSelectorModal.addEventListener("click", (e) => {
      if (e.target === chatSelectorModal) {
        closeChatSelectorModal();
      }
    });
  }
});
