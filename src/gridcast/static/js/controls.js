import { fetchJson, fillModelSelect, istDate } from "./api.js";

export function initPanelControls() {
  for (const button of document.querySelectorAll("[data-panel-expand]")) {
    button.addEventListener("click", async () => {
      const panel = button.closest("[data-panel]");
      try {
        if (document.fullscreenElement === panel) {
          await document.exitFullscreen();
        } else {
          await panel.requestFullscreen();
        }
      } catch {
        const notice = document.querySelector("#dashboard-notice");
        notice.textContent = "Fullscreen is not available in this browser.";
        notice.hidden = false;
      }
    });
  }
}

export function applyTileStatus(tile, status) {
  tile.classList.remove("status-green", "status-yellow", "status-red");
  if (status !== "neutral") {
    tile.classList.add(`status-${status}`);
  }
}

export function initDashboardControls(onRefresh) {
  const modelSelect = document.querySelector("#global-model-select");
  const dateSelect = document.querySelector("#global-date-select");
  const sourceSelect = document.querySelector("#global-source-select");
  const refreshButton = document.querySelector("#dashboard-refresh");
  const autoRefresh = document.querySelector("#auto-refresh");
  const notice = document.querySelector("#dashboard-notice");
  const sourceBadge = document.querySelector("#dashboard-source-badge");
  let intervalId;
  let modelItems = [];

  dateSelect.value = istDate();

  const controls = {
    modelId: () => modelSelect.value,
    date: () => dateSelect.value,
    source: () => sourceSelect.value,
    models: () => modelItems,
    setSource(source) {
      if (source === "live" || source === "backtest") {
        sourceBadge.textContent = `Source: ${source}`;
      } else {
        sourceBadge.textContent = "Source: unavailable";
      }
    },
    refresh: () => void refresh(),
  };

  const showNotice = (message, retry) => {
    notice.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    notice.append(text);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Retry";
      button.addEventListener("click", () => void refresh());
      notice.append(button);
    }
    notice.hidden = false;
  };

  async function refresh() {
    refreshButton.disabled = true;
    const originalText = refreshButton.textContent;
    refreshButton.textContent = "Refreshing…";
    try {
      const response = await fetchJson("/v1/models", {
        onRetry: ({ delayMs }) =>
          showNotice(
            `Waking the server… retrying in ${Math.ceil(delayMs / 1000)} seconds.`,
            false,
          ),
      });
      modelItems = response.items;
      fillModelSelect(modelSelect, modelItems);
      notice.hidden = true;
      sourceBadge.textContent = sourceSelect.value
        ? `Source: ${sourceSelect.value}`
        : "Source: live + backtest";
      await onRefresh(controls);
    } catch (error) {
      showNotice(error.message || "The dashboard could not be refreshed.", true);
      sourceBadge.textContent = sourceSelect.value
        ? `Source: ${sourceSelect.value}`
        : "Source: live + backtest";
      await onRefresh(controls);
    } finally {
      refreshButton.disabled = false;
      refreshButton.textContent = originalText;
    }
  }

  refreshButton.addEventListener("click", () => void refresh());
  for (const input of [modelSelect, dateSelect, sourceSelect]) {
    input.addEventListener("change", () => void refresh());
  }
  autoRefresh.addEventListener("change", () => {
    if (intervalId) {
      window.clearInterval(intervalId);
      intervalId = undefined;
    }
    if (autoRefresh.checked) {
      intervalId = window.setInterval(() => void refresh(), 5 * 60 * 1000);
    }
  });

  initPanelControls();
  void refresh();
  return controls;
}
