// One-page dashboard: filters -> KPI tiles + three charts. All data comes from /v1.
import { ApiError, fmt, getJson } from "./api.js";

const $ = (id) => document.getElementById(id);
const modelSelect = $("model");
const dateInput = $("date");
const STATUS_LABEL = { green: "On target", yellow: "Watch", red: "Off target", neutral: "" };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

let last = null; // last fetched payloads, so a theme change can redraw without refetching

// ------------------------------------------------------------------ helpers
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function showNotice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function baseLayout({ yTitle, xType } = {}) {
  const axis = {
    gridcolor: css("--grid"),
    linecolor: css("--axis"),
    zeroline: false,
    tickfont: { color: css("--muted"), size: 11 },
    automargin: true,
  };
  return {
    paper_bgcolor: css("--surface"),
    plot_bgcolor: css("--surface"),
    font: { family: "system-ui, -apple-system, Segoe UI, sans-serif", color: css("--ink-2"), size: 12 },
    margin: { l: 8, r: 8, t: 8, b: 8 },
    xaxis: { ...axis, type: xType, showgrid: false, showline: true },
    yaxis: { ...axis, title: { text: yTitle, font: { size: 11 } } },
    legend: { orientation: "h", x: 0, y: 1.12, font: { color: css("--ink-2") } },
    hoverlabel: { bgcolor: css("--surface"), bordercolor: css("--border"), font: { color: css("--ink") } },
    hovermode: "x unified",
  };
}

function draw(id, traces, layout) {
  $(id).querySelector(".empty")?.remove();
  window.Plotly.react($(id), traces, layout, { responsive: true, displaylogo: false, displayModeBar: false });
}

function empty(id, message) {
  window.Plotly.purge($(id));
  $(id).innerHTML = `<div class="empty">${message}</div>`;
}

function setKpi(key, kpi, digits = 1) {
  $(`kpi-${key}`).textContent = fmt(kpi?.value, digits);
  $(`kpi-${key}-note`).textContent = kpi?.note || "";
  const status = $(`status-${key}`);
  if (status) {
    status.textContent = STATUS_LABEL[kpi?.status || "neutral"];
    $(`tile-${key}`).dataset.status = kpi?.status || "neutral";
  }
}

// ------------------------------------------------------------------ renderers
function renderSummary(s) {
  setKpi("peak", s.peak_mw, 0);
  $("kpi-peak-note").textContent = s.peak_time ? `at ${s.peak_time} IST · ${s.peak_mw.note}` : s.peak_mw.note;
  setKpi("temp", s.max_temp_c);
  setKpi("mape", s.mape_7d, 2);
  setKpi("coverage", s.coverage_7d);
  setKpi("lag", s.data_lag_days, 0);
  $("source").textContent = s.source ? `source: ${s.source}` : "no forecast";
}

