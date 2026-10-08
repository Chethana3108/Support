(function () {
  "use strict";

  if (window.__BIZBOT_WIDGET_INITIALIZED__) return;
  window.__BIZBOT_WIDGET_INITIALIZED__ = true;

  // Determine API Base URL
  const currentScript = document.currentScript;
  let scriptOrigin = "http://localhost:8000";
  if (currentScript && currentScript.src) {
    try { scriptOrigin = new URL(currentScript.src).origin; } catch (e) {}
  }
  const API_BASE = window.BIZBOT_API_URL || scriptOrigin;

  // Persistent session & user identity
  let sessionId = localStorage.getItem("bizbot_session_id") || "sess_" + Math.random().toString(36).substring(2, 11);
  let userId = localStorage.getItem("bizbot_user_id") || "user_" + Math.random().toString(36).substring(2, 11);
  localStorage.setItem("bizbot_session_id", sessionId);
  localStorage.setItem("bizbot_user_id", userId);

  let isListening = false;
  let recognition = null;
  let isFirstMessage = true;

  // ── INJECT ENTERPRISE STYLES ──
  const styleEl = document.createElement("style");
  styleEl.textContent = `
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap');

    #bizbot-root {
      position: fixed;
      bottom: 0;
      right: 24px;
      z-index: 2147483647;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    #bizbot-root *, #bizbot-root *::before, #bizbot-root *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    /* ── LAUNCHER PILL (When Minimized) ── */
    .bz-launcher {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: linear-gradient(135deg, #5c2d91 0%, #7c3aed 100%);
      color: #ffffff;
      border: none;
      border-radius: 999px;
      padding: 12px 22px;
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      box-shadow: 0 10px 30px rgba(92, 45, 145, 0.45);
      font-size: 14px;
      font-weight: 700;
      transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .bz-launcher:hover { transform: translateY(-2px); box-shadow: 0 14px 36px rgba(92, 45, 145, 0.65); }
    .bz-launcher.bz-hide { display: none !important; }

    /* ── MAIN WIDGET WINDOW ── */
    .bz-widget {
      width: 380px;
      height: 620px;
      max-height: calc(100vh - 24px);
      background: #ffffff;
      border-radius: 20px 20px 0 0;
      box-shadow: 0 -8px 40px rgba(15, 23, 42, 0.18), 0 0 0 1px rgba(0, 0, 0, 0.08);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      position: relative;
      animation: bzSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }

    @keyframes bzSlideUp {
      from { opacity: 0; transform: translateY(24px) scale(0.98); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }

    .bz-widget.bz-hide { display: none !important; }
    .bz-widget.bz-expanded { width: 480px; height: 740px; }

    /* ── TOP CONTROLS (EXPAND / CLOSE) ── */
    .bz-controls {
      position: absolute;
      top: 12px;
      right: 12px;
      z-index: 30;
      display: flex;
      gap: 6px;
    }
    .bz-ctrl-btn {
      width: 28px;
      height: 28px;
      background: rgba(15, 23, 42, 0.6);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.25);
      border-radius: 50%;
      color: #ffffff;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
      line-height: 1;
    }
    .bz-ctrl-btn:hover { background: rgba(15, 23, 42, 0.9); }

    /* ── TALKING AVATAR HEADER ── */
    .bz-avatar-wrap {
      position: relative;
      width: 100%;
      height: 180px;
      background: #0f172a;
      overflow: hidden;
      flex-shrink: 0;
      border-bottom: 1px solid #e2e8f0;
    }

    .bz-avatar-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: center 20%;
      display: block;
      transition: transform 0.25s ease;
    }

    /* Speaking Dynamic Expression */
    .bz-avatar-wrap.speaking .bz-avatar-img {
      animation: bzAvatarTalking 0.4s ease-in-out infinite alternate;
    }

    @keyframes bzAvatarTalking {
      0% { transform: scale(1.0) translateY(0); }
      50% { transform: scale(1.02) translateY(-2px); }
      100% { transform: scale(1.03) translateY(1px); }
    }

    .bz-avatar-grad {
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(0,0,0,0.02) 0%, rgba(0,0,0,0.4) 100%);
      pointer-events: none;
    }

    /* Speak Now Pill Button Overlay */
    .bz-speak-pill {
      position: absolute;
      bottom: 12px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(255, 255, 255, 0.96);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.8);
      color: #1e293b;
      padding: 7px 18px;
      border-radius: 999px;
      font-size: 12.5px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 7px;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
      transition: all 0.2s ease;
      z-index: 20;
    }
    .bz-speak-pill:hover { background: #ffffff; transform: translateX(-50%) scale(1.03); }

    .bz-mic-dot {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: #10b981;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ffffff;
      font-size: 9px;
    }

    .bz-speak-pill.recording {
      background: #ef4444;
      color: #ffffff;
      animation: bzPulse 1.5s infinite;
    }
    .bz-speak-pill.recording .bz-mic-dot { background: #ffffff; color: #ef4444; }

    .bz-speak-pill.speaking {
      background: #5c2d91;
      color: #ffffff;
    }
    .bz-speak-pill.speaking .bz-mic-dot { background: #10b981; color: #fff; }

    @keyframes bzPulse {
      0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
      70% { box-shadow: 0 0 0 10px rgba(239, 68, 68, 0); }
      100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
    }

    /* ── SCROLLABLE CHAT MESSAGES BODY ── */
    .bz-body {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      overflow-x: hidden;
      padding: 16px 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: #f8fafc;
      scroll-behavior: smooth;
    }

    .bz-body::-webkit-scrollbar { width: 5px; }
    .bz-body::-webkit-scrollbar-track { background: transparent; }
    .bz-body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }

    /* Plain Introductory Greeting */
    .bz-greeting {
      font-size: 13.5px;
      line-height: 1.55;
      color: #334155;
      background: #ffffff;
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid #e2e8f0;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
    }

    /* Quick Action Outlined Chips */
    .bz-chips {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 4px 0 6px;
    }
    .bz-chip {
      background: #ffffff;
      border: 1.5px solid #c7d2fe;
      color: #5c2d91;
      padding: 9px 16px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
      text-align: center;
      cursor: pointer;
      transition: all 0.18s ease;
      font-family: inherit;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
    }
    .bz-chip:hover {
      background: #f5f3ff;
      border-color: #7c3aed;
      color: #4c1d95;
      transform: translateY(-1px);
    }

    /* ── MESSAGE ROWS ── */
    .bz-msg-row {
      display: flex;
      width: 100%;
      flex-direction: column;
      margin: 4px 0;
      animation: bzFadeIn 0.22s ease-out;
    }

    @keyframes bzFadeIn {
      from { opacity: 0; transform: translateY(6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .bz-msg-row.user {
      align-items: flex-end;
    }

    .bz-msg-row.bot {
      align-items: flex-start;
    }

    /* User Message Bubble (Input) */
    .bz-user-bubble {
      background: linear-gradient(135deg, #5c2d91 0%, #7c3aed 100%);
      color: #ffffff;
      padding: 10px 16px;
      border-radius: 18px 18px 4px 18px;
      font-size: 13.5px;
      font-weight: 500;
      line-height: 1.45;
      max-width: 85%;
      word-break: break-word;
      overflow-wrap: break-word;
      box-shadow: 0 3px 10px rgba(92, 45, 145, 0.25);
    }

    /* Bot Message Bubble (Output) */
    .bz-bot-bubble {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      color: #1e293b;
      padding: 15px 16px;
      border-radius: 18px 18px 18px 4px;
      font-size: 13.5px;
      line-height: 1.6;
      max-width: 92%;
      word-break: break-word;
      overflow-wrap: break-word;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.04);
    }

    .bz-bot-bubble p { margin-bottom: 10px; }
    .bz-bot-bubble p:last-child { margin-bottom: 0; }
    .bz-bot-bubble strong { font-weight: 700; color: #0f172a; }
    .bz-bot-bubble ul { margin: 8px 0 10px 18px; padding-left: 0; }
    .bz-bot-bubble li { margin-bottom: 6px; color: #334155; line-height: 1.5; }

    /* Case Study Card (Clean Card Format) */
    .bz-case-card {
      margin-top: 10px;
      padding: 12px 14px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-left: 3.5px solid #7c3aed;
      border-radius: 10px;
      font-size: 12.5px;
      line-height: 1.5;
    }
    .bz-case-card-title {
      font-weight: 700;
      color: #0f172a;
      font-size: 13px;
      margin-bottom: 4px;
    }
    .bz-case-card-summary {
      color: #64748b;
      margin-bottom: 8px;
      line-height: 1.45;
    }
    .bz-case-card a {
      color: #5c2d91;
      text-decoration: none;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .bz-case-card a:hover { text-decoration: underline; color: #4c1d95; }

    /* ── LEAD CAPTURE & BOOKING FORMS ── */
    .bz-form-card {
      margin-top: 12px;
      padding: 14px 16px;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      width: 100%;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    }
    .bz-form-card h4 {
      font-size: 13px;
      font-weight: 700;
      color: #1e293b;
      margin-bottom: 8px;
    }
    .bz-form-badge {
      font-size: 11px;
      color: #5c2d91;
      background: #f5f3ff;
      border: 1px solid #ddd6fe;
      padding: 6px 9px;
      border-radius: 6px;
      margin-bottom: 10px;
      line-height: 1.4;
      font-weight: 500;
    }
    .bz-form-group {
      margin-bottom: 8px;
    }
    .bz-form-group label {
      display: block;
      font-size: 11px;
      font-weight: 600;
      color: #64748b;
      margin-bottom: 3px;
    }
    .bz-form-group input, .bz-form-group select {
      width: 100%;
      padding: 8px 11px;
      font-size: 13px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      outline: none;
      background: #f8fafc;
      color: #1e293b;
      font-family: inherit;
      transition: all 0.15s;
    }
    .bz-form-group input:focus, .bz-form-group select:focus {
      border-color: #5c2d91;
      background: #ffffff;
      box-shadow: 0 0 0 2px rgba(92, 45, 145, 0.15);
    }
    .bz-submit-btn {
      width: 100%;
      padding: 10px;
      background: linear-gradient(135deg, #5c2d91 0%, #7c3aed 100%);
      color: #ffffff;
      border: none;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
      transition: opacity 0.15s;
      font-family: inherit;
      box-shadow: 0 2px 6px rgba(92, 45, 145, 0.3);
    }
    .bz-submit-btn:hover { opacity: 0.92; }
    .bz-submit-btn:disabled { background: #cbd5e1; box-shadow: none; cursor: not-allowed; }

    /* ── FOOTER INPUT BAR ── */
    .bz-footer {
      padding: 12px 16px 10px;
      background: #ffffff;
      border-top: 1px solid #f1f5f9;
      flex-shrink: 0;
    }
    .bz-input-row {
      display: flex;
      align-items: center;
      border: 1px solid #cbd5e1;
      border-radius: 999px;
      padding: 4px 6px 4px 16px;
      gap: 6px;
      background: #f8fafc;
      transition: all 0.15s;
    }
    .bz-input-row:focus-within {
      border-color: #5c2d91;
      background: #ffffff;
      box-shadow: 0 0 0 3px rgba(92, 45, 145, 0.12);
    }
    .bz-input-row input {
      flex: 1;
      border: none;
      outline: none;
      font-size: 13.5px;
      color: #0f172a;
      background: transparent;
      padding: 7px 0;
      font-family: inherit;
      min-width: 0;
    }
    .bz-input-row input::placeholder { color: #94a3b8; }
    
    .bz-mic-btn {
      background: transparent;
      border: none;
      color: #64748b;
      cursor: pointer;
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      border-radius: 50%;
      transition: all 0.15s;
    }
    .bz-mic-btn:hover { color: #5c2d91; background: #f5f3ff; }
    .bz-mic-btn.active { color: #ef4444; background: #fee2e2; }
    
    .bz-send-btn {
      width: 32px;
      height: 32px;
      flex-shrink: 0;
      background: #e2e8f0;
      border: none;
      border-radius: 50%;
      color: #64748b;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all 0.15s;
    }
    .bz-input-row input:not(:placeholder-shown) ~ .bz-send-btn,
    .bz-send-btn:hover {
      background: #5c2d91;
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(92, 45, 145, 0.35);
    }

    .bz-privacy {
      font-size: 10.5px;
      color: #94a3b8;
      text-align: center;
      padding: 6px 0 2px;
      line-height: 1.4;
    }
    .bz-privacy a { color: #64748b; text-decoration: underline; }
  `;
  document.head.appendChild(styleEl);

  // ── INJECT DOM STRUCTURE ──
  const root = document.createElement("div");
  root.id = "bizbot-root";
  root.innerHTML = `
    <button class="bz-launcher bz-hide" id="bz-launcher" title="Chat with Biz Assistant">
      <span>💬</span>
      <span>Chat with Biz Assistant</span>
    </button>

    <div class="bz-widget" id="bz-widget">
      <!-- Top Action Controls -->
      <div class="bz-controls">
        <button class="bz-ctrl-btn" id="bz-new-chat" title="New Chat">🔄</button>
        <button class="bz-ctrl-btn" id="bz-clear-history" title="Clear History">🗑️</button>
        <button class="bz-ctrl-btn" id="bz-expand" title="Expand / Shrink">⤢</button>
        <button class="bz-ctrl-btn" id="bz-close" title="Close">✕</button>
      </div>

      <!-- Avatar Header -->
      <div class="bz-avatar-wrap" id="bz-avatar">
        <img src="${API_BASE}/static/avatar-idle.png" alt="Biz Assistant - AI Consultant" class="bz-avatar-img" id="bz-avatar-img">
        <div class="bz-avatar-grad"></div>

        <!-- Speak Now Pill -->
        <button class="bz-speak-pill" id="bz-speak">
          <span class="bz-mic-dot">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
          </span>
          <span id="bz-speak-label">Speak now</span>
        </button>
      </div>

      <!-- Scrollable Chat Body -->
      <div class="bz-body" id="bz-body">
        <div class="bz-greeting" id="bz-greeting">
          Hi, I'm Biz Assistant from Biztechnosys. I help enterprises accelerate digital transformation and customer engagement with Sitecore composable solutions. How can I assist you today?
        </div>

        <div class="bz-chips" id="bz-chips">
          <button class="bz-chip" id="bz-chip-sales">Book a Meeting with Sales</button>
          <button class="bz-chip" id="bz-chip-support">Support</button>
        </div>
      </div>

      <!-- Footer Input Bar -->
      <div class="bz-footer">
        <div class="bz-input-row">
          <input type="text" id="bz-input" placeholder="Ask Biz Assistant a question" autocomplete="off">
          <button class="bz-mic-btn" id="bz-mic" title="Voice Input">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/><path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>
          </button>
          <button class="bz-send-btn" id="bz-send" title="Send">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>
          </button>
        </div>
        <div class="bz-privacy">
          This chat may be recorded as described in our <a href="https://beta.biztechnosys.com/privacy-policy" target="_blank">Privacy Policy</a>.
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  // ── DOM REFERENCES ──
  const launcher = document.getElementById("bz-launcher");
  const widget = document.getElementById("bz-widget");
  const btnClose = document.getElementById("bz-close");
  const btnExpand = document.getElementById("bz-expand");
  const speakBtn = document.getElementById("bz-speak");
  const speakLabel = document.getElementById("bz-speak-label");
  const micBtn = document.getElementById("bz-mic");
  const sendBtn = document.getElementById("bz-send");
  const inputEl = document.getElementById("bz-input");
  const bodyEl = document.getElementById("bz-body");
  const chipsEl = document.getElementById("bz-chips");
  const greetingEl = document.getElementById("bz-greeting");
  const avatarEl = document.getElementById("bz-avatar");

  // ── CONTROLS ──
  launcher.addEventListener("click", () => {
    widget.classList.remove("bz-hide");
    launcher.classList.add("bz-hide");
  });
  btnClose.addEventListener("click", () => {
    widget.classList.add("bz-hide");
    launcher.classList.remove("bz-hide");
  });
  btnExpand.addEventListener("click", () => {
    widget.classList.toggle("bz-expanded");
  });

  // New Chat — fresh session ID, clear everything
  document.getElementById("bz-new-chat").addEventListener("click", () => {
    sessionId = "sess_" + Math.random().toString(36).substring(2, 11);
    localStorage.setItem("bizbot_session_id", sessionId);
    isFirstMessage = true;
    salesBookingState = { active: false, email: "", companyName: "", meetingTopic: "" };

    // Clear all messages, restore greeting & chips
    bodyEl.innerHTML = "";
    const greeting = document.createElement("div");
    greeting.className = "bz-greeting";
    greeting.id = "bz-greeting";
    greeting.textContent = "Hi, I'm Biz Assistant from Biztechnosys. I help enterprises accelerate digital transformation and customer engagement with Sitecore composable solutions. How can I assist you today?";
    bodyEl.appendChild(greeting);

    const chips = document.createElement("div");
    chips.className = "bz-chips";
    chips.id = "bz-chips";
    chips.innerHTML = `
      <button class="bz-chip" id="bz-chip-sales">Book a Meeting with Sales</button>
      <button class="bz-chip" id="bz-chip-support">Support</button>
    `;
    bodyEl.appendChild(chips);

    // Re-attach chip listeners
    document.getElementById("bz-chip-sales").addEventListener("click", () => { startSalesBookingFlow(); });
    document.getElementById("bz-chip-support").addEventListener("click", () => { askPrompt("What technical support and engineering services do you provide?"); });
  });

  // Clear History — clear chat messages, keep session
  document.getElementById("bz-clear-history").addEventListener("click", () => {
    isFirstMessage = true;
    salesBookingState = { active: false, email: "", companyName: "", meetingTopic: "" };

    bodyEl.innerHTML = "";
    const greeting = document.createElement("div");
    greeting.className = "bz-greeting";
    greeting.id = "bz-greeting";
    greeting.textContent = "Hi, I'm Biz Assistant from Biztechnosys. I help enterprises accelerate digital transformation and customer engagement with Sitecore composable solutions. How can I assist you today?";
    bodyEl.appendChild(greeting);

    const chips = document.createElement("div");
    chips.className = "bz-chips";
    chips.id = "bz-chips";
    chips.innerHTML = `
      <button class="bz-chip" id="bz-chip-sales">Book a Meeting with Sales</button>
      <button class="bz-chip" id="bz-chip-support">Support</button>
    `;
    bodyEl.appendChild(chips);

    document.getElementById("bz-chip-sales").addEventListener("click", () => { startSalesBookingFlow(); });
    document.getElementById("bz-chip-support").addEventListener("click", () => { askPrompt("What technical support and engineering services do you provide?"); });
  });

  const SALES_REP = {
    name: "Chethana",
    title: "Sales Consultant",
    fullTitle: "Chethana — Sales Consultant (Biztechnosys)"
  };

  // State for the conversational sales booking flow
  let salesBookingState = {
    active: false,
    email: "",
    companyName: "",
    meetingTopic: ""
  };

  // ── CSS for conversational booking flow ──
  const bookingFlowStyle = document.createElement("style");
  bookingFlowStyle.textContent = `
    /* Inline single-field mini form */
    .bz-inline-form {
      margin-top: 10px;
      display: flex;
      gap: 8px;
      align-items: stretch;
    }
    .bz-inline-form input,
    .bz-inline-form textarea {
      flex: 1;
      padding: 10px 14px;
      font-size: 13px;
      border: 1.5px solid #c7d2fe;
      border-radius: 10px;
      outline: none;
      background: #ffffff;
      color: #1e293b;
      font-family: inherit;
      transition: all 0.18s;
    }
    .bz-inline-form textarea {
      min-height: 60px;
      resize: vertical;
      border-radius: 10px;
    }
    .bz-inline-form input:focus,
    .bz-inline-form textarea:focus {
      border-color: #5c2d91;
      box-shadow: 0 0 0 3px rgba(92, 45, 145, 0.12);
    }
    .bz-inline-form input::placeholder,
    .bz-inline-form textarea::placeholder { color: #94a3b8; }
    .bz-inline-submit {
      padding: 10px 18px;
      background: linear-gradient(135deg, #5c2d91 0%, #7c3aed 100%);
      color: #ffffff;
      border: none;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      font-family: inherit;
      white-space: nowrap;
      transition: opacity 0.15s;
      box-shadow: 0 2px 6px rgba(92, 45, 145, 0.3);
    }
    .bz-inline-submit:hover { opacity: 0.92; }

    /* Step indicator */
    .bz-step-indicator {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 10px;
    }
    .bz-step-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #e2e8f0;
      transition: background 0.2s;
    }
    .bz-step-dot.active { background: #5c2d91; }
    .bz-step-dot.done { background: #10b981; }
    .bz-step-label {
      font-size: 11px;
      color: #94a3b8;
      font-weight: 500;
    }

    /* Meeting confirmation card */
    .bz-meeting-confirmed {
      margin-top: 12px;
      background: linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);
      border: 1.5px solid #c7d2fe;
      border-radius: 14px;
      padding: 18px 16px;
      animation: bzFadeIn 0.3s ease-out;
    }
    .bz-meeting-confirmed-header {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
    }
    .bz-meeting-rep-avatar {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: linear-gradient(135deg, #5c2d91 0%, #7c3aed 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ffffff;
      font-size: 18px;
      font-weight: 700;
      flex-shrink: 0;
    }
    .bz-meeting-rep-info {
      display: flex;
      flex-direction: column;
    }
    .bz-meeting-rep-name {
      font-size: 14px;
      font-weight: 700;
      color: #1e293b;
    }
    .bz-meeting-rep-title {
      font-size: 11.5px;
      color: #64748b;
      font-weight: 500;
    }
    .bz-meeting-details {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .bz-meeting-detail-row {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }
    .bz-meeting-detail-icon {
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #5c2d91;
      flex-shrink: 0;
    }
    .bz-meeting-detail-label {
      color: #64748b;
      font-weight: 500;
      min-width: 50px;
    }
    .bz-meeting-detail-value {
      color: #1e293b;
      font-weight: 600;
    }
    .bz-meeting-invite-badge {
      margin-top: 14px;
      padding: 8px 12px;
      background: #dcfce7;
      border: 1px solid #bbf7d0;
      border-radius: 8px;
      font-size: 12px;
      color: #166534;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    /* Email error hint */
    .bz-email-hint {
      font-size: 11px;
      color: #dc2626;
      margin-top: 4px;
      padding-left: 2px;
    }
  `;
  document.head.appendChild(bookingFlowStyle);


  function startSalesBookingFlow() {
    // Reset state
    salesBookingState = { active: true, email: "", companyName: "", meetingTopic: "" };

    if (isFirstMessage) {
      isFirstMessage = false;
      if (greetingEl) greetingEl.style.display = "none";
      if (chipsEl) chipsEl.style.display = "none";
    }

    // Show user's action
    appendUserMessage("Book a Meeting with Sales");

    // Bot asks for email
    const row = document.createElement("div");
    row.className = "bz-msg-row bot";
    row.innerHTML = `
      <div class="bz-bot-bubble">
        <div class="bz-step-indicator">
          <span class="bz-step-dot active"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-label">Step 1 of 4</span>
        </div>
        <p><strong>Absolutely. What's the best email address to use?</strong></p>
        <form class="bz-inline-form bz-email-form">
          <input type="email" placeholder="you@email.com" required autocomplete="email">
          <button type="submit" class="bz-inline-submit">→</button>
        </form>
        <div class="bz-email-hint" style="display:none;"></div>
      </div>
    `;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;

    // TTS
    tts("Absolutely. What's the best email address to use?");

    // Attach handler
    const form = row.querySelector(".bz-email-form");
    const hintEl = row.querySelector(".bz-email-hint");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const emailInput = form.querySelector("input");
      const submitBtn = form.querySelector(".bz-inline-submit");
      const email = emailInput.value.trim();
      if (!email) return;

      // Verify email actually exists
      submitBtn.disabled = true;
      submitBtn.textContent = "⏳";
      hintEl.textContent = "Verifying email...";
      hintEl.style.display = "block";
      hintEl.style.color = "#64748b";

      try {
        const res = await fetch(`${API_BASE}/api/booking/verify-email?email=${encodeURIComponent(email)}`);
        const data = await res.json();

        if (!data.exists) {
          hintEl.textContent = "This email address does not exist. Please enter a valid, existing email.";
          hintEl.style.color = "#dc2626";
          submitBtn.disabled = false;
          submitBtn.textContent = "→";
          emailInput.focus();
          tts("This email address does not exist. Please enter a valid, existing email.");
          return;
        }
      } catch (err) {
        console.warn("[BizBot] Email verification failed, allowing:", err);
      }

      // Email verified — proceed
      hintEl.style.display = "none";
      submitBtn.disabled = false;
      submitBtn.textContent = "→";

      salesBookingState.email = email;

      // Show user's email as a message
      appendUserMessage(email);
      // Remove the form
      form.closest(".bz-bot-bubble").querySelector(".bz-step-indicator")?.remove();
      form.closest(".bz-bot-bubble").querySelector(".bz-email-hint")?.remove();
      form.remove();

      // Ask for company
      askCompanyStep();
    });
  }

  function askCompanyStep() {
    const row = document.createElement("div");
    row.className = "bz-msg-row bot";
    row.innerHTML = `
      <div class="bz-bot-bubble">
        <div class="bz-step-indicator">
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot active"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-label">Step 2 of 4</span>
        </div>
        <p><strong>Perfect. What company are you with?</strong></p>
        <form class="bz-inline-form bz-company-form">
          <input type="text" placeholder="Your company name" required>
          <button type="submit" class="bz-inline-submit">→</button>
        </form>
      </div>
    `;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;
    tts("Perfect. What company are you with?");

    const form = row.querySelector(".bz-company-form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const input = form.querySelector("input");
      const company = input.value.trim();
      if (!company) return;

      salesBookingState.companyName = company;
      appendUserMessage(company);
      form.closest(".bz-bot-bubble").querySelector(".bz-step-indicator")?.remove();
      form.remove();

      // Ask for meeting topic
      askTopicStep();
    });
  }

  function askTopicStep() {
    const row = document.createElement("div");
    row.className = "bz-msg-row bot";
    row.innerHTML = `
      <div class="bz-bot-bubble">
        <div class="bz-step-indicator">
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot active"></span>
          <span class="bz-step-dot"></span>
          <span class="bz-step-label">Step 3 of 4</span>
        </div>
        <p><strong>Got it. What would you like to meet about?</strong></p>
        <form class="bz-inline-form bz-topic-form">
          <textarea placeholder="e.g., Discuss Sitecore AI capabilities, potential use cases, and business outcomes..." required></textarea>
          <button type="submit" class="bz-inline-submit">→</button>
        </form>
      </div>
    `;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;
    tts("Got it. What would you like to meet about?");

    const form = row.querySelector(".bz-topic-form");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = form.querySelector("textarea");
      const topic = input.value.trim();
      if (!topic) return;

      salesBookingState.meetingTopic = topic;
      appendUserMessage(topic);
      form.closest(".bz-bot-bubble").querySelector(".bz-step-indicator")?.remove();
      form.remove();

      // Submit lead data to backend
      try {
        await fetch(`${API_BASE}/api/sales-booking/submit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            user_id: userId,
            email: salesBookingState.email,
            company_name: salesBookingState.companyName,
            meeting_topic: salesBookingState.meetingTopic,
            lead_name: "Valued Client"
          })
        });
      } catch (err) {
        console.error("[BizBot] Sales booking submit error:", err);
      }

      // Show calendar step
      showCalendarStep();
    });
  }

  function showCalendarStep() {
    // Build a short summary of the topic (first 80 chars)
    const topicShort = salesBookingState.meetingTopic.length > 80
      ? salesBookingState.meetingTopic.substring(0, 77) + "..."
      : salesBookingState.meetingTopic;

    const row = document.createElement("div");
    row.className = "bz-msg-row bot";

    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const minDateStr = tomorrow.toISOString().split("T")[0];

    row.innerHTML = `
      <div class="bz-bot-bubble">
        <div class="bz-step-indicator">
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot done"></span>
          <span class="bz-step-dot active"></span>
          <span class="bz-step-label">Step 4 of 4</span>
        </div>
        <p>Great, let's get time on the calendar with a Biztechnosys sales consultant to cover <strong>${escapeHtml(topicShort)}</strong>. <strong>Please choose a time that works for you.</strong></p>
      </div>
      <div class="bz-form-card bz-book-wrap" style="margin-top:10px;">
        <h4>Meet with ${escapeHtml(SALES_REP.name)}</h4>
        <div style="font-size:12px; color:#64748b; margin-bottom:4px;">${escapeHtml(SALES_REP.title)} (Biztechnosys)</div>
        <div class="bz-form-badge" style="display:flex; align-items:center; gap:6px;">
          <span>⏱</span> 30 minutes · Asia/Calcutta
        </div>
        <form class="bz-book-form">
          <div class="bz-form-group">
            <label>Select Date</label>
            <input type="date" class="bz-bk-date" min="${minDateStr}" required>
          </div>
          <div class="bz-form-group">
            <label>Available Slots (IST)</label>
            <select class="bz-bk-time" required disabled>
              <option value="">-- Select Date First --</option>
            </select>
          </div>
          <div class="bz-bk-alert" style="display: none; color: #dc2626; font-size: 11px; margin-bottom: 6px;"></div>
          <button type="submit" class="bz-submit-btn bz-bk-confirm" disabled>Confirm Meeting</button>
        </form>
      </div>
    `;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;
    tts("Great, let's get time on the calendar. Please choose a time that works for you.");

    // Attach booking form handler (same logic as existing)
    attachSalesBookingFormHandler(row);
  }

  function attachSalesBookingFormHandler(container) {
    const form = container.querySelector(".bz-book-form");
    if (!form) return;

    const dateInput = form.querySelector(".bz-bk-date");
    const timeSelect = form.querySelector(".bz-bk-time");
    const confirmBtn = form.querySelector(".bz-bk-confirm");
    const alertMsg = form.querySelector(".bz-bk-alert");

    const loadSlots = async () => {
      const dateVal = dateInput.value;
      if (!dateVal) return;

      timeSelect.innerHTML = `<option value="">Loading slots...</option>`;
      timeSelect.disabled = true;
      confirmBtn.disabled = true;
      alertMsg.style.display = "none";

      try {
        const res = await fetch(`${API_BASE}/api/booking/slots?date=${encodeURIComponent(dateVal)}`);
        if (!res.ok) {
          throw new Error(`Server returned HTTP ${res.status}`);
        }
        const data = await res.json();

        if (data.slots && data.slots.length > 0) {
          let opts = `<option value="">-- Choose Time Slot --</option>`;
          data.slots.forEach((s) => {
            opts += `<option value="${s}">${s}</option>`;
          });
          timeSelect.innerHTML = opts;
          timeSelect.disabled = false;
          confirmBtn.disabled = false;
        } else {
          timeSelect.innerHTML = `<option value="">No Slots Available</option>`;
          alertMsg.textContent = data.message || "No appointments are available for this date (24-hr advance notice required).";
          alertMsg.style.display = "block";
        }
      } catch (err) {
        console.error("[Widget] Failed to load slots:", err);
        timeSelect.innerHTML = `<option value="">Failed to load slots</option>`;
        alertMsg.textContent = "Could not fetch slots. Please check your connection or choose another date.";
        alertMsg.style.display = "block";
      }
    };

    dateInput.addEventListener("change", loadSlots);
    dateInput.addEventListener("input", loadSlots);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const dateVal = dateInput.value;
      const timeVal = timeSelect.value;
      if (!dateVal || !timeVal) return;

      confirmBtn.disabled = true;
      confirmBtn.textContent = "Scheduling in Outlook...";

      const messageText = `Submit Booking: Date: ${dateVal}, Time: ${timeVal}`;
      appendUserMessage(`Requested booking for ${dateVal} at ${timeVal}`);
      form.closest(".bz-book-wrap").remove();

      try {
        const res = await fetch(`${API_BASE}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: messageText,
            session_id: sessionId,
            user_id: userId
          })
        });
        const data = await res.json();

        // Render meeting confirmation card instead of generic bot response
        renderMeetingConfirmation(dateVal, timeVal, salesBookingState.email);

        if (data.reply) tts(data.reply);
      } catch (err) {
        console.error(err);
        appendBotFallback("Sorry, there was an issue scheduling your meeting. Please try again.");
      }

      // Reset sales booking state
      salesBookingState.active = false;
    });
  }

  function renderMeetingConfirmation(dateStr, timeStr, email) {
    // Format date nicely
    const dateObj = new Date(dateStr + "T00:00:00");
    const options = { weekday: "long", year: "numeric", month: "long", day: "numeric" };
    const formattedDate = dateObj.toLocaleDateString("en-US", options);

    const row = document.createElement("div");
    row.className = "bz-msg-row bot";
    row.innerHTML = `
      <div class="bz-bot-bubble">
        <p>Meeting scheduled with <strong>${escapeHtml(SALES_REP.name)}</strong></p>
      </div>
      <div class="bz-meeting-confirmed">
        <div class="bz-meeting-confirmed-header">
          <div class="bz-meeting-rep-avatar">${SALES_REP.name.charAt(0)}</div>
          <div class="bz-meeting-rep-info">
            <span class="bz-meeting-rep-name">Meet with ${escapeHtml(SALES_REP.name)}</span>
            <span class="bz-meeting-rep-title">${escapeHtml(SALES_REP.title)} (Biztechnosys)</span>
          </div>
        </div>
        <div class="bz-meeting-details">
          <div class="bz-meeting-detail-row">
            <span class="bz-meeting-detail-icon">🕐</span>
            <span class="bz-meeting-detail-label">Time</span>
            <span class="bz-meeting-detail-value">${escapeHtml(timeStr)} IST</span>
          </div>
          <div class="bz-meeting-detail-row">
            <span class="bz-meeting-detail-icon">📅</span>
            <span class="bz-meeting-detail-label">Date</span>
            <span class="bz-meeting-detail-value">${escapeHtml(formattedDate)}</span>
          </div>
          <div class="bz-meeting-detail-row">
            <span class="bz-meeting-detail-icon">⏱</span>
            <span class="bz-meeting-detail-label">Duration</span>
            <span class="bz-meeting-detail-value">30 minutes</span>
          </div>
        </div>
        <div class="bz-meeting-invite-badge">
          <span>✅</span> Meeting invite sent to <strong>${escapeHtml(email)}</strong>
        </div>
      </div>
    `;
    bodyEl.appendChild(row);

    setTimeout(() => {
      bodyEl.scrollTop = bodyEl.scrollHeight;

      // Add a follow-up message
      const followRow = document.createElement("div");
      followRow.className = "bz-msg-row bot";
      followRow.innerHTML = `
        <div class="bz-bot-bubble">
          <p>Thanks, you're all set! You're booked to meet with <strong>${escapeHtml(SALES_REP.name)}</strong> on <strong>${escapeHtml(formattedDate)}</strong> at <strong>${escapeHtml(timeStr)} IST</strong>.</p>
          <p><strong>Do you have any other project or requirement we can help you with?</strong></p>
        </div>
      `;
      bodyEl.appendChild(followRow);
      bodyEl.scrollTop = bodyEl.scrollHeight;
      tts(`Thanks, you're all set! You're booked to meet with ${SALES_REP.name} on ${formattedDate} at ${timeStr}.`);
    }, 600);
  }


  document.getElementById("bz-chip-sales").addEventListener("click", () => {
    startSalesBookingFlow();
  });
  document.getElementById("bz-chip-support").addEventListener("click", () => {
    askPrompt("What technical support and engineering services do you provide?");
  });

  // ── SPEECH RECOGNITION ──
  function initSpeech() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) return;

    recognition = new SpeechRec();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onstart = () => {
      isListening = true;
      speakBtn.classList.add("recording");
      micBtn.classList.add("active");
      speakLabel.textContent = "Listening...";
    };
    recognition.onresult = (e) => {
      inputEl.value = e.results[0][0].transcript;
      sendMessage();
    };
    recognition.onerror = () => stopSpeech();
    recognition.onend = () => stopSpeech();
  }

  function toggleSpeech() {
    if (!recognition) {
      alert("Speech recognition is not supported in this browser. Please use Chrome or Edge.");
      return;
    }
    if (isListening) {
      recognition.stop();
      stopSpeech();
    } else {
      try { recognition.start(); } catch (e) {}
    }
  }

  function stopSpeech() {
    isListening = false;
    speakBtn.classList.remove("recording");
    micBtn.classList.remove("active");
    speakLabel.textContent = "Speak now";
  }

  speakBtn.addEventListener("click", toggleSpeech);
  micBtn.addEventListener("click", toggleSpeech);
  initSpeech();

  // ── TALKING EXPRESSION & TTS ──
  function startTalkingExpression() {
    avatarEl.classList.add("speaking");
    speakBtn.classList.add("speaking");
    speakLabel.textContent = "Speaking...";
  }

  function stopTalkingExpression() {
    avatarEl.classList.remove("speaking");
    speakBtn.classList.remove("speaking");
    speakLabel.textContent = "Speak now";
  }

  function onWidgetWordBoundary(word) {
    widgetLastWordTime = performance.now();
    const widgetVid = document.getElementById("bz-widget-avatar-video");
    if (widgetVid) {
      if (widgetIsPausedForSilence || widgetVid.paused) {
        widgetIsPausedForSilence = false;
        widgetVid.playbackRate = 0.88;
        widgetVid.play().catch(() => {});
      }
    }
  }

  function getBestIndianFemaleVoice() {
    if (!window.speechSynthesis) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    const preferred = [
      "heera", "neerja", "swara", "veena", "sangeeta", "priya", "ananya", "kavya", "aditi",
      "google हिन्दी", "google english (india)", "en-in", "aria", "jenny", "zira", "female"
    ];
    for (const p of preferred) {
      const match = voices.find(v => {
        const n = v.name.toLowerCase();
        const l = v.lang.toLowerCase().replace("_", "-");
        return n.includes(p) || (p === "en-in" && (l === "en-in" || l.startsWith("en-in")));
      });
      if (match) return match;
    }
    return voices.find(v => v.lang.toLowerCase().startsWith("en")) || voices[0] || null;
  }

  function tts(text) {
    if (!window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const clean = text
        .replace(/[*#_`~]/g, "")
        .replace(/<[^>]*>/g, "")
        .replace(/https?:\/\/[^\s]+/g, "")
        .replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu, "")
        .trim();
      if (!clean) return;

      const utterance = new SpeechSynthesisUtterance(clean);
      utterance.lang = "en-IN";
      utterance.rate = 0.98;
      utterance.pitch = 1.08;
      const voice = getBestIndianFemaleVoice();
      if (voice) utterance.voice = voice;

      utterance.onstart = () => startTalkingExpression();
      utterance.onend = () => stopTalkingExpression();
      utterance.onerror = () => stopTalkingExpression();
      utterance.onboundary = (e) => {
        if (e.name === "word") {
          const w = clean.substring(e.charIndex, e.charIndex + (e.charLength || 4)) || "";
          onWidgetWordBoundary(w);
        }
      };
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      stopTalkingExpression();
    }
  }

  // ── TEXT CLEANING ──
  function cleanBotText(text) {
    if (!text) return "";
    return text
      .replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu, "")
      .trim();
  }

  // Clean Case Study Summary to remove broken text chunks
  function cleanCaseSummary(summary) {
    if (!summary) return "";
    let s = summary.trim();
    // If starts with lower case or broken prefix like "rce, " or ", "
    s = s.replace(/^[a-z0-9]+,\s*/i, "").replace(/^[,.\s-]+/, "");
    if (s.length > 0) {
      s = s.charAt(0).toUpperCase() + s.slice(1);
    }
    return s;
  }

  // ── MESSAGING ──
  function askPrompt(promptText) {
    inputEl.value = promptText;
    sendMessage();
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;

    if (isFirstMessage) {
      isFirstMessage = false;
      if (greetingEl) greetingEl.style.display = "none";
      if (chipsEl) chipsEl.style.display = "none";
    }

    // Detect booking intent FIRST — before appending user message
    const textLower = text.toLowerCase();
    const isBookingIntent = (
      textLower.includes("book appoint") ||
      textLower.includes("book a meeting") ||
      textLower.includes("book meeting") ||
      textLower.includes("book a call") ||
      textLower.includes("book call") ||
      textLower.includes("book a demo") ||
      textLower.includes("book demo") ||
      textLower.includes("schedule appoint") ||
      textLower.includes("schedule a call") ||
      textLower.includes("schedule call") ||
      textLower.includes("schedule meeting") ||
      textLower.includes("schedule a meeting") ||
      textLower.includes("schedule demo") ||
      textLower.includes("discovery call") ||
      textLower.includes("i want to book") ||
      textLower.includes("want to book") ||
      textLower.includes("meeting with sales") ||
      (textLower.includes("book") && /appoint|meeting|call|demo|slot|session/.test(textLower))
    );
    if (isBookingIntent) {
      inputEl.value = "";
      startSalesBookingFlow();
      return;
    }

    appendUserMessage(text);
    inputEl.value = "";

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: sessionId,
          user_id: userId
        })
      });

      const data = await res.json();
      if (data.user_id) {
        userId = data.user_id;
        localStorage.setItem("bizbot_user_id", userId);
      }

      renderBotResponse(data);
      if (data.reply) tts(data.reply);
    } catch (err) {
      console.error("[BizBot] Chat error:", err);
      appendBotFallback("Sorry, I encountered an issue connecting to the assistant. Please try again.");
    }
  }

  sendBtn.addEventListener("click", sendMessage);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  function appendUserMessage(text) {
    const row = document.createElement("div");
    row.className = "bz-msg-row user";
    row.innerHTML = `<div class="bz-user-bubble">${escapeHtml(text)}</div>`;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  function appendBotFallback(text) {
    const row = document.createElement("div");
    row.className = "bz-msg-row bot";
    row.innerHTML = `<div class="bz-bot-bubble"><p>${escapeHtml(text)}</p></div>`;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  function renderBotResponse(data) {
    const row = document.createElement("div");
    row.className = "bz-msg-row bot";

    const cleanReply = cleanBotText(data.reply);
    let html = `<div class="bz-bot-bubble">${formatMarkdown(cleanReply)}</div>`;

    // Case Study Cards (Rendered cleanly below main reply)
    if (data.case_studies && data.case_studies.length > 0) {
      data.case_studies.forEach((cs) => {
        let cleanTitle = cs.title.replace(/\s*\|\s*Biztechnosys/gi, "").trim();
        let cleanSumm = cleanCaseSummary(cs.summary);
        html += `
          <div class="bz-case-card">
            <div class="bz-case-card-title">${escapeHtml(cleanTitle)}</div>
            <div class="bz-case-card-summary">${escapeHtml(cleanSumm)}</div>
            <a href="${escapeHtml(cs.url)}" target="_blank">Read Case Study →</a>
          </div>
        `;
      });
    }

    // Lead Form
    if (data.lead_form && data.lead_form.fields) {
      html += renderLeadFormHTML(data.lead_form.fields);
    }

    // Booking Form
    if (data.booking_form) {
      html += renderBookingFormHTML();
    }

    row.innerHTML = html;
    bodyEl.appendChild(row);
    bodyEl.scrollTop = bodyEl.scrollHeight;

    attachLeadFormHandler(row);
    attachBookingFormHandler(row);
  }

  function renderLeadFormHTML(fields) {
    let html = `
      <div class="bz-form-card bz-lead-wrap">
        <h4>Book an Appointment</h4>
        <form class="bz-lead-form">
    `;
    fields.forEach((f) => {
      html += `
        <div class="bz-form-group">
          <label>${escapeHtml(f.label)}</label>
          <input type="${f.input_type}" name="${f.field}" placeholder="${escapeHtml(f.placeholder)}" ${f.required ? "required" : ""}>
        </div>
      `;
    });
    html += `
          <button type="submit" class="bz-submit-btn">Book Appointment</button>
        </form>
      </div>
    `;
    return html;
  }

  function attachLeadFormHandler(container) {
    const form = container.querySelector(".bz-lead-form");
    if (!form) return;

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const formData = new FormData(form);
      let parts = [];
      formData.forEach((val, key) => {
        if (val) parts.push(`${key}: ${val}`);
      });
      const messageText = "My contact details are: " + parts.join(", ");
      appendUserMessage("Submitted contact details.");
      form.closest(".bz-lead-wrap").remove();

      try {
        const res = await fetch(`${API_BASE}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: messageText,
            session_id: sessionId,
            user_id: userId
          })
        });
        renderBotResponse(await res.json());
      } catch (err) {
        console.error(err);
      }
    });
  }

  function renderBookingFormHTML() {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const minDateStr = tomorrow.toISOString().split("T")[0];

    return `
      <div class="bz-form-card bz-book-wrap">
        <h4>Book a Discovery Call</h4>
        <div class="bz-form-badge">
          Live Outlook calendar sync (24-hr advance notice)
        </div>
        <form class="bz-book-form">
          <div class="bz-form-group">
            <label>Select Date</label>
            <input type="date" class="bz-bk-date" min="${minDateStr}" required>
          </div>
          <div class="bz-form-group">
            <label>Available Slots (IST)</label>
            <select class="bz-bk-time" required disabled>
              <option value="">-- Select Date First --</option>
            </select>
          </div>
          <div class="bz-bk-alert" style="display: none; color: #dc2626; font-size: 11px; margin-bottom: 6px;"></div>
          <button type="submit" class="bz-submit-btn bz-bk-confirm" disabled>Confirm Discovery Call</button>
        </form>
      </div>
    `;
  }

  function attachBookingFormHandler(container) {
    const form = container.querySelector(".bz-book-form");
    if (!form) return;

    const dateInput = form.querySelector(".bz-bk-date");
    const timeSelect = form.querySelector(".bz-bk-time");
    const confirmBtn = form.querySelector(".bz-bk-confirm");
    const alertMsg = form.querySelector(".bz-bk-alert");

    const loadSlots = async () => {
      const dateVal = dateInput.value;
      if (!dateVal) return;

      timeSelect.innerHTML = `<option value="">Loading slots...</option>`;
      timeSelect.disabled = true;
      confirmBtn.disabled = true;
      alertMsg.style.display = "none";

      try {
        const res = await fetch(`${API_BASE}/api/booking/slots?date=${encodeURIComponent(dateVal)}`);
        if (!res.ok) {
          throw new Error(`Server returned HTTP ${res.status}`);
        }
        const data = await res.json();

        if (data.slots && data.slots.length > 0) {
          let opts = `<option value="">-- Choose Time Slot --</option>`;
          data.slots.forEach((s) => {
            opts += `<option value="${s}">${s}</option>`;
          });
          timeSelect.innerHTML = opts;
          timeSelect.disabled = false;
          confirmBtn.disabled = false;
        } else {
          timeSelect.innerHTML = `<option value="">No Slots Available</option>`;
          alertMsg.textContent = data.message || "No appointments are available for this date (24-hr advance notice required).";
          alertMsg.style.display = "block";
        }
      } catch (err) {
        console.error("[Widget] Failed to load slots:", err);
        timeSelect.innerHTML = `<option value="">Failed to load slots</option>`;
        alertMsg.textContent = "Could not fetch slots. Please check your connection or choose another date.";
        alertMsg.style.display = "block";
      }
    };

    dateInput.addEventListener("change", loadSlots);
    dateInput.addEventListener("input", loadSlots);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const dateVal = dateInput.value;
      const timeVal = timeSelect.value;
      if (!dateVal || !timeVal) return;

      confirmBtn.disabled = true;
      confirmBtn.textContent = "Scheduling in Outlook...";

      const messageText = `Submit Booking: Date: ${dateVal}, Time: ${timeVal}`;
      appendUserMessage(`Requested booking for ${dateVal} at ${timeVal}`);
      form.closest(".bz-book-wrap").remove();

      try {
        const res = await fetch(`${API_BASE}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: messageText,
            session_id: sessionId,
            user_id: userId
          })
        });
        renderBotResponse(await res.json());
      } catch (err) {
        console.error(err);
      }
    });
  }

  // ── ROBUST MARKDOWN PARSER FOR ENTERPRISE LISTS & PARAGRAPHS ──
  function formatMarkdown(text) {
    if (!text) return "";
    let lines = text.split("\n");
    let result = [];
    let inList = false;

    lines.forEach((line) => {
      let trimmed = line.trim();
      // Detect bullet points: - item, * item, • item, or numbered 1. item
      if (trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ")) {
        if (!inList) {
          result.push("<ul>");
          inList = true;
        }
        let itemContent = trimmed.replace(/^[-*•]\s+/, "");
        itemContent = parseInline(itemContent);
        result.push(`<li>${itemContent}</li>`);
      } else {
        if (inList) {
          result.push("</ul>");
          inList = false;
        }
        if (trimmed) {
          result.push(`<p>${parseInline(trimmed)}</p>`);
        }
      }
    });

    if (inList) result.push("</ul>");
    return result.join("");
  }

  function parseInline(text) {
    return text
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>");
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
})();
