import { initAvatar, setAvatarState } from "./avatar.js";

// Session & User ID tracking
let sessionId = localStorage.getItem("bizbot_session_id") || "sess_" + Math.random().toString(36).substring(2, 11);
let userId = localStorage.getItem("bizbot_user_id") || "user_" + Math.random().toString(36).substring(2, 11);
localStorage.setItem("bizbot_session_id", sessionId);
localStorage.setItem("bizbot_user_id", userId);

const isDevVite = window.location.port && window.location.port !== "8000";
const API_BASE = window.BIZBOT_API_URL || (isDevVite ? "" : "http://127.0.0.1:8000");

// State variables
let isFirstMessage = true;

// DOM Elements
const launcher = document.getElementById("bz-launcher");
const widget = document.getElementById("bz-widget");
const btnClose = document.getElementById("bz-close");
const btnExpand = document.getElementById("bz-expand");
const sendBtn = document.getElementById("bz-send");
const inputEl = document.getElementById("bz-input");
const bodyEl = document.getElementById("bz-body");
const chipsEl = document.getElementById("bz-chips");
const greetingEl = document.getElementById("bz-greeting");
const avatarEl = document.getElementById("bz-avatar");

// ── INITIALIZE AVATAR (STATIC IMAGE) ──
initAvatar();

// Widget Open / Close / Expand Controls
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
document.getElementById("bz-new-chat")?.addEventListener("click", () => {
  sessionId = "sess_" + Math.random().toString(36).substring(2, 11);
  localStorage.setItem("bizbot_session_id", sessionId);
  isFirstMessage = true;
  salesBookingState = { active: false, email: "", companyName: "", meetingTopic: "" };

  bodyEl.innerHTML = "";
  const greeting = document.createElement("div");
  greeting.className = "bz-greeting";
  greeting.id = "bz-greeting";
  greeting.innerHTML = "Hi, I'm <strong>Biz Assistant</strong> from Biztechnosys. I help enterprises accelerate digital transformation and customer engagement with Sitecore composable solutions. How can I assist you today?";
  bodyEl.appendChild(greeting);

  const chips = document.createElement("div");
  chips.className = "bz-chips";
  chips.id = "bz-chips";
  chips.innerHTML = `
    <button class="bz-chip" id="bz-chip-sales">Book a Meeting with Sales</button>
    <button class="bz-chip" id="bz-chip-support">Support</button>
  `;
  bodyEl.appendChild(chips);

  document.getElementById("bz-chip-sales")?.addEventListener("click", () => { startSalesBookingFlow(); });
  document.getElementById("bz-chip-support")?.addEventListener("click", () => { askPrompt("What technical support and engineering services do you provide?"); });
});

// Clear History — clear chat messages, keep session
document.getElementById("bz-clear-history")?.addEventListener("click", () => {
  isFirstMessage = true;
  salesBookingState = { active: false, email: "", companyName: "", meetingTopic: "" };

  bodyEl.innerHTML = "";
  const greeting = document.createElement("div");
  greeting.className = "bz-greeting";
  greeting.id = "bz-greeting";
  greeting.innerHTML = "Hi, I'm <strong>Biz Assistant</strong> from Biztechnosys. I help enterprises accelerate digital transformation and customer engagement with Sitecore composable solutions. How can I assist you today?";
  bodyEl.appendChild(greeting);

  const chips = document.createElement("div");
  chips.className = "bz-chips";
  chips.id = "bz-chips";
  chips.innerHTML = `
    <button class="bz-chip" id="bz-chip-sales">Book a Meeting with Sales</button>
    <button class="bz-chip" id="bz-chip-support">Support</button>
  `;
  bodyEl.appendChild(chips);

  document.getElementById("bz-chip-sales")?.addEventListener("click", () => { startSalesBookingFlow(); });
  document.getElementById("bz-chip-support")?.addEventListener("click", () => { askPrompt("What technical support and engineering services do you provide?"); });
});

// ── CONVERSATIONAL SALES BOOKING FLOW ──

const SALES_REP = {
  name: "Chethana",
  title: "Sales Consultant",
  fullTitle: "Chethana — Sales Consultant (Biztechnosys)"
};

let salesBookingState = {
  active: false,
  fullName: "",
  email: "",
  companyName: "",
  meetingTopic: ""
};