function renderDay(day) {
  if (!day) return empty("chart-day", "No forecast for this day.");
  const x = day.points.map((p) => p.ts);
  const traces = [];
  if (day.points.some((p) => p.q10_mw !== null)) {
    traces.push(
      { x, y: day.points.map((p) => p.q10_mw), mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      {
        x, y: day.points.map((p) => p.q90_mw), mode: "lines", line: { width: 0 }, fill: "tonexty",
        fillcolor: css("--band"), name: "q10–q90 interval", hoverinfo: "skip",
      },
    );
  }
  traces.push({
    x, y: day.points.map((p) => p.yhat_mw), mode: "lines", name: `Forecast (${day.source})`,
    line: { color: css("--series-1"), width: 2 }, hovertemplate: "%{y:,.0f} MW",
  });
  if (day.points.some((p) => p.actual_mw !== null)) {
    traces.push({
      x, y: day.points.map((p) => p.actual_mw), mode: "lines", name: "Actual",
      line: { color: css("--series-2"), width: 2 }, hovertemplate: "%{y:,.0f} MW",
    });
  }
  const layout = baseLayout({ yTitle: "Demand (MW)", xType: "date" });
  layout.xaxis.tickformat = "%H:%M";
  layout.xaxis.hoverformat = "%H:%M IST";
  draw("chart-day", traces, layout);
}

function renderDaily(daily) {
  if (!daily?.items.length) return empty("chart-daily", "No scored forecasts in this window yet.");
  const x = daily.items.map((d) => d.day);
  const layout = baseLayout({ yTitle: "MAPE (%)", xType: "date" });
  layout.xaxis.hoverformat = "%a %d %b";
  layout.bargap = 0.35;
  draw("chart-daily", [
    {
      type: "bar", x, y: daily.items.map((d) => d.mape), name: "Daily MAPE",
      marker: { color: css("--series-1"), cornerradius: 4 }, hovertemplate: "%{y:.2f}%",
    },
    {
      x, y: daily.items.map((d) => d.peak_mape), mode: "lines+markers", name: "Peak 18–22h MAPE",
      line: { color: css("--series-2"), width: 2 }, marker: { size: 8, line: { color: css("--surface"), width: 2 } },
      hovertemplate: "%{y:.2f}%",
    },
  ], layout);
}

function renderBias(heat) {
  if (!heat?.items.length) return empty("chart-bias", "No scored forecasts in this window yet.");
  const z = DAYS.map(() => Array(24).fill(null));
  const n = DAYS.map(() => Array(24).fill(0));
  for (const c of heat.items) {
    z[c.dow][c.hour] = c.bias_mw;
    n[c.dow][c.hour] = c.n;
  }
  const limit = Math.max(1, ...heat.items.map((c) => Math.abs(c.bias_mw ?? 0)));
  const layout = baseLayout({});
  layout.hovermode = "closest";
  layout.yaxis.autorange = "reversed";
  layout.yaxis.showgrid = false;
  layout.xaxis.title = { text: "Hour of day (IST)", font: { size: 11 } };
  layout.xaxis.dtick = 3;
  draw("chart-bias", [{
    type: "heatmap", z, customdata: n, x: [...Array(24).keys()], y: DAYS,
    zmin: -limit, zmax: limit, zmid: 0, xgap: 2, ygap: 2,
    colorscale: [[0, css("--div-neg")], [0.5, css("--div-mid")], [1, css("--div-pos")]],
    colorbar: { thickness: 10, outlinewidth: 0, tickfont: { color: css("--muted"), size: 10 } },
    hovertemplate: "%{y} %{x}:00<br>bias %{z:+,.0f} MW<br>%{customdata} intervals<extra></extra>",
  }], layout);
}

function renderAll() {
  if (!last) return;
  renderSummary(last.summary);
  renderDay(last.day);
  renderDaily(last.daily);
  renderBias(last.heat);
}

// ------------------------------------------------------------------ data flow
async function refresh() {
  showNotice("");
  const params = { model_id: modelSelect.value, date: dateInput.value };
  try {
    const summary = await getJson("/v1/summary", params);
    dateInput.value = summary.date; // backend default = model's latest forecast day
    params.date = summary.date;
    params.model_id = summary.model_id;
    const orNull = (p) => p.catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e)));
    const [day, daily, heat] = await Promise.all([
      orNull(getJson("/v1/day", params)),
      getJson("/v1/accuracy/daily", params),
      getJson("/v1/accuracy/heatmap", params),
    ]);
    last = { summary, day, daily, heat };
    renderAll();
  } catch (error) {
    showNotice(error.message);
  }
}

async function init() {
  try {
    const { items } = await getJson("/v1/models");
    if (!items.length) throw new Error("No models registered yet. Run: python scripts/seed_demo.py");
    modelSelect.replaceChildren(
      ...items.map((m) => new Option(`${m.model_id}${m.is_champion ? " ★ champion" : ""}`, m.model_id, false, m.is_champion)),
    );
  } catch (error) {
    showNotice(error.message);
    return;
  }
  modelSelect.addEventListener("change", () => {
    dateInput.value = ""; // jump to the new model's latest forecast day
    refresh();
  });
  dateInput.addEventListener("change", refresh);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
  await refresh();
}

window.addEventListener("load", init); // after the deferred Plotly bundle has loaded
