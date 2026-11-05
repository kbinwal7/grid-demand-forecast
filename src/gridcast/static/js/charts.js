import { fetchJson } from "./api.js";

export function chartLayout({ title, yTitle, xTitle = "Local IST" } = {}) {
  const style = getComputedStyle(document.documentElement);
  const text = style.getPropertyValue("--text").trim();
  const muted = style.getPropertyValue("--muted").trim();
  const surface = style.getPropertyValue("--panel").trim();
  const border = style.getPropertyValue("--border").trim();

  return {
    title: title ? { text: title, font: { color: text, size: 15 } } : undefined,
    paper_bgcolor: surface,
    plot_bgcolor: surface,
    font: { color: text, family: "system-ui, sans-serif" },
    margin: { l: 68, r: 24, t: title ? 52 : 24, b: 62 },
    xaxis: {
      title: { text: xTitle },
      gridcolor: border,
      tickfont: { color: muted },
      automargin: true,
    },
    yaxis: {
      title: { text: yTitle },
      gridcolor: border,
      tickfont: { color: muted },
      automargin: true,
    },
    legend: { orientation: "h", y: -0.25 },
    hovermode: "x unified",
  };
}

export function chartConfig() {
  return { responsive: true, displaylogo: false };
}

export async function runChart(
  root,
  loadAndDraw,
  emptyMessage = "No data is available for this selection.",
) {
  const state = root.querySelector("[data-chart-state]");
  const chart = root.querySelector("[data-chart]");

  const showState = (message, { retry = true } = {}) => {
    state.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    state.append(text);

    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Retry";
      button.setAttribute("aria-label", "Retry loading this chart");
      button.addEventListener("click", () => void run());
      state.append(button);
    }
    state.hidden = false;
    chart.hidden = true;
  };

  const run = async () => {
    showState("Loading chart data…", { retry: false });
    try {
      const request = (path) =>
        fetchJson(path, {
          onRetry: ({ delayMs }) => {
            showState(
              `Waking the server… retrying in ${Math.ceil(delayMs / 1000)} seconds.`,
              { retry: false },
            );
          },
        });
      const hasData = await loadAndDraw(chart, request);
      if (!hasData) {
        showState(emptyMessage);
        return;
      }
      state.hidden = true;
      chart.hidden = false;
      window.Plotly.Plots.resize(chart);
    } catch (error) {
      showState(error.message || "Chart data could not be loaded.");
    }
  };

  await run();
}

export async function runPanel(root, loadPanel, emptyMessage = "No data is available.") {
  const state = root.querySelector("[data-panel-state]");
  const content = root.querySelector("[data-panel-content]");

  const showState = (message, retry = true) => {
    state.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    state.append(text);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Retry";
      button.setAttribute("aria-label", "Retry loading this panel");
      button.addEventListener("click", () => void run());
      state.append(button);
    }
    state.hidden = false;
    content.hidden = true;
  };

  const run = async () => {
    showState("Loading data…", false);
    try {
      const request = (path) =>
        fetchJson(path, {
          onRetry: ({ delayMs }) =>
            showState(
              `Waking the server… retrying in ${Math.ceil(delayMs / 1000)} seconds.`,
              false,
            ),
        });
      if (!(await loadPanel(root, request))) {
        showState(emptyMessage);
        return;
      }
      state.hidden = true;
      content.hidden = false;
    } catch (error) {
      showState(error.message || "Data could not be loaded.");
    }
  };

  await run();
}

export function drawPlot(element, traces, layout) {
  return window.Plotly.newPlot(element, traces, layout, chartConfig());
}

export function timeLabels(points) {
  return points.map((point) => String(point.ts).slice(11, 16));
}
