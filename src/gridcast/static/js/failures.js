import { formatNumber, queryString } from "./api.js";
import { chartLayout, drawPlot, runChart, runPanel } from "./charts.js";

const RANGE_DAYS = 30;
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

export function aggregateBiasCells(items) {
  const cells = new Map();
  for (const item of items) {
    if (
      !Number.isInteger(item.dow) ||
      item.dow < 0 ||
      item.dow >= WEEKDAYS.length ||
      !Number.isInteger(item.hour) ||
      item.hour < 0 ||
      item.hour >= HOURS.length ||
      !Number.isFinite(item.bias_mw) ||
      !Number.isFinite(item.n) ||
      item.n <= 0
    ) {
      continue;
    }
    const key = `${item.dow}:${item.hour}`;
    const cell = cells.get(key) || { weightedBias: 0, n: 0, sources: new Set() };
    cell.weightedBias += item.bias_mw * item.n;
    cell.n += item.n;
    cell.sources.add(item.source);
    cells.set(key, cell);
  }
  if (!cells.size) {
    return null;
  }
  const biasFor = (dow, hour) => {
    const cell = cells.get(`${dow}:${hour}`);
    return cell ? cell.weightedBias / cell.n : null;
  };
  const maxAbsoluteBias = Math.max(
    ...[...cells.values()].map((cell) =>
      Math.abs(cell.weightedBias / cell.n),
    ),
  );
  return {
    z: WEEKDAYS.map((_, dow) => HOURS.map((hour) => biasFor(dow, hour))),
    customdata: WEEKDAYS.map((_, dow) =>
      HOURS.map((hour) => {
        const cell = cells.get(`${dow}:${hour}`);
        return cell ? [cell.n, [...cell.sources].sort().join(", ")] : [0, ""];
      }),
    ),
    magnitude: Math.max(10, Math.ceil(maxAbsoluteBias / 10) * 10),
  };
}

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
  const aggregate = aggregateBiasCells(items);
  if (!aggregate) {
    return false;
  }
  const traces = [{
    x: HOURS,
    y: WEEKDAYS,
    z: aggregate.z,
    customdata: aggregate.customdata,
    type: "heatmap",
    colorbar: {
      title: { text: "Mean bias (MW)", side: "right" },
      tickformat: ".0f",
      ticks: "outside",
    },
    hovertemplate:
      "%{y}, %{x}:00 IST<br>Mean bias: %{z:+.1f} MW" +
      "<br>Scored intervals: %{customdata[0]}" +
      "<br>Sources: %{customdata[1]}<extra></extra>",
    zmin: -aggregate.magnitude,
    zmax: aggregate.magnitude,
    colorscale: [
      [0, "#3b75b8"],
      [0.35, "#91b8d9"],
      [0.5, "#252a30"],
      [0.65, "#f0bf7a"],
      [1, "#c95749"],
    ],
    showscale: true,
    xgap: 2,
    ygap: 2,
  }];
  const layout = chartLayout({
    title: `Mean forecast bias by weekday and hour · last ${RANGE_DAYS} days`,
    yTitle: "Day of week",
    xTitle: "Hour of day (IST)",
  });
  layout.xaxis = {
    ...layout.xaxis,
    tickmode: "array",
    tickvals: HOURS.filter((hour) => hour % 3 === 0),
    ticktext: HOURS.filter((hour) => hour % 3 === 0).map((hour) =>
      String(hour).padStart(2, "0"),
    ),
    range: [-0.5, 23.5],
  };
  layout.yaxis = { ...layout.yaxis, autorange: "reversed" };
  layout.margin = { ...layout.margin, l: 76, r: 112, t: 58, b: 62   };
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

export function renderPage(controls) {
  return Promise.all([
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