// Inject conversational booking flow styles
const bookingFlowStyleEl = document.createElement("style");
bookingFlowStyleEl.textContent = `
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

  .bz-meeting-confirmed {
    margin-top: 12px;
    background: linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);
    border: 1.5px solid #c7d2fe;
    border-radius: 14px;
    padding: 18px 16px;
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
  .bz-meeting-rep-info { display: flex; flex-direction: column; }
  .bz-meeting-rep-name { font-size: 14px; font-weight: 700; color: #1e293b; }
  .bz-meeting-rep-title { font-size: 11.5px; color: #64748b; font-weight: 500; }
  .bz-meeting-details { display: flex; flex-direction: column; gap: 8px; }
  .bz-meeting-detail-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .bz-meeting-detail-icon { width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; color: #5c2d91; flex-shrink: 0; }
  .bz-meeting-detail-label { color: #64748b; font-weight: 500; min-width: 50px; }
  .bz-meeting-detail-value { color: #1e293b; font-weight: 600; }
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
  .bz-email-hint {
    font-size: 11px;
    color: #dc2626;
    margin-top: 4px;
    padding-left: 2px;
  }
`;
document.head.appendChild(bookingFlowStyleEl);


function startSalesBookingFlow() {
  salesBookingState = { active: true, fullName: "", email: "", companyName: "", meetingTopic: "" };

  if (isFirstMessage) {
    isFirstMessage = false;
    if (greetingEl) greetingEl.style.display = "none";
    if (chipsEl) chipsEl.style.display = "none";
  }

  appendUserMessage("Book a Meeting with Sales");

  const row = document.createElement("div");
  row.className = "bz-msg-row bot";
  row.innerHTML = `
    <div class="bz-bot-bubble">
      <div class="bz-step-indicator">
        <span class="bz-step-dot active"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-label">Step 1 of 5</span>
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

  const form = row.querySelector(".bz-email-form");
  const hintEl = row.querySelector(".bz-email-hint");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const emailInput = form.querySelector("input");
    const submitBtn = form.querySelector(".bz-inline-submit");
    const email = emailInput.value.trim();
    if (!email) return;

    // Verify email — 5-second timeout so it never hangs
    submitBtn.disabled = true;
    submitBtn.textContent = "⏳";
    hintEl.textContent = "Verifying email...";
    hintEl.style.display = "block";
    hintEl.style.color = "#64748b";

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000); // 5s max
      const res = await fetch(
        `${API_BASE}/api/booking/verify-email?email=${encodeURIComponent(email)}`,
        { signal: controller.signal }
      );
      clearTimeout(timeoutId);
      const data = await res.json();

      if (!data.exists) {
        hintEl.textContent = "This email address does not exist. Please enter a valid, existing email.";
        hintEl.style.color = "#dc2626";
        submitBtn.disabled = false;
        submitBtn.textContent = "→";
        emailInput.focus();
        return;
      }
    } catch (err) {
      // Timeout or network error — allow the email and proceed
      console.warn("[App] Email verification timed out or failed, allowing:", err);
    }

    // Email verified — proceed
    hintEl.style.display = "none";
    submitBtn.disabled = false;
    submitBtn.textContent = "→";

    salesBookingState.email = email;
    appendUserMessage(email);
    form.closest(".bz-bot-bubble").querySelector(".bz-step-indicator")?.remove();
    form.closest(".bz-bot-bubble").querySelector(".bz-email-hint")?.remove();
    form.remove();
    askFullNameStep();
  });
}

function askFullNameStep() {
  const row = document.createElement("div");
  row.className = "bz-msg-row bot";
  row.innerHTML = `
    <div class="bz-bot-bubble">
      <div class="bz-step-indicator">
        <span class="bz-step-dot done"></span>
        <span class="bz-step-dot active"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-label">Step 2 of 5</span>
      </div>
      <p><strong>Great! What's your full name?</strong></p>
      <form class="bz-inline-form bz-fullname-form">
        <input type="text" placeholder="Your full name" required autocomplete="name">
        <button type="submit" class="bz-inline-submit">→</button>
      </form>
    </div>
  `;
  bodyEl.appendChild(row);
  bodyEl.scrollTop = bodyEl.scrollHeight;

  const form = row.querySelector(".bz-fullname-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = form.querySelector("input");
    const name = input.value.trim();
    if (!name) return;

    salesBookingState.fullName = name;
    appendUserMessage(name);
    form.closest(".bz-bot-bubble").querySelector(".bz-step-indicator")?.remove();
    form.remove();
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
        <span class="bz-step-dot done"></span>
        <span class="bz-step-dot active"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-label">Step 3 of 5</span>
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
        <span class="bz-step-dot done"></span>
        <span class="bz-step-dot active"></span>
        <span class="bz-step-dot"></span>
        <span class="bz-step-label">Step 4 of 5</span>
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

    try {
      await fetch(`${API_BASE}/api/sales-booking/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          user_id: userId,
          lead_name: salesBookingState.fullName,
          email: salesBookingState.email,
          company_name: salesBookingState.companyName,
          meeting_topic: salesBookingState.meetingTopic
        })
      });
    } catch (err) {
      console.error("[App] Sales booking submit error:", err);
    }

    showCalendarStep();
  });
}

