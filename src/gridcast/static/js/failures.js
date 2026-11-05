import { formatNumber, queryString } from "./api.js";
import { chartLayout, drawPlot, runChart, runPanel } from "./charts.js";
import { initDashboardControls } from "./controls.js";

const RANGE_DAYS = 30;

function dayOffset(day, days) {
  const result = new Date(`${day}T00:00:00Z`);
  result.setUTCDate(result.getUTCDate() + days);
  return result.toISOString().slice(0, 10);
}

function rangeQuery(controls, extra = {}) {
  return queryString({
    start: dayOffset(controls.date(), -(RANGE_DAYS - 1)),
    end: controls.date(),
    model_id: controls.modelId(),
    source: controls.source(),
    ...extra,
  });
}

async function renderHeatmap(controls, chart, request) {
  const { items } = await request(
    `/v1/accuracy/heatmap${rangeQuery(controls)}`,
  );
  if (!items.length) {
    return false;
  }
  const sources = controls.source()
    ? [controls.source()]
    : ["live", "backtest"].filter((source) =>
        items.some((item) => item.source === source),
      );
  const hours = Array.from({ length: 24 }, (_, hour) => hour);
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const traces = sources.map((source) => {
    const sourceRows = items.filter((item) => item.source === source);
    const yLabels = weekdays.map((day) => `${day} · ${source}`);
    const cells = new Map(
      sourceRows.map((item) => [`${item.dow}:${item.hour}`, item.bias_mw]),
    );
    return {
      x: hours,
      y: yLabels,
      z: weekdays.map((_, dow) =>
        hours.map((hour) => cells.get(`${dow}:${hour}`) ?? null),
      ),
      type: "heatmap",
      name: source,
      colorbar: { title: "Bias MW" },
      hovertemplate: "%{y}<br>%{x}:00 IST<br>Bias: %{z:.1f} MW<extra></extra>",
      zmid: 0,
      colorscale: [
        [0, "#5794f2"],
        [0.5, "#181b1f"],
        [1, "#f2495c"],
      ],
      showscale: true,
    };
  });
  const layout = chartLayout({
    title: `Forecast bias by hour · ${sources.join(" + ")}`,
    yTitle: "",
    xTitle: "Hour (IST)",
  });
  layout.xaxis.dtick = 1;
  layout.yaxis.autorange = "reversed";
  await drawPlot(chart, traces, layout);
  return true;
}

function appendCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
}

async function renderWorstDays(root, request, controls) {
  const { items } = await request(
    `/v1/accuracy/worst-days${rangeQuery(controls, { n: 10 })}`,
  );
  const table = root.querySelector("#worst-days-table");
  const body = root.querySelector("#worst-days-body");
  body.replaceChildren();
  for (const item of items) {
    const row = document.createElement("tr");
    appendCell(row, item.day);
    appendCell(row, item.model_id);
    appendCell(row, item.source);
    appendCell(row, String(item.n));
    appendCell(row, item.mape === null ? "n/a" : `${formatNumber(item.mape)}%`);
    appendCell(row, formatNumber(item.bias_mw));
    body.append(row);
  }
  return items.length > 0;
}

function renderPage(controls) {
  void Promise.all([
    runChart(
      document.querySelector("#failures-heatmap-root"),
      (chart, request) => renderHeatmap(controls, chart, request),
      "No joined forecast/actual values are available for this window.",
    ),
    runPanel(
      document.querySelector("#worst-days-root"),
      (root, request) => renderWorstDays(root, request, controls),
      "No scored forecast days are available for this window.",
    ),
  ]);
}

initDashboardControls(renderPage);
