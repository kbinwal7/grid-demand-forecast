import { fetchJson, fillModelSelect, istDate } from "./api.js";
import { initPanelControls } from "./controls.js";
import { createTabLoader, TAB_IDS, tabFromHash } from "./tabs.js";

const modelSelect = document.querySelector("#global-model-select");
const dateSelect = document.querySelector("#global-date-select");
const refreshButton = document.querySelector("#dashboard-refresh");
const sourceBadge = document.querySelector("#dashboard-source-badge");
const notice = document.querySelector("#dashboard-notice");
const tabs = new Map(
  TAB_IDS.map((id) => [id, document.querySelector(`#${CSS.escape(id)}`)]),
);
const panels = new Map(
  TAB_IDS.map((id) => [id, document.querySelector(`#panel-${CSS.escape(id)}`)]),
);
let modelItems = [];
let activeTab = "forecast";

dateSelect.value = istDate();

const controls = {
  modelId: () => modelSelect.value,
  date: () => dateSelect.value,
  source: () => "",
  models: () => modelItems,
  setSource(source) {
    sourceBadge.textContent = source ? `Source: ${source}` : "Source: unavailable";
  },
};

function showNotice(message, retry) {
  notice.replaceChildren(document.createTextNode(message));
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Retry";
    button.addEventListener("click", () => void initializeModels());
    notice.append(button);
  }
  notice.hidden = false;
}

const loader = createTabLoader({
  forecast: async () => {
    const tab = await import("./forecast.js");
    await tab.renderPage(controls);
  },
  accuracy: async () => {
    const tab = await import("./accuracy.js");
    await tab.renderPage(controls);
  },
  failures: async () => {
    const tab = await import("./failures.js");
    await tab.renderPage(controls);
  },
  models: async ({ refresh }) => {
    if (refresh) {
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
    }
    const tab = await import("./models.js");
    await tab.renderPage(controls);
  },
  "data-health": async () => {
    const tab = await import("./health.js");
    await tab.renderPage(controls);
  },
});

function setActiveTab(tabId, { updateHash = false, refresh = false } = {}) {
  activeTab = tabId;
  for (const id of TAB_IDS) {
    const selected = id === tabId;
    tabs.get(id).setAttribute("aria-selected", String(selected));
    tabs.get(id).tabIndex = selected ? 0 : -1;
    panels.get(id).hidden = !selected;
  }
  if (updateHash && location.hash !== `#${tabId}`) {
    location.hash = tabId;
  }
  void loader(tabId, { refresh }).catch((error) => {
    showNotice(error.message || "The selected dashboard tab could not be loaded.", true);
  });
}

function normalizeHash() {
  const tabId = tabFromHash(location.hash);
  if (location.hash !== `#${tabId}`) {
    history.replaceState(null, "", `#${tabId}`);
  }
  setActiveTab(tabId);
}

for (const [tabId, tab] of tabs) {
  tab.addEventListener("click", () => setActiveTab(tabId, { updateHash: true }));
  tab.addEventListener("keydown", (event) => {
    const index = TAB_IDS.indexOf(tabId);
    let nextIndex;
    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % TAB_IDS.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index + TAB_IDS.length - 1) % TAB_IDS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = TAB_IDS.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const nextTabId = TAB_IDS[nextIndex];
    tabs.get(nextTabId).focus();
    setActiveTab(nextTabId, { updateHash: true });
  });
}

window.addEventListener("hashchange", normalizeHash);
initPanelControls();
refreshButton.addEventListener("click", () => {
  notice.hidden = true;
  void loader(activeTab, { refresh: true }).catch((error) => {
    showNotice(error.message || "The selected dashboard tab could not be refreshed.", true);
  });
});
for (const control of [modelSelect, dateSelect]) {
  control.addEventListener("change", () => {
    loader.invalidate();
    setActiveTab(activeTab, { refresh: true });
  });
}

async function initializeModels() {
  refreshButton.disabled = true;
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
    normalizeHash();
  } catch (error) {
    showNotice(error.message || "Model information could not be loaded.", true);
  } finally {
    refreshButton.disabled = false;
  }
}

void initializeModels();