function showCalendarStep() {
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
        <span class="bz-step-dot done"></span>
        <span class="bz-step-dot active"></span>
        <span class="bz-step-label">Step 5 of 5</span>
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
      console.error("[App] Failed to load slots:", err);
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
          user_id: userId,
        }),
      });
      const data = await res.json();

      renderMeetingConfirmation(dateVal, timeVal, salesBookingState.email);
    } catch (err) {
      console.error(err);
      appendBotFallback("Sorry, there was an issue scheduling your meeting. Please try again.");
    }

    salesBookingState.active = false;
  });
}

function renderMeetingConfirmation(dateStr, timeStr, email) {
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
  }, 600);
}

// Quick Prompt Chips
document.getElementById("bz-chip-sales")?.addEventListener("click", () => {
  startSalesBookingFlow();
});
document.getElementById("bz-chip-support")?.addEventListener("click", () => {
  askPrompt("What technical support and engineering services do you provide?");
});
// ── TEXT & CHAT HANDLING (SSE STREAMING) ──
function askPrompt(promptText) {
  sendMessage(promptText);
}

async function sendMessage(explicitText) {
  const text = (typeof explicitText === "string" ? explicitText : inputEl.value).trim();
  if (!text) return;

  if (isFirstMessage) {
    isFirstMessage = false;
    if (greetingEl) greetingEl.style.display = "none";
    if (chipsEl) chipsEl.style.display = "none";
  }

  // Detect booking intent FIRST — directly route connect, discovery call, meet, sales/support contact to sales booking
  const textLower = text.toLowerCase().trim();
  const isBookingIntent = (
    textLower === "connect" ||
    textLower === "connect call" ||
    textLower === "meet" ||
    textLower === "call" ||
    textLower === "discovery call" ||
    textLower === "book" ||
    textLower === "book a meeting" ||
    textLower === "book a meeting with sales" ||
    textLower === "how do i get in touch with sales/support?" ||
    textLower === "how do i get in touch with sales/support" ||
    textLower === "how do i get in touch with sales" ||
    textLower === "get in touch with sales/support" ||
    textLower === "get in touch with sales" ||
    textLower === "book a call" ||
    textLower === "book meeting" ||
    textLower === "book appointment" ||
    /\b(get\s*in\s*touch|in\s*touch\s*with|touch\s*with\s*(?:sales|support|team)|contact\s*(?:sales|support|us)|reach\s*(?:out\s*to\s*)?(?:sales|support|team))\b/i.test(textLower) ||
    /\b(connect\s*(?:a\s*)?call|want\s*to\s*connect|connect\s*with\s*(?:sales|team|us)|connect\s*me|can\s*we\s*connect)\b/i.test(textLower) ||
    /\b(discovery\s*call|discovery\s*meeting|discovery\s*session)\b/i.test(textLower) ||
    /\b(meet\s*with\s*sales|meeting\s*with\s*sales|talk\s*to\s*sales|speak\s*with\s*sales|reach\s*sales)\b/i.test(textLower) ||
    /\b(book\s*(?:a\s*)?(?:meeting|call|appoint|slot|demo|session)|schedule\s*(?:a\s*)?(?:meeting|call|appoint|slot|demo|session))\b/i.test(textLower) ||
    /\b(i\s*want\s*to\s*(?:connect|meet|call|book|schedule))\b/i.test(textLower) ||
    /\b(want\s*to\s*(?:connect|meet|call|book|schedule))\b/i.test(textLower) ||
    /\b(let'?s\s*(?:connect|meet|call|schedule))\b/i.test(textLower) ||
    /\b(book\s*appoint|schedule\s*appoint|book\s*a\s*demo|schedule\s*demo)\b/i.test(textLower)
  );
  if (isBookingIntent) {
    inputEl.value = "";
    startSalesBookingFlow();
    return;
  }

  // Append user bubble unless already added by a custom form handler
  if (!explicitText || (!explicitText.startsWith("Submit Booking:") && !explicitText.startsWith("My contact details are:"))) {
    appendUserMessage(text);
  }
  inputEl.value = "";

  setAvatarState("idle");

  // Create bot row immediately for instant streaming response
  const row = document.createElement("div");
  row.className = "bz-msg-row bot";
  const bubble = document.createElement("div");
  bubble.className = "bz-bot-bubble";
  bubble.innerHTML = '<span class="bz-streaming-cursor">▌</span>';
  row.appendChild(bubble);
  bodyEl.appendChild(row);
  bodyEl.scrollTop = bodyEl.scrollHeight;

  let accumulatedText = "";
  let metadata = null;

  try {
    const res = await fetch(`${API_BASE}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        session_id: sessionId,
        user_id: userId,
      }),
    });

    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data: ")) continue;
        const jsonStr = trimmed.slice(6).trim();
        if (jsonStr === "[DONE]") continue;

        try {
          const payload = JSON.parse(jsonStr);
          if (payload.type === "content") {
            accumulatedText += payload.content;
            bubble.innerHTML = formatMarkdown(cleanBotText(accumulatedText)) + '<span class="bz-streaming-cursor">▌</span>';
            bodyEl.scrollTop = bodyEl.scrollHeight;
          } else if (payload.type === "metadata") {
            metadata = payload;
            if (payload.user_id) {
              userId = payload.user_id;
              localStorage.setItem("bizbot_user_id", userId);
            }
          }
        } catch (e) {
          // ignore partial json
        }
      }
    }

    // Finished streaming: clean render without cursor
    bubble.innerHTML = formatMarkdown(cleanBotText(accumulatedText));

    if (metadata) {
      appendMetadataToRow(row, metadata);
    }
  } catch (err) {
    console.error("[App] Streaming chat error:", err);
    bubble.innerHTML = `<p>Sorry, I encountered an issue connecting to the AI assistant. Please try again.</p>`;
  } finally {
    setAvatarState("idle");
    setTimeout(() => {
      bodyEl.scrollTop = bodyEl.scrollHeight;
      row.scrollIntoView({ behavior: "smooth", block: "end" });
    }, 60);
  }
}

sendBtn.addEventListener("click", () => sendMessage());
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

function appendMetadataToRow(row, data) {
  // Case Studies
  if (data.case_studies && data.case_studies.length > 0) {
    data.case_studies.forEach((cs) => {
      let cleanTitle = cs.title.replace(/\s*\|\s*Biztechnosys/gi, "").trim();
      let cleanSumm = cleanCaseSummary(cs.summary);
      const card = document.createElement("div");
      card.className = "bz-case-card";
      card.innerHTML = `
        <div class="bz-case-card-title">${escapeHtml(cleanTitle)}</div>
        <div class="bz-case-card-summary">${escapeHtml(cleanSumm)}</div>
        <a href="${escapeHtml(cs.url)}" target="_blank" rel="noopener">Read Case Study →</a>
      `;
      row.appendChild(card);
    });
  }
}

// ── UTILITIES ──
function cleanBotText(text) {
  if (!text) return "";
  return text
    .replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu, "")
    .trim();
}

function cleanCaseSummary(summary) {
  if (!summary) return "";
  let s = summary.trim();
  s = s.replace(/^[a-z0-9]+,\s*/i, "").replace(/^[,.\s-]+/, "");
  if (s.length > 0) {
    s = s.charAt(0).toUpperCase() + s.slice(1);
  }
  return s;
}

function formatMarkdown(text) {
  if (!text) return "";
  let lines = text.split("\n");
  let result = [];
  let inList = false;

  lines.forEach((line) => {
    let trimmed = line.trim();
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

// Initialize on page load
window.addEventListener("DOMContentLoaded", () => {
  console.log("[App] Biztechnosys AI Assistant initialized (browser-native voice).");
});
