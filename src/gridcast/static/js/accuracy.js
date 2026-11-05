import { ApiError, queryString } from "./api.js";
import { chartLayout, drawPlot, runChart, timeLabels } from "./charts.js";
import { BASELINE_MODEL_ID } from "./thresholds.js";

const RANGE_DAYS = 30;

function dayOffset(day, days) {
  const result = new Date(`${day}T00:00:00Z`);
  result.setUTCDate(result.getUTCDate() + days);
  return result.toISOString().slice(0, 10);
}

function selectedForecastQuery(controls) {
  return queryString({
    date: controls.date(),
    model_id: controls.modelId(),
    source: controls.source(),
  });
}

function rangeQuery(controls) {
  return queryString({
    start: dayOffset(controls.date(), -(RANGE_DAYS - 1)),
    end: controls.date(),
    source: controls.source(),
  });
}

async function renderComparison(controls, chart, request) {
  const date = controls.date();
  const forecast = await request(
    `/v1/forecast${selectedForecastQuery(controls)}`,
  ).catch((error) => {
    if (error instanceof ApiError && error.status === 404) {
      return null;
    }
    throw error;
  });
  if (!forecast?.points?.length) {
    controls.setSource(null);
    return false;
  }
  controls.setSource(forecast.source);
  const actuals = await request(
    `/v1/actuals${queryString({ start: date, end: date })}`,
  );
  const actualByTimestamp = new Map(
    actuals.points.map((point) => [
      String(point.ts).replace(" ", "T").slice(0, 19),
      point.actual_mw,
    ]),
  );
  const points = forecast.points.filter((point) =>
    actualByTimestamp.has(String(point.ts).replace(" ", "T").slice(0, 19)),
  );
  if (!points.length) {
    return false;
  }
  const labels = timeLabels(points);
  const traces = [
    {
      x: labels,
      y: points.map((point) =>
        actualByTimestamp.get(String(point.ts).replace(" ", "T").slice(0, 19)),
      ),
      type: "scatter",
      mode: "lines",
      name: "Actual",
      line: { color: "#5794f2", width: 1.5 },
    },
    {
      x: labels,
      y: points.map((point) => point.yhat_mw),
      type: "scatter",
      mode: "lines",
      name: `Forecast · ${forecast.source}`,
      line: { color: "#73bf69", width: 2 },
    },
  ];
  await drawPlot(
    chart,
    traces,
    chartLayout({
      title: `${date} · ${forecast.model_id} · ${forecast.source}`,
      yTitle: "Demand (MW)",
      xTitle: "Time (IST)",
    }),
  );
  return true;
}

function rollingSevenDay(rows) {
  const sorted = [...rows].sort((a, b) => a.day.localeCompare(b.day));
  return sorted.map((row) => {
    const windowStart = dayOffset(row.day, -6);
    const window = sorted.filter(
      (item) => item.day >= windowStart && item.day <= row.day && item.mape !== null,
    );
    const count = window.reduce((sum, item) => sum + item.n, 0);
    return {
      day: row.day,
      value:
        count === 0
          ? null
          : window.reduce((sum, item) => sum + item.mape * item.n, 0) / count,
    };
  });
}

async function renderDailyMetric(controls, chart, request, field, title) {
  const { items } = await request(`/v1/accuracy/daily${rangeQuery(controls)}`);
  const sourceNames = controls.source()
    ? [controls.source()]
    : ["live", "backtest"];
  const traces = [];
  for (const source of sourceNames) {
    const modelIds = [...new Set([controls.modelId(), BASELINE_MODEL_ID].filter(Boolean))];
    for (const modelId of modelIds) {
      const rows = items.filter(
        (item) =>
          item.source === source &&
          item.model_id === modelId &&
          item[field] !== null,
      );
      if (!rows.length) {
        continue;
      }
      const values =
        field === "mape"
          ? rollingSevenDay(rows)
          : rows.map((row) => ({ day: row.day, value: row.peak_mape }));
      traces.push({
        x: values.map((item) => item.day),
        y: values.map((item) => item.value),
        type: "scatter",
        mode: field === "mape" ? "lines" : "lines+markers",
        name: `${modelId} · ${source} · ${field === "mape" ? "rolling 7-day" : "18:00–22:00"}`,
      });
    }
  }
  if (!traces.length) {
    return false;
  }
  await drawPlot(
    chart,
    traces,
    chartLayout({
      title,
      yTitle: "MAPE (%)",
      xTitle: "Date",
    }),
  );
  return true;
}

async function renderCoverage(controls, chart, request) {
  const { items } = await request(`/v1/accuracy/coverage${rangeQuery(controls)}`);
  const rows = items.filter((item) => item.total > 0);
  if (!rows.length) {
    return false;
  }
  const traces = [
    {
      x: rows.map((item) => item.source),
      y: rows.map((item) => item.share * 100),
      customdata: rows.map((item) => item.total),
      type: "bar",
      name: "q10–q90 coverage",
      marker: { color: "#b877d9" },
      hovertemplate: "%{x} · %{y:.1f}%<br>%{customdata} scored points<extra></extra>",
    },
  ];
  const layout = chartLayout({
    title: `${RANGE_DAYS}-day interval coverage by source`,
    yTitle: "Actuals inside q10–q90 (%)",
    xTitle: "Forecast source",
  });
  layout.yaxis.range = [0, 100];
  await drawPlot(chart, traces, layout);
  return true;
}

export function renderPage(controls) {
  return Promise.all([
    runChart(
      document.querySelector("#accuracy-comparison-root"),
      (chart, request) => renderComparison(controls, chart, request),
      "No actuals and forecasts overlap for this day.",
    ),
    runChart(
      document.querySelector("#accuracy-rolling-root"),
      (chart, request) =>
        renderDailyMetric(
          controls,
          chart,
          request,
          "mape",
          "Rolling seven-calendar-day MAPE",
        ),
      "No daily accuracy data is available for this window.",
    ),
    runChart(
      document.querySelector("#accuracy-peak-root"),
      (chart, request) =>
        renderDailyMetric(
          controls,
          chart,
          request,
          "peak_mape",
          "Daily peak-window MAPE",
        ),
      "No peak-window accuracy data is available for this window.",
    ),
    runChart(
      document.querySelector("#accuracy-coverage-root"),
      (chart, request) => renderCoverage(controls, chart, request),
      "No q10–q90 intervals are available for this window.",
    ),
  ]);
}
