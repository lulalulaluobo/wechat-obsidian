(function () {
  const THEME_STORAGE_KEY = "wechat-md-theme";

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_STORAGE_KEY) || "system";
    } catch (_) {
      return "system";
    }
  }

  function resolveTheme(theme) {
    if (theme === "light" || theme === "dark") {
      return theme;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme, persist) {
    const selected = theme || "system";
    document.documentElement.dataset.theme = resolveTheme(selected);
    document.documentElement.dataset.themeMode = selected;
    if (persist) {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, selected);
      } catch (_) {
        // ignore storage errors
      }
    }
  }

  function initThemeControls() {
    const selects = Array.from(document.querySelectorAll("[data-theme-select]"));
    const storedTheme = getStoredTheme();
    applyTheme(storedTheme, false);
    selects.forEach((select) => {
      select.value = storedTheme;
      select.addEventListener("change", (event) => {
        applyTheme(event.target.value, true);
        selects.forEach((other) => {
          if (other !== event.target) {
            other.value = event.target.value;
          }
        });
      });
    });

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const syncSystemTheme = () => {
      if (getStoredTheme() === "system") {
        applyTheme("system", false);
      }
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", syncSystemTheme);
    } else if (typeof media.addListener === "function") {
      media.addListener(syncSystemTheme);
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function prettyJson(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  function badge(label, tone) {
    return `<span class="status-badge ${escapeHtml(tone || "info")}">${escapeHtml(label || "-")}</span>`;
  }

  function summaryItems(items) {
    return items
      .map(
        (item) => `
          <div class="summary-item">
            <span>${escapeHtml(item.label)}</span>
            <div>${escapeHtml(item.value ?? "-")}</div>
          </div>
        `
      )
      .join("");
  }

  function resultPanel(options) {
    const title = options?.title || "状态";
    const tone = options?.tone || "info";
    const statusText = options?.statusText || "待处理";
    const description = options?.description ? `<p class="helper-text">${escapeHtml(options.description)}</p>` : "";
    const items = summaryItems(options?.items || []);
    return `
      <div class="result-summary">
        <div class="summary-title-row">
          <strong>${escapeHtml(title)}</strong>
          ${badge(statusText, tone)}
        </div>
        ${description}
        <div class="summary-grid">${items}</div>
      </div>
    `;
  }

  function setHtml(id, html) {
    const element = document.getElementById(id);
    if (element) {
      element.innerHTML = html;
    }
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element) {
      element.textContent = text;
    }
  }

  window.AdminUI = {
    initThemeControls,
    escapeHtml,
    prettyJson,
    badge,
    summaryItems,
    resultPanel,
    setHtml,
    setText,
  };
})();
