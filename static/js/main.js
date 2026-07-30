// DROP — shared front-end behavior

document.addEventListener("DOMContentLoaded", () => {
  // Mobile sidebar toggle
  const toggle = document.querySelector(".mobile-toggle");
  const sidebar = document.querySelector(".sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", () => sidebar.classList.toggle("open"));
    document.addEventListener("click", (e) => {
      if (!sidebar.contains(e.target) && !toggle.contains(e.target)) {
        sidebar.classList.remove("open");
      }
    });
  }

  // Auto-dismiss flash messages
  document.querySelectorAll(".flash-item").forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = "opacity 0.3s ease, transform 0.3s ease";
      el.style.opacity = "0";
      el.style.transform = "translateX(20px)";
      setTimeout(() => el.remove(), 300);
    }, 4500 + i * 300);
  });

  // Theme toggle (persists via /settings form + localStorage for instant switch)
  const themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const html = document.documentElement;
      const current = html.getAttribute("data-theme") || "light";
      const next = current === "light" ? "dark" : "light";
      html.setAttribute("data-theme", next);
      localStorage.setItem("drop-theme", next);
      fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `form_type=theme&theme=${next}`,
      });
    });
  }

  // Join classroom modal helper
  document.querySelectorAll("[data-copy]").forEach((el) => {
    el.addEventListener("click", () => {
      navigator.clipboard.writeText(el.getAttribute("data-copy"));
      const original = el.textContent;
      el.textContent = "Copied!";
      setTimeout(() => (el.textContent = original), 1200);
    });
  });

  initTutorChat();
});

function initTutorChat() {
  const form = document.getElementById("tutor-form");
  if (!form) return;

  const input = document.getElementById("tutor-input");
  const windowEl = document.getElementById("chat-window");
  const sendBtn = document.getElementById("tutor-send");
  let mode = "default";

  document.querySelectorAll(".mode-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".mode-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      mode = chip.dataset.mode;
    });
  });

  function scrollToBottom() {
    windowEl.scrollTop = windowEl.scrollHeight;
  }

  function appendBubble(role, content) {
    const row = document.createElement("div");
    row.className = `chat-row ${role}`;
    const avatar =
      role === "assistant"
        ? '<div class="ai-avatar" style="width:30px;height:30px;font-size:12px;">AI</div>'
        : "";
    row.innerHTML = `${avatar}<div class="chat-bubble ${role}"></div>`;
    row.querySelector(".chat-bubble").textContent = content;
    windowEl.appendChild(row);
    scrollToBottom();
    return row.querySelector(".chat-bubble");
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    if (!message) return;

    appendBubble("user", message);
    input.value = "";
    sendBtn.disabled = true;

    const thinkingRow = document.createElement("div");
    thinkingRow.className = "chat-row assistant";
    thinkingRow.innerHTML =
      '<div class="ai-avatar thinking" style="width:30px;height:30px;font-size:12px;">AI</div><div class="chat-bubble assistant">Thinking…</div>';
    windowEl.appendChild(thinkingRow);
    scrollToBottom();

    try {
      const res = await fetch("/api/tutor/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, mode }),
      });
      const data = await res.json();
      thinkingRow.remove();
      appendBubble("assistant", data.reply || "Sorry, something went wrong.");
    } catch (err) {
      thinkingRow.remove();
      appendBubble("assistant", "Network error — please try again.");
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  });
}
